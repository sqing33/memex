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

import os
import sqlite3
import threading
from typing import Any

from ..constants import (
    DEFAULT_RERANK_MODEL,
    KIND_PRIOR,
    RERANK_MAX_LENGTH,
    RRF_K,
)
from ..core import MemexError
from ..embeddings import (
    Embedder,
    _load_timeout_seconds,
    _run_with_deadline,
    cosine,
    unpack_vector,
)
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


# —— 检索重排（G25）——
# _rerank_enabled 此前是死代码：旋钮接到了，后面什么都没接。这里把真重排接上。
# 只对 RRF 融合后的候选**重排**，不增不删——所以 top-10 命中率按构造不变，
# rerank 真正买的是「前 K 条里谁排第一」，这正是 D2 要的东西。
# 失败一律显式报错（零假成功），绝不静默退回 RRF 假装重排成功。

_CROSS_ENC_LOCK = threading.Lock()
_CROSS_ENC_CACHE: dict[str, Any] = {}
_CROSS_ENC_ERRORS: dict[str, MemexError] = {}


def _load_cross_encoder(model_name: str) -> Any:
    """加载 cross-encoder（sentence-transformers 的 CrossEncoder）。

    真模型加载昂贵，联网回源可能挂几分钟（与嵌入器同一类风险），因此：
    按模型名进程内缓存、失败结果也缓存、墙钟上限复用 MEMEX_EMBEDDER_LOAD_TIMEOUT。
    """
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:  # noqa: BLE001 - 归一为 MemexError
        raise MemexError(
            "internal",
            "rerank 需要 sentence-transformers：装 memex[default] 或把 rerank 关掉",
            {
                "reason": str(exc),
                "model": model_name,
                "outs": [
                    "pip install memex[default]",
                    "MEMEX_RERANK=off 关掉重排，退回三通道 RRF",
                ],
            },
        ) from exc

    def build() -> Any:
        return CrossEncoder(model_name, max_length=RERANK_MAX_LENGTH)

    # 离线优先，与嵌入器同一套路（embeddings.py:191）：命中本地 HF 缓存即不联网；
    # 只有本地没有才联网拉一次，且有墙钟上限。
    # 注意 bge-reranker-base 是 1.1GB 级的模型，比嵌入器重得多，
    # 墙钟上限用独立的 MEMEX_RERANK_LOAD_TIMEOUT（默认 120s）而不是复用 20s。
    label = "cross-encoder " + model_name
    try:
        return CrossEncoder(model_name, max_length=RERANK_MAX_LENGTH, local_files_only=True)
    except Exception:  # noqa: BLE001 - 本地无缓存 -> 联网拉取一次（有界）
        pass
    return _run_with_deadline(build, _rerank_load_timeout_seconds(), label)


def _rerank_load_timeout_seconds() -> float:
    """cross-encoder 联网加载的墙钟上限（秒）。

    独立于 MEMEX_EMBEDDER_LOAD_TIMEOUT：bge-reranker-base 约 1.1GB，
    用嵌入器的 20s 默认值会**必然超时**（实测首次联网加载就撞了 20s 上限）。
    """
    raw = os.environ.get("MEMEX_RERANK_LOAD_TIMEOUT")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 120.0


def _get_cross_encoder(model_name: str) -> Any:
    """按模型名取 cross-encoder（进程内缓存，失败也缓存）。"""
    key = model_name.strip() or DEFAULT_RERANK_MODEL
    hit = _CROSS_ENC_CACHE.get(key)
    if hit is not None:
        return hit
    err = _CROSS_ENC_ERRORS.get(key)
    if err is not None:
        raise err
    with _CROSS_ENC_LOCK:
        hit = _CROSS_ENC_CACHE.get(key)
        if hit is not None:
            return hit
        err = _CROSS_ENC_ERRORS.get(key)
        if err is not None:
            raise err
        try:
            hit = _load_cross_encoder(key)
        except MemexError as exc:
            _CROSS_ENC_ERRORS[key] = exc
            raise
        except Exception as exc:  # noqa: BLE001 - 归一为 MemexError 并缓存
            wrapped = MemexError(
                "internal",
                "加载 cross-encoder 失败：" + str(exc),
                {"model": model_name, "reason": str(exc)},
            )
            _CROSS_ENC_ERRORS[key] = wrapped
            raise wrapped from exc
        _CROSS_ENC_CACHE[key] = hit
        return hit


