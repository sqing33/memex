"""检索层：三通道 RRF 融合（tech-design §2 / solution-analysis「3-channel RRF」）。

通道（等权）：
1. 向量通道：对 chunk_vectors 做余弦（sqlite-vec 可用则走它，否则暴力回退）；
2. 关键词通道：FTS5(trigram) MATCH；
3. 子串通道：LIKE '%q%'。

融合：RRF(k=60)，三通道等权相加；随后按 kind_prior 乘性偏置（在融合之后，
符合「kind_prior applied AFTER fusion」）；可选 rerank（V1 默认 off）。

**两个文本通道都是两级降级（V3 实测修正，tech-design §2.2）**：FTS5 的 trigram
分词器下，把整串查询加引号当**短语**等于要求整串连续出现，实测整句中文自然语言
探针恒 0 命中、三通道融合退化成单路向量。所以先整串连续匹配（符号名/短查询），
整串无命中时降级为**按位置切的 trigram OR 匹配**（不依赖任何分词器，中英文一视同仁）。
降级会写进响应的 notes——不静默。

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
_TRIGRAM = 3
# 降级时一条查询最多展开多少个 trigram。FTS5 的 OR 表达式是左深树，
# 过长的查询会撞上 sqlite3 的表达式深度上限（届时 MATCH 抛 OperationalError），
# 所以必须封顶——且封顶这件事要写进 notes，不能让调用方以为全量匹配过了。
_TEXT_FALLBACK_MAX_TERMS = 48


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


def _like_pattern(s: str) -> str:
    """LIKE 的字面量模式：转义 % 与 _（代码标识符里 _ 极常见，不转义就变成通配）。

    顺带修掉一个既有的精度缺陷：查 'get_user' 时旧实现里 '_' 是通配符，
    'getXuser' 也会命中——「整串连续匹配」本该是精确的。
    """
    for ch in ("\\", "%", "_"):
        s = s.replace(ch, "\\" + ch)
    return "%" + s + "%"


def _trigrams(text: str) -> list[str]:
    """按位置切 3 字符窗口（去重、保序）。

    刻意不用正则分词：中文全角标点、英文标识符、路径、数字一视同仁，
    且与 FTS5 的 trigram 切法保持一致。
    """
    s = text.strip()
    if len(s) < _TRIGRAM:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for i in range(len(s) - _TRIGRAM + 1):
        g = s[i : i + _TRIGRAM]
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


def _keyword_rank(conn: sqlite3.Connection, query: str, notes: list[str] | None = None) -> list[str]:
    q = query.strip()
    if len(q) < _TRIGRAM:
        return []
    try:
        rows = conn.execute(
            "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts) LIMIT ?",
            (_fts_quote(q), _CHANNEL_TOP),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return [r["chunk_id"] for r in rows]

    grams = _trigrams(q)
    if not grams:
        return []
    used = grams[:_TEXT_FALLBACK_MAX_TERMS]
    expr = " OR ".join(_fts_quote(g) for g in used)
    try:
        rows = conn.execute(
            "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts) LIMIT ?",
            (expr, _CHANNEL_TOP),
        ).fetchall()
    except sqlite3.OperationalError:
        if notes is not None:
            notes.append("关键词通道降级为 trigram OR 匹配，但查询过长超出 FTS5 表达式上限，本次未参与融合")
        return []
    if notes is not None:
        note = f"关键词通道无整串命中，降级为 trigram OR 匹配（{len(used)} 个 trigram）"
        if len(used) < len(grams):
            note += f"；查询过长已截断（原文可切 {len(grams)} 个）"
        notes.append(note)
    return [r["chunk_id"] for r in rows]


def _substr_rank(conn: sqlite3.Connection, query: str, notes: list[str] | None = None) -> list[str]:
    q = query.strip()
    if not q:
        return []
    rows = conn.execute(
        "SELECT chunk_id FROM chunks WHERE text LIKE ? ESCAPE '\\' LIMIT ?",
        (_like_pattern(q), _CHANNEL_TOP),
    ).fetchall()
    if rows:
        return [r["chunk_id"] for r in rows]

    grams = _trigrams(q)
    if not grams:
        return []
    used = grams[:_TEXT_FALLBACK_MAX_TERMS]
    where = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in used)
    rows = conn.execute(
        f"SELECT chunk_id, COUNT(*) AS hits FROM chunks WHERE {where} "
        f"GROUP BY chunk_id ORDER BY hits DESC, chunk_id LIMIT ?",
        [*(_like_pattern(g) for g in used), _CHANNEL_TOP],
    ).fetchall()
    if notes is not None:
        note = f"子串通道无整串命中，降级为逐 trigram 命中计数（{len(used)} 个 trigram）"
        if len(used) < len(grams):
            note += f"；查询过长已截断（原文可切 {len(grams)} 个）"
        notes.append(note)
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
    notes: list[str] = []
    named.append(("keyword", _keyword_rank(conn, q, notes)))
    named.append(("substr", _substr_rank(conn, q, notes)))

    match_channels: dict[str, set[str]] = {}
    for name, lst in named:
        for cid in lst:
            match_channels.setdefault(cid, set()).add(name)

    # G29：substr 只在 keyword 返空时兜底投票——两个文本通道在降级路径上是
    # 同一个测量，各占 1/3 等于把字面信号加权两次。channels 仍如实报三通道条数。
    channels = {
        "vector": len(named[0][1]),
        "keyword": len(named[1][1]),
        "substr": len(named[2][1]),
    }
    votes = [lst for _, lst in named[:2]]
    if not named[1][1]:
        votes.append(named[2][1])
    fused = _rrf(votes, RRF_K)
    if not fused:
        return {"count": 0, "total": 0, "items": [], "channels": channels, "query": q,
                "notes": [*notes, "库中无匹配"]}

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
    return {"count": len(items), "total": total, "items": items, "channels": channels, "query": q,
            "notes": notes}


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
