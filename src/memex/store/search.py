"""检索层：三通道 RRF 融合（tech-design §2 / solution-analysis「3-channel RRF」）。

通道（等权）：
1. 向量通道：对 chunk_vectors 做余弦（sqlite-vec 可用则走它，否则暴力回退）；
2. 关键词通道：FTS5(trigram) MATCH；
3. 子串通道：LIKE '%q%'。

融合：RRF(k=60)，三通道等权相加；随后按 kind_prior 乘性偏置（在融合之后，
符合「kind_prior applied AFTER fusion」）；可选 rerank（V1 默认 off）。

检索对象是**块**（feature/card/pattern/report_section）；卡片块正文是英文机制描述，
因此同一查询可跨语言命中（第二支柱）。返回值带每个结果命中的通道（matched_by）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..constants import KIND_PRIOR, RRF_K
from ..core import MemexError
from ..embeddings import Embedder, cosine, unpack_vector
from .db import json_loads

_CHANNEL_TOP = 50


def _vector_rank(conn: sqlite3.Connection, embedder: Embedder, query_vec: list[float]) -> list[str]:
    rows = conn.execute("SELECT chunk_id, vec FROM chunk_vectors WHERE embedder = ?", (embedder.model,)).fetchall()
    if not rows:
        # embedder 标识不一致时退一步：用任意已有向量（读侧容错）
        rows = conn.execute("SELECT chunk_id, vec FROM chunk_vectors").fetchall()
    scored: list[tuple[float, str]] = []
    for r in rows:
        try:
            v = unpack_vector(r["vec"])
        except Exception:
            continue
        scored.append((cosine(query_vec, v), r["chunk_id"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cid for _, cid in scored[:_CHANNEL_TOP]]


def _fts_quote(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _keyword_rank(conn: sqlite3.Connection, query: str) -> list[str]:
    q = query.strip()
    if len(q) < 3:
        return []
    try:
        rows = conn.execute(
            "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts) LIMIT ?",
            (_fts_quote(q), _CHANNEL_TOP),
        ).fetchall()
        return [r["chunk_id"] for r in rows]
    except sqlite3.OperationalError:
        return []


def _substr_rank(conn: sqlite3.Connection, query: str) -> list[str]:
    q = query.strip()
    if not q:
        return []
    rows = conn.execute(
        "SELECT chunk_id FROM chunks WHERE text LIKE ? LIMIT ?",
        ("%" + q + "%", _CHANNEL_TOP),
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def _rrf(rank_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    fused: dict[str, float] = {}
    for lst in rank_lists:
        for pos, cid in enumerate(lst):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + pos + 1)
    return fused


def _rerank_enabled(rerank: Any, cfg_rerank: str) -> bool:
    if rerank is None:
        return cfg_rerank not in ("", "off", "false", "0")
    if isinstance(rerank, bool):
        return rerank
    return str(rerank).lower() not in ("off", "false", "0", "")


def search(
    conn: sqlite3.Connection,
    embedder: Embedder | None,
    query: str,
    *,
    limit: int = 8,
    repo_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    cfg_rerank: str = "off",
    rerank: Any = None,
) -> dict[str, Any]:
    """跨仓语义召回。返回 {count,total,items,channels,query}。"""
    q = (query or "").strip()
    if not q:
        raise MemexError("invalid_argument", "query 不能为空", {})

    named: list[tuple[str, list[str]]] = []
    if embedder is not None:
        try:
            qv = embedder.embed_one(q)
            named.append(("vector", _vector_rank(conn, embedder, qv)))
        except MemexError:
            named.append(("vector", []))
    else:
        named.append(("vector", []))
    named.append(("keyword", _keyword_rank(conn, q)))
    named.append(("substr", _substr_rank(conn, q)))

    match_channels: dict[str, set[str]] = {}
    for name, lst in named:
        for cid in lst:
            match_channels.setdefault(cid, set()).add(name)

    fused = _rrf([lst for _, lst in named], RRF_K)
    channels = {
        "vector": len(named[0][1]),
        "keyword": len(named[1][1]),
        "substr": len(named[2][1]),
    }
    if not fused:
        return {"count": 0, "total": 0, "items": [], "channels": channels, "query": q, "notes": ["库中无匹配"]}

    items = _hydrate(conn, fused, match_channels)
    if repo_id:
        items = [it for it in items if it.get("repo_id") == repo_id]
    if language:
        items = [it for it in items if (it.get("language") or "") == language]
    if kind:
        items = [it for it in items if it.get("kind") == kind]
    items.sort(key=lambda it: (-it["score"], it["chunk_id"]))
    total = len(items)
    if limit:
        items = items[:limit]
    return {"count": len(items), "total": total, "items": items, "channels": channels, "query": q}


def _hydrate(conn: sqlite3.Connection, fused: dict[str, float], match_channels: dict[str, set[str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cid, base in fused.items():
        row = conn.execute(
            "SELECT chunk_id, kind, ref_id, card_id, text, repo_id, language, heading FROM chunks WHERE chunk_id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            continue
        prior = KIND_PRIOR.get(row["kind"], 1.0)
        item: dict[str, Any] = {
            "chunk_id": cid,
            "kind": row["kind"],
            "ref_id": row["ref_id"],
            "card_id": row["card_id"],
            "heading": row["heading"],
            "repo_id": row["repo_id"],
            "language": row["language"],
            "score": round(base * prior, 6),
            "rrf": round(base, 6),
            "matched_by": sorted(match_channels.get(cid, set())),
            "text": row["text"],
        }
        if row["kind"] == "card" and row["card_id"]:
            item["card"] = _card_view(conn, row["card_id"])
        items.append(item)
    return items


def _card_view(conn: sqlite3.Connection, card_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT card_id, kind, title, summary, mechanism_desc, language, symbol, reusable, code_spans_json "
        "FROM cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        return {}
    d = dict(row)
    d["reusable"] = bool(d["reusable"])
    d["code_spans"] = json_loads(d.pop("code_spans_json"), [])
    return d