def _resolve_rerank_model(cfg_rerank: str) -> str:
    """把配置值解析成模型名：on / true / 1 走 G25 定案的默认模型名。"""
    raw = (cfg_rerank or "").strip()
    if raw.lower() in ("on", "true", "1", "yes", "default"):
        return DEFAULT_RERANK_MODEL
    return raw


def _apply_rerank(
    items: list[dict[str, Any]],
    query: str,
    model_name: str,
    rerank: Any,
    notes: list[str],
) -> list[dict[str, Any]]:
    """用 cross-encoder 对已融合的候选重排（只换顺序，不增不删）。

    rerank 参数是现成的 cross-encoder 对象时直接用它（测试与调用方注入），
    否则按 model_name 取进程内缓存的实例。
    """
    if not items:
        return items
    encoder = rerank if not isinstance(rerank, bool) and rerank is not None else None
    if encoder is None:
        encoder = _get_cross_encoder(model_name)
    pairs = [(query, str(it.get("text") or "")) for it in items]
    try:
        scores = list(encoder.predict(pairs))
    except Exception as exc:  # noqa: BLE001 - 归一为 MemexError，不静默退回 RRF
        raise MemexError(
            "internal",
            "cross-encoder 打分失败：" + str(exc),
            {
                "model": model_name,
                "reason": str(exc),
                "outs": [
                    "MEMEX_RERANK=off 关掉重排，退回三通道 RRF",
                    "换一个 cross-encoder 模型名（MEMEX_RERANK=<name>）",
                ],
            },
        ) from exc
    if len(scores) != len(items):
        raise MemexError(
            "internal",
            "cross-encoder 返回的分数条数与候选数不一致",
            {"model": model_name, "scores": len(scores), "candidates": len(items)},
        )
    for it, sc in zip(items, scores):
        it["rerank_score"] = round(float(sc), 6)
    notes.append(
        "rerank=%s：对前 %d 条候选重排（只重排不增删，召回集合不变）" % (model_name, len(items))
    )
    # 稳定排序：分数降序；同分保持 RRF 原序（list.sort 是稳定排序）。
    return sorted(items, key=lambda it: -float(it["rerank_score"]))


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
    # G25：重排只换顺序不增不删，截断后生效——所以它买的是「前 K 条里谁排第一」
    # 而不是扩大召回集合。默认 off（实测 ms-marco 有害、bge 有效，见 decisions.md）。
    if _rerank_enabled(rerank, cfg_rerank):
        items = _apply_rerank(
            items,
            q,
            _resolve_rerank_model(str(rerank) if not isinstance(rerank, bool) and rerank else cfg_rerank),
            rerank if not isinstance(rerank, bool) and rerank is not None else None,
            notes,
        )
    return {"count": len(items), "total": total, "items": items, "channels": channels, "query": q,
            "notes": notes}


def _hydrate(conn: sqlite3.Connection, fused: dict[str, float], match_channels: dict[str, set[str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cid, base in fused.items():
        row = conn.execute(
            "SELECT chunk_id, kind, ref_id, card_id, text, repo_id, language, heading, producer "
            "FROM chunks WHERE chunk_id = ?",
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
            # G21：可能是 None —— 历史块（加列之前建的）与跨 producer 的 pattern 块。
            # **不给默认值**，调用方拿到 null 应理解成「不知道」，不是「agent」。
            "producer": row["producer"],
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
