"""索引写路径：把一次分析物化成语义块 + 向量 + 全文索引。

重建性（tech-design §2.3）：索引层（chunks / chunk_vectors / chunk_fts）可整层
重算——它不进备份，只由真源（repos/analyses/features/cards/evidence）派生。
因此这里是**唯一**写索引的地方，commit 成功后调用一次；reindex 也走这里。

两种粒度：
- feature 块：中文（标题 + 摘要 + 五轴摘要），供中文关键词命中；
- card 块：以卡片的 mechanism_desc（强制英文）为主文本，向量层索引
  「语言无关的机制描述」，这是跨语言召回的前提（AGENTS.md 第二支柱）。

pattern 块与 report_section 块由 patterns/ 与 commit 路径分别追加。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ..constants import KIND_PRIOR
from ..embeddings import Embedder, pack_vector
from ..store.db import json_loads

_ORD_SEP = "~"


def chunk_id_for(kind: str, ref_id: str, ordinal: int = 0) -> str:
    """稳定 chunk_id：同 (kind, ref_id, ordinal) 永远得到同一 id，便于幂等重写。"""
    return f"{kind}{_ORD_SEP}{ref_id}{_ORD_SEP}{ordinal}" if ordinal else f"{kind}{_ORD_SEP}{ref_id}"


def _axis_summary(principles: dict[str, Any]) -> str:
    """把 5 轴压缩成一行中文摘要（用于 feature 块正文）。"""
    labels = {
        "runtime_control_flow": "运行/控制流",
        "data_flow": "数据流",
        "state_lifecycle": "状态与生命周期",
        "failure_recovery": "失败恢复",
        "concurrency_timing": "并发与时机",
    }
    parts: list[str] = []
    for key in ("runtime_control_flow", "data_flow", "state_lifecycle", "failure_recovery", "concurrency_timing"):
        val = principles.get(key)
        if isinstance(val, dict):
            val = val.get("detail") or val.get("summary") or ""
        if val:
            parts.append(f"{labels[key]}：{val}")
    return " ".join(parts)


def build_feature_text(feature: dict[str, Any]) -> str:
    principles = feature.get("principles") or {}
    if isinstance(principles, str):
        principles = json_loads(principles, {}) or {}
    return "。".join(x for x in [
        feature.get("title") or "",
        feature.get("summary") or "",
        _axis_summary(principles),
    ] if x).strip()


def build_card_text(card: dict[str, Any]) -> str:
    """卡片正文：mechanism_desc 为主干（英文、语言无关），后缀补「怎么找到它」。

    tech-design §2.2「块文本的组成」：源码路径、符号名、tag 一律进块文本，
    否则 FTS5 命中不到卡片，只能命中把整段 Markdown 塞进索引的 report_section 块，
    检索就返回目录噪声而不是解释代码的那张卡（Doraemon 验收实测）。
    """
    parts: list[str] = []
    mech = (card.get("mechanism_desc") or "").strip()
    if mech:
        parts.append(mech)
    for key in ("summary", "title"):
        val = (card.get(key) or "").strip()
        if val:
            parts.append(val)
    tags = [str(t).strip() for t in (card.get("tags") or []) if str(t).strip()]
    if tags:
        parts.append(" ".join(tags))
    # 证据：源码路径 + 符号名，是工程师最常敲的字面量
    for ev in card.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        path = str(ev.get("path") or "").strip()
        if not path:
            continue
        symbol = str(ev.get("symbol") or "").strip()
        parts.append(f"{path} {symbol}".strip())
    if card.get("symbol"):
        parts.append(str(card["symbol"]).strip())
    return " ".join(parts).strip()


def _is_pending(quality_json: Any) -> bool:
    """这张卡是不是「机制描述待补英文」状态（P1-3 / G12）。

    标记来自 import-vibecraft 写卡时打的 quality_json.pending。
    读不出、不是布尔、没这个键——一律当**非** pending：闸门只拦「明确标了待补」的卡，
    宁可放行也不把正常卡片静默剔出索引（那是另一种假信号）。
    """
    q = json_loads(quality_json, {}) or {}
    if not isinstance(q, dict):
        return False
    return bool(q.get("pending"))


def _card_evidence(conn: sqlite3.Connection, card_id: str) -> list[dict[str, Any]]:
    """取卡片的证据（源码路径 + 符号名），供块文本进 FTS（tech-design §2.2）。"""
    rows = conn.execute(
        "SELECT path, symbol FROM evidence WHERE card_id = ? ORDER BY evidence_id",
        (card_id,),
    ).fetchall()
    return [{"path": r["path"], "symbol": r["symbol"]} for r in rows]


def _write_fts(conn: sqlite3.Connection, chunk_id: str, text: str) -> None:
    conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?, ?)", (chunk_id, text))


def _drop_chunks(conn: sqlite3.Connection, chunk_ids: list[str]) -> None:
    for cid in chunk_ids:
        conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (cid,))
        conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (cid,))
        conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (cid,))


def drop_chunks_by_ref(conn: sqlite3.Connection, kind: str, ref_ids: Iterable[str]) -> int:
    """按 (kind, ref_id) 前缀删除该 ref 的所有块（含 ordinal 变体）。"""
    n = 0
    for rid in ref_ids:
        prefix = f"{kind}{_ORD_SEP}{rid}"
        rows = conn.execute(
            "SELECT chunk_id FROM chunks WHERE chunk_id = ? OR chunk_id LIKE ?",
            (prefix, prefix + _ORD_SEP + "%"),
        ).fetchall()
        ids = [r["chunk_id"] for r in rows]
        _drop_chunks(conn, ids)
        n += len(ids)
    return n


def put_chunks(conn: sqlite3.Connection, embedder: Embedder, rows: list[dict[str, Any]]) -> int:
    """写入一批块：先删同名旧块（幂等），再落 chunks + 向量 + FTS。返回写入数。"""
    if not rows:
        return 0
    _drop_chunks(conn, [r["chunk_id"] for r in rows])
    texts = [r["text"] for r in rows]
    vectors = embedder.embed(texts)
    dim = embedder.dim
    for row, vec in zip(rows, vectors):
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading, producer) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                row["chunk_id"],
                row["kind"],
                row.get("ref_id"),
                row.get("card_id"),
                row["text"],
                row.get("repo_id"),
                row.get("language"),
                row.get("heading"),
                row.get("producer"),
            ),
        )
        conn.execute(
            "INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
            (row["chunk_id"], embedder.model, dim, pack_vector(vec)),
        )
        _write_fts(conn, row["chunk_id"], row["text"])
    return len(rows)


def index_analysis(
    conn: sqlite3.Connection,
    embedder: Embedder,
    analysis_id: str,
    *,
    report_md: str | None = None,
) -> dict[str, int]:
    """为一次分析建立 feature/card/report_section 块。幂等（先删后建）。"""
    feats = conn.execute(
        "SELECT feature_id, slug, title, summary, position FROM features WHERE analysis_id = ? ORDER BY position",
        (analysis_id,),
    ).fetchall()
    rows: list[dict[str, Any]] = []
    ref_ids: list[str] = []
    n_cards = 0
    n_pending = 0
    # tags 存在 report_json 里（cards 表无 tags 列），按 feature slug + 卡片标题定位
    # G21 补记：producer 在**建块这一刻**定死，不做运行时 join analyses——检索每行本来
    # 就带着 ref_id，运行时 join 要多查一次，还会撞上「ref 已删」的孤儿块（见 db.py 的
    # _drop_repo_chunks 第 2 条路）。analyses.producer 是 NOT NULL，读不到就留 None 不猜。
    arow = conn.execute(
        "SELECT report_json, producer FROM analyses WHERE analysis_id = ?", (analysis_id,)
    ).fetchone()
    report = json_loads(arow, {}) or {}
    producer = arow["producer"] if arow is not None else None
    tags_by_title: dict[tuple[str, str], list[str]] = {}
    for feat in (report.get("features", []) if isinstance(report, dict) else []):
        slug = feat.get("key")
        if not isinstance(slug, str):
            continue
        for card in feat.get("cards", []) or []:
            title = card.get("title")
            if isinstance(title, str):
                tags_by_title[(slug, title)] = list(card.get("tags") or [])

    for f in feats:
        fid = f["feature_id"]
        ref_ids.append(fid)
        # principles 没有独立列，改由 feature 摘要派生
        ftext = build_feature_text({"title": f["title"], "summary": f["summary"], "principles": {}})
        rows.append({
            "chunk_id": chunk_id_for("feature", fid),
            "kind": "feature",
            "ref_id": fid,
            "card_id": None,
            "text": ftext,
            "repo_id": None,
            "language": None,
            "heading": f["title"],
            "producer": producer,
        })
        cards = conn.execute(
            "SELECT card_id, kind, title, summary, mechanism_desc, language, reusable, "
            "quality_json FROM cards WHERE feature_id = ? ORDER BY card_id",
            (fid,),
        ).fetchall()
        # feature 块带上所属卡的标题：搜到 feature 时能顺藤摸到具体机制
        if cards:
            ftext = ftext + " " + " ".join((c["title"] or "") for c in cards if c["title"])
        for c in cards:
            cid = c["card_id"]
            # P1-3 / G12：mechanism_desc 非语言中立的卡片**不建块**。判断放在这里
            # （索引层唯一写入口）而不是放在各调用方——reindex 与回填走的是同一个
            # index_analysis，闸门写在调用方就等于没有闸门。
            if _is_pending(c["quality_json"]):
                n_pending += 1
                continue
            rows.append({
                "chunk_id": chunk_id_for("card", cid),
                "kind": "card",
                "ref_id": cid,
                "card_id": cid,
                "text": build_card_text({
                    "mechanism_desc": c["mechanism_desc"],
                    "summary": c["summary"],
                    "title": c["title"],
                    "language": c["language"],
                    "tags": tags_by_title.get((f["slug"] or "", c["title"] or ""), []),
                    "evidence": _card_evidence(conn, cid),
                }),
                "repo_id": None,
                "language": c["language"],
                "heading": c["title"],
                "producer": producer,
            })
            n_cards += 1
    # report_section：把 Markdown 按 H2 切段（每段一块）
    n_sections = 0
    if report_md:
        for i, section in enumerate(_split_h2(report_md)):
            cid = chunk_id_for("report_section", analysis_id, i)
            rows.append({
                "chunk_id": cid,
                "kind": "report_section",
                "ref_id": analysis_id,
                "card_id": None,
                "text": section,
                "repo_id": None,
                "language": None,
                "heading": section.splitlines()[0][:120] if section else "",
                "producer": producer,
            })
            n_sections += 1
    drop_chunks_by_ref(conn, "feature", ref_ids)
    drop_chunks_by_ref(conn, "report_section", [analysis_id])
    written = put_chunks(conn, embedder, rows)
    # cards 是「本次建块的卡片数」，pending 单列：被闸门挡下的不是没有卡片，
    # 是卡片没进索引——两者混成一个数会让调用方以为卡片丢了。
    return {"features": len(feats), "cards": n_cards, "sections": n_sections,
            "chunks": written, "pending_cards": n_pending}


def _split_h2(md: str) -> list[str]:
    """按 '## ' 切分 Markdown，返回含标题的段落列表（丢弃首个空段）。"""
    lines = md.splitlines()
    sections: list[str] = []
    cur: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if cur:
                sections.append("\n".join(cur).strip())
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    return [s for s in sections if s]


def set_chunk_repo(conn: sqlite3.Connection, repo_id: str) -> int:
    """把某仓库所有 feature/card/report_section 块的 repo_id 回填（供按仓库过滤）。"""
    n = 0
    for kind, ref_sql in (
        ("feature", "SELECT feature_id FROM features WHERE analysis_id IN (SELECT analysis_id FROM analyses WHERE repo_id = ?)"),
        ("card", "SELECT card_id FROM cards WHERE feature_id IN (SELECT feature_id FROM features WHERE analysis_id IN (SELECT analysis_id FROM analyses WHERE repo_id = ?))"),
        ("report_section", "SELECT analysis_id FROM analyses WHERE repo_id = ?"),
    ):
        for r in conn.execute(ref_sql, (repo_id,)).fetchall():
            rid = list(r)[0]
            conn.execute(
                "UPDATE chunks SET repo_id = ? WHERE kind = ? AND ref_id = ?",
                (repo_id, kind, rid),
            )
            n += 1
    return n
