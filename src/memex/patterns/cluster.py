"""跨仓聚类（tech-design / decision G10、G14）。

对象：**可复用卡片描述向量**（跨语言前提——向量建立在语言无关的 mechanism_desc 上）。
只对 reusable=1 的卡片聚类；只有跨源仓（>=2 个不同 source_group）的簇才留。

source_group = COALESCE(fork_of, identity_key)：fork 不计作独立来源，避免 fork 污染
min_repos 语义（G10）。

产出：patterns / pattern_members / pattern_intents，并 upsert pattern 块
（key='pat:<key>'，text = 成员 mechanism_desc 用 '; ' 拼接并按 unit 截断到 60）。

算法：并查集 + 贪心相似度合并（V1 规模足够；V4 再做质量实验）。阈值默认 0.75。
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from ..core import Config, count_units
from ..embeddings import Embedder, cosine, unpack_vector

DEFAULT_SIM_THRESHOLD = 0.75
MIN_REPOS = 2  # 语义过滤器，不可调参（solution-analysis）

__all__ = ["cluster_cards", "recluster", "pattern_chunk_text", "DEFAULT_SIM_THRESHOLD", "MIN_REPOS"]


def _source_group(conn: sqlite3.Connection, repo_id: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(fork_of, identity_key) AS g FROM repos WHERE repo_id = ?", (repo_id,)
    ).fetchone()
    return row["g"] if row else repo_id


def _load_cards(conn: sqlite3.Connection, embedder: Embedder) -> list[dict[str, Any]]:
    """载入可复用卡片 + 其向量 + 所属 repo（经 feature -> analysis -> repo 回溯）。"""
    rows = conn.execute(
        "SELECT card_id, feature_id, title, mechanism_desc FROM cards WHERE reusable = 1"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for c in rows:
        cid = c["card_id"]
        vrow = conn.execute(
            "SELECT vec FROM chunk_vectors WHERE chunk_id = ?", (f"card~{cid}",)
        ).fetchone()
        if vrow is None:
            continue
        arow = conn.execute(
            "SELECT a.repo_id FROM cards c JOIN features f ON c.feature_id = f.feature_id "
            "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE c.card_id = ?",
            (cid,),
        ).fetchone()
        if arow is None:
            continue
        out.append({
            "card_id": cid,
            "feature_id": c["feature_id"],
            "repo_id": arow["repo_id"],
            "title": c["title"],
            "mechanism_desc": c["mechanism_desc"] or "",
            "vec": unpack_vector(vrow["vec"]),
        })
    return out


def _tags(conn: sqlite3.Connection, card_ids: list[str]) -> list[str]:
    """聚合成员卡片的 tags。卡片 tags 存于 report_json（cards 表无 tags 列）。"""
    seen: list[str] = []
    for cid in card_ids:
        for t in _card_tags(conn, cid):
            if t not in seen:
                seen.append(t)
    return seen


def _card_tags(conn: sqlite3.Connection, card_id: str) -> list[str]:
    from ..store.db import json_loads

    row = conn.execute(
        "SELECT f.slug AS slug, a.report_json AS report_json, c.title AS title "
        "FROM cards c JOIN features f ON c.feature_id = f.feature_id "
        "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE c.card_id = ?",
        (card_id,),
    ).fetchone()
    if not row:
        return []
    report = json_loads(row["report_json"], {}) or {}
    for feat in report.get("features", []):
        if feat.get("key") != row["slug"]:
            continue
        for card in feat.get("cards", []):
            if card.get("title") == row["title"]:
                tags = card.get("tags") or []
                return [str(t) for t in tags] if isinstance(tags, list) else []
    return []


def pattern_chunk_text(members: list[dict[str, Any]], *, max_units: int) -> str:
    """pattern 块正文：成员 mechanism_desc 用 '; ' 拼接，按 unit 截断。"""
    parts: list[str] = []
    total = 0
    for m in members:
        mech = (m.get("mechanism_desc") or "").strip()
        if not mech:
            continue
        add = count_units(mech)
        if total + add > max_units and parts:
            break
        parts.append(mech)
        total += add
    return "; ".join(parts)


def cluster_cards(
    cards: list[dict[str, Any]], *, threshold: float = DEFAULT_SIM_THRESHOLD
) -> list[list[dict[str, Any]]]:
    """并查集贪心聚类：两卡相似度 >= threshold 则合并。返回簇列表。"""
    n = len(cards)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if cosine(cards[i]["vec"], cards[j]["vec"]) >= threshold:
                union(i, j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, card in enumerate(cards):
        groups.setdefault(find(i), []).append(card)
    return list(groups.values())


def _keep(cluster: list[dict[str, Any]], groups_by_repo: dict[str, str]) -> bool:
    return len({groups_by_repo.get(c["repo_id"], c["repo_id"]) for c in cluster}) >= MIN_REPOS


def recluster(conn: sqlite3.Connection, cfg: Config, *, threshold: float = DEFAULT_SIM_THRESHOLD) -> dict[str, Any]:
    """重算全部模式簇。清空 patterns 派生层后重建。返回统计。"""
    from ..store.db import json_dumps
    from ..store.index import chunk_id_for, put_chunks

    cards = _load_cards(conn, _embedder_for(conn))
    groups_by_repo = {r["repo_id"]: _source_group(conn, r["repo_id"]) for r in conn.execute("SELECT repo_id FROM repos").fetchall()}

    clusters = [c for c in cluster_cards(cards, threshold=threshold) if _keep(c, groups_by_repo)]

    # 清空派生层（可重算）：先清 pattern 块，再清派生表
    for r in conn.execute("SELECT chunk_id FROM chunks WHERE kind = 'pattern'").fetchall():
        cid = r["chunk_id"]
        conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (cid,))
        conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (cid,))
    conn.execute("DELETE FROM chunks WHERE kind = 'pattern'")
    conn.execute("DELETE FROM pattern_members")
    conn.execute("DELETE FROM pattern_intents")
    conn.execute("DELETE FROM patterns")

    patterns_written = 0
    chunk_rows: list[dict[str, Any]] = []
    for cluster in clusters:
        card_ids = sorted(c["card_id"] for c in cluster)
        key = "cluster-" + hashlib.sha1("|".join(card_ids).encode("utf-8")).hexdigest()[:10]
        pattern_id = "pat_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        tags = _tags(conn, card_ids)
        title = _pattern_title(cluster)
        repo_ids = {c["repo_id"] for c in cluster}
        conn.execute(
            "INSERT INTO patterns(pattern_id, key, title, tags_json, card_count, repo_count) VALUES(?,?,?,?,?,?)",
            (pattern_id, key, title, json_dumps(tags), len(card_ids), len(repo_ids)),
        )
        for c in cluster:
            conn.execute(
                "INSERT OR REPLACE INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
                (pattern_id, c["card_id"], 1.0),
            )
        for intent in _cluster_intents(conn, card_ids):
            conn.execute("INSERT INTO pattern_intents(pattern_id, text) VALUES(?,?)", (pattern_id, intent))
        text = pattern_chunk_text(cluster, max_units=cfg.pattern_chunk_max_units)
        if text:
            chunk_rows.append({
                "chunk_id": chunk_id_for("pattern", key),
                "kind": "pattern",
                "ref_id": pattern_id,
                "card_id": None,
                "text": text,
                "repo_id": None,
                "language": None,
                "heading": title,
            })
        patterns_written += 1

    if chunk_rows:
        put_chunks(conn, _embedder_for(conn), chunk_rows)
    return {
        "patterns": patterns_written,
        "clusters_seen": len(cluster_cards(cards, threshold=threshold)),
        "cards_considered": len(cards),
        "chunks": len(chunk_rows),
        "threshold": threshold,
    }


def _embedder_for(conn: sqlite3.Connection) -> Embedder:
    from ..embeddings import get_embedder
    from ..store.db import get_meta

    spec = get_meta(conn, "embedder")
    return get_embedder(spec)


def _pattern_title(cluster: list[dict[str, Any]]) -> str:
    """模式标题：取成员卡片标题里最短的一条（通常是概括性最强的那条）。"""
    titles = [str(c["title"]) for c in cluster if c.get("title")]
    if not titles:
        return "跨仓复用模式"
    return min(titles, key=len)


def _cluster_intents(conn: sqlite3.Connection, card_ids: list[str]) -> list[str]:
    """汇总成员所属 feature 的 intent（英文，存于 report_json），去重后返回。"""
    from ..store.db import json_loads

    out: list[str] = []
    seen: set[str] = set()
    for cid in card_ids:
        row = conn.execute(
            "SELECT a.report_json, f.slug FROM cards c JOIN features f ON c.feature_id = f.feature_id "
            "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE c.card_id = ?",
            (cid,),
        ).fetchone()
        if not row:
            continue
        report = json_loads(row["report_json"], {}) or {}
        slug = row["slug"]
        for feat in report.get("features", []):
            if feat.get("key") == slug and feat.get("intent") and feat["intent"] not in seen:
                seen.add(feat["intent"])
                out.append(feat["intent"])
    return out
