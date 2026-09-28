"""启动检查清单（tech-design §4.1 / operations.md §4）。

serve-mcp / serve-http 启动时按序执行；任一失败给**可操作**提示而非栈：

1. MEMEX_HOME 可读写；库不存在则提示 memex init。
2. meta.schema_version 与本版常量比对（G9）。
3. meta.embedder 存在；chunk_vectors 无异构 embedder 行（有 -> 警告，不阻止启动）。
4. 嵌入器规格**轻量形式校验**：真模型**不在启动路径同步加载**，而是进程起来后
   后台预热（见 handlers.Runtime.warm）。预热失败时，首次需要向量的工具调用会
   **显式报错**（不静默降级，守 D1/G22）。
5. MEMEX_TOOLS 解析成功；打印放行集。
6. 远程形态：MEMEX_TOKEN 非空，否则拒绝（E16）。

设计取舍：真模型加载要 import torch + 载权重，冷启动十几秒；若放在 initialize 之前
同步执行，客户端（默认 60s 握手超时）极易被判超时。故启动检查只做**轻量**校验，
重活挪到后台线程，initialize 立即返回。
"""
from __future__ import annotations

from typing import Any

from .core import Config, MemexError
from .embeddings import DEFAULT_MODEL
from .store.db import check_schema, connect, get_meta

# 已知句向量模型的输出维度（用于启动横幅与 sanity 校验；未收录的返回 None）
_KNOWN_DIMS = {
    "paraphrase-multilingual-MiniLM-L12-v2": 384,
    "all-MiniLM-L6-v2": 384,
}


def _spec_dim(spec: str) -> int | None:
    """从嵌入器规格推断输出维度（纯字符串运算，不加载模型）。"""
    s = (spec or "").strip()
    if s.startswith("hash:"):
        try:
            return int(s.split(":", 1)[1])
        except ValueError:
            return None
    name = s.split(":", 1)[1] if s.startswith("sentence-transformers:") else s
    return _KNOWN_DIMS.get(name)


def _is_st_spec(spec: str) -> bool:
    s = (spec or "").strip()
    return not (s.startswith("hash:") or s.startswith("http:"))


def run_startup_check(cfg: Config, *, load_embedder: bool = True) -> dict[str, Any]:
    """执行启动检查。通过则返回摘要；失败抛 MemexError（由传输层转成可读提示）。

    load_embedder 保留为兼容参数：启动路径**不再同步加载真模型**，此参数仅决定
    是否对本地缓存做一次轻量探测（用于提前给出「模型未缓存」提示）。
    """
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
        # G11/C4：混模型库**拒启**，不再只 warnings。
        # 写入路径已由 index.put_chunks 的 assert_embedder 守住（C2），故这里的异构行
        # 只可能来自外部改动/半程迁移。远程形态下随机失败最贵（agent 拿到的是错向量、
        # 不是报错），故远程直接 conflict；hash/stdio 侧仅提示（保持本地调试可用）。
        current = cfg.embedder or meta_embedder
        if len(used) > 1 and cfg.is_http:
            raise MemexError(
                "conflict",
                "chunk_vectors 存在异构 embedder 行 " + str(used)
                + "；混模型库会静默毁掉召回质量，拒绝启动。请运行 memex reindex --embedder "
                + str(current),
                {"embedders": used, "current": current},
            )
        if len(used) > 1:
            warnings.append(
                "chunk_vectors 存在异构 embedder 行 " + str(used) + "；检索会被拒，请运行 memex reindex --embedder <当前>"
            )
    finally:
        conn.close()

    # 4. 嵌入器规格：仅做轻量形式校验（真模型改后台预热，见模块 docstring）-- #
    spec = cfg.embedder or meta_embedder or DEFAULT_MODEL
    degraded = spec.strip().startswith("hash:")
    embedder_info: dict[str, Any] = {
        "model": spec,
        "dim": _spec_dim(spec),
        "degraded": degraded,
    }
    if degraded:
        warnings.append("embedder 为 hash 伪向量（degraded:true），无语义，仅供冒烟")
    if load_embedder and _is_st_spec(spec):
        from .embeddings import local_model_cached

        if not local_model_cached(spec):
            warnings.append(
                "嵌入模型未在本地缓存（" + spec + "）；启动后会自动联网下载（约 470MB，"
                "落到卷内 HF_HOME），下载期间 /healthz 的 embedder_ready=false"
            )

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
