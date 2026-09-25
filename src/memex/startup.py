"""启动检查清单（tech-design §4.1 / operations.md §4）。

serve-mcp / serve-http 启动时按序执行；任一失败给**可操作**提示而非栈：

1. MEMEX_HOME 可读写；库不存在则提示 memex init。
2. meta.schema_version 与本版常量比对（G9）。
3. meta.embedder 存在；chunk_vectors 无异构 embedder 行（有 -> 警告，不阻止启动）。
4. 嵌入模型可加载；失败 -> **拒绝启动**，并给三条出路（G22 / D1）。
5. MEMEX_TOOLS 解析成功；打印放行集。
6. 远程形态：MEMEX_TOKEN 非空，否则拒绝（E16）。
"""
from __future__ import annotations

from typing import Any

from .core import Config, MemexError
from .embeddings import DEFAULT_MODEL, get_embedder
from .store.db import check_schema, connect, get_meta


def run_startup_check(cfg: Config, *, load_embedder: bool = True) -> dict[str, Any]:
    """执行启动检查。通过则返回摘要；失败抛 MemexError（由传输层转成可读提示）。"""
    warnings: list[str] = []
    paths = cfg.paths

    # 1. HOME 可读写 ---------------------------------------------------- #
    home = paths.home
    if not home.exists():
        raise MemexError(
            "not_found",
            "MEMEX_HOME 不存在，请先运行 memex init",
            {"home": str(home)},
        )
    if not paths.db.exists():
        raise MemexError(
            "not_found",
            "数据库不存在，请先运行 memex init",
            {"db": str(paths.db)},
        )

    conn = connect(paths.db)
    try:
        # 2. schema 版本（G9） ------------------------------------------ #
        check_schema(conn)

        # 3. embedder meta + 异构行（G11） ------------------------------ #
        meta_embedder = get_meta(conn, "embedder")
        rows = conn.execute(
            "SELECT DISTINCT embedder FROM chunk_vectors WHERE embedder IS NOT NULL"
        ).fetchall()
        used = sorted({str(r["embedder"]) for r in rows})
        if len(used) > 1:
            warnings.append(
                "chunk_vectors 存在异构 embedder 行 " + str(used) + "；检索会被拒，请运行 memex reindex --embedder <当前>"
            )
    finally:
        conn.close()

    # 4. 嵌入模型可加载（失败即拒绝启动，D1/G22） ----------------------- #
    embedder_info: dict[str, Any] = {}
    if load_embedder:
        spec = cfg.embedder or meta_embedder or DEFAULT_MODEL
        try:
            emb = get_embedder(spec)
        except MemexError as exc:
            raise MemexError(
                exc.code,
                "嵌入模型不可用，拒绝启动：" + exc.message,
                {
                    "spec": spec,
                    "reason": (exc.details or {}).get("reason"),
                    "outs": [
                        "预置模型文件到 HF 缓存（离线可用）",
                        "MEMEX_EMBEDDER=http:<url> 指向远端嵌入接口",
                        "仅调试可显式 MEMEX_EMBEDDER=hash:512（无语义，degraded:true）",
                    ],
                },
            ) from exc
        embedder_info = {"model": emb.model, "dim": emb.dim, "degraded": emb.degraded}
        if emb.degraded:
            warnings.append("embedder 为 hash 伪向量（degraded:true），无语义，仅供冒烟")

    # 5. MEMEX_TOOLS 解析 ---------------------------------------------- #
    tools = sorted(cfg.tools)

    # 6. 远程形态必须带 token（E16） ----------------------------------- #
    if cfg.is_http and not cfg.token:
        raise MemexError(
            "invalid_argument",
            "远程形态必须设置 MEMEX_TOKEN（E16）",
            {"env": "MEMEX_TOKEN"},
        )

    return {"ok": True, "embedder": embedder_info, "tools": tools, "warnings": warnings}


def startup_banner(summary: dict[str, Any]) -> str:
    """把启动摘要渲染成一行（写 stderr，不污染 stdio 协议流）。"""
    emb = summary.get("embedder") or {}
    model = emb.get("model") or "-"
    degraded = " degraded" if emb.get("degraded") else ""
    tools = ",".join(summary.get("tools") or [])
    return "memex 启动：embedder=" + str(model) + degraded + " tools=[" + tools + "]"
