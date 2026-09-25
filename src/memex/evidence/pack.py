"""证据包：目录树 + 入口点 + 符号清单（T2 / A2）。

A2 明令：证据包**只给**目录树、入口点、符号清单，**不给代码切片**。
理由——代码切片要 agent 自己按需拉（read_file_slice），
server 不替 agent 做「哪段代码重要」的判断，避免把判断力收走。

超限一律截断 + 打标（G5），不因为仓库大就拒绝。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..core import MemexError, Paths
from ..fsutil import detect_language, load_gitignore, walk_files
from ..limits import READMES, depth_profile
from ..store.db import connect
from .symbols import Symbol, extract_symbols

ENTRY_KINDS = ("main", "cli", "server", "worker", "test", "config", "build", "docs")

_MAIN_NAMES = {
    "main.py": "main", "__main__.py": "main", "app.py": "main", "manage.py": "main",
    "main.go": "main", "main.rs": "main", "index.js": "main", "index.ts": "main",
    "server.py": "server", "server.js": "server", "server.ts": "server",
}
_ROLE_BY_KIND = {
    "main": "程序主入口",
    "cli": "命令行入口",
    "server": "服务端入口",
    "worker": "后台任务入口",
    "test": "测试入口",
    "config": "构建/依赖配置",
    "build": "构建脚本",
    "docs": "文档",
}


def _read_text(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _classify_file(rel: str, text: str) -> str | None:
    """按路径名与内容判定入口点类别；不命中返回 None。"""
    name = rel.split("/")[-1]
    lowered = text.lower()
    if name in _MAIN_NAMES:
        return _MAIN_NAMES[name]
    if name.startswith("test_") or name.endswith("_test.go") or name.endswith(".test.js") or name.endswith(".test.ts"):
        return "test"
    if "/tests/" in f"/{rel}" or rel.startswith("tests/") or "/test/" in f"/{rel}":
        return "test"
    if name in {"setup.py", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt", "pom.xml", "build.gradle"}:
        return "config"
    if name in {"Dockerfile", "Makefile", "docker-compose.yml", "docker-compose.yaml"} or name.startswith("Dockerfile"):
        return "build"
    if rel.lower().startswith("docs/") or name.lower() in {"readme.md", "readme.rst"} or rel.lower().endswith(".md"):
        return "docs"
    if "argparse" in lowered or "click.command" in lowered or "typer" in lowered or "cobra.command" in lowered:
        return "cli"
    if any(k in lowered for k in ("flask(", "fastapi(", "uvicorn.run", "http.server", "express()", "createServer(", "actix_web", "axum::")):
        return "server"
    if any(k in lowered for k in ("celery", "rq.worker", "worker(", "grpc.server")):
        return "worker"
    return None


def _build_tree(items: list[Any], *, truncate: bool) -> dict[str, Any]:
    """构造目录树（children 嵌套）；根节点路径为 ""。"""
    root: dict[str, Any] = {"path": "", "type": "dir", "children": []}
    dir_index: dict[str, dict[str, Any]] = {"": root}
    for it in items:
        parts = it.rel_path.split("/")
        cur = root
        cur_path = ""
        for d in parts[:-1]:
            cur_path = f"{cur_path}/{d}" if cur_path else d
            if cur_path not in dir_index:
                node: dict[str, Any] = {"path": cur_path, "type": "dir", "children": []}
                dir_index[cur_path] = node
                cur["children"].append(node)
            cur = dir_index[cur_path]
        cur["children"].append({
            "path": it.rel_path, "type": "file", "size": it.size, "lang": it.language,
        })
    root["truncated"] = truncate
    return root


def build_pack(
    cfg: Any,
    repo_id: str,
    *,
    depth: str = "standard",
    subpath: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """生成证据包（T2 payload 的 tree/entry_points/symbols/stats 部分）。"""
    profile = depth_profile(depth)
    paths = Paths(cfg.home)
    owns = conn is None
    if conn is None:
        if not paths.db.exists():
            raise MemexError("not_found", "知识库不存在，请先运行 memex init", {"db": str(paths.db)})
        conn = connect(str(paths.db))
    try:
        row = conn.execute("SELECT * FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
        if row is None:
            raise MemexError("not_found", "仓库未收录", {"repo_id": repo_id})
        repo = {k: row[k] for k in row.keys()}
    finally:
        if owns:
            conn.close()

    repo_root = Path(repo["repo_path"] or "")
    if not repo["repo_path"] or not repo_root.is_dir():
        raise MemexError("not_found", "本地克隆缺失，请先 fetch_repo", {"repo_id": repo_id})
    effective_subpath = subpath if subpath is not None else repo.get("subpath")
    base = repo_root / effective_subpath if effective_subpath else repo_root
    if not base.is_dir():
        raise MemexError("invalid_argument", "subpath 不存在", {"subpath": effective_subpath})

    gitignore = load_gitignore(base)
    items, truncated = walk_files(
        repo_root,
        gitignore=gitignore,
        max_files=profile.max_files,
        max_file_bytes=cfg.max_file_bytes,
        subpath=effective_subpath,
    )

    # README（按深度截断）
    readme_text = ""
    readme_path = ""
    for name in READMES:
        cand = base / name
        if cand.is_file():
            readme_path = name
            readme_text = _read_text(cand, profile.max_readme)
            break

    entry_points: list[dict[str, Any]] = []
    symbols: list[Symbol] = []
    symbols_truncated = False
    seen_entries: set[str] = set()
    for it in items:
        text = _read_text(base / it.rel_path, 20000)
        kind = _classify_file(it.rel_path, text)
        if kind is not None and it.rel_path not in seen_entries:
            seen_entries.add(it.rel_path)
            entry_points.append({
                "path": it.rel_path,
                "role": _ROLE_BY_KIND.get(kind, "入口点"),
                "kind": kind,
            })
        if it.language == "other":
            continue
        for sym in extract_symbols(it.rel_path, text, it.language):
            symbols.append(sym)
            if len(symbols) >= profile.symbol_cap:
                symbols_truncated = True
                break
        if symbols_truncated:
            break

    total_bytes = sum(i.size for i in items)
    dirs = {"/".join(i.rel_path.split("/")[:-1]) for i in items}
    stats = {
        "files": len(items),
        "dirs": len({d for d in dirs if d}),
        "bytes": total_bytes,
        "symbols_total": len(symbols),
        "symbols_returned": len(symbols),
        "truncated": bool(truncated or symbols_truncated),
        "truncation_reason": (
            "max_files" if truncated else ("symbol_cap" if symbols_truncated else None)
        ),
    }

    return {
        "repo": repo,
        "commit_sha": repo.get("head_sha"),
        "readme": {"path": readme_path, "text": readme_text},
        "tree": _build_tree(items, truncate=truncated),
        "entry_points": entry_points,
        "symbols": [s.to_dict() for s in symbols],
        "stats": stats,
    }
