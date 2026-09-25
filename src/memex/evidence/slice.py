"""按行读代码切片（T3 read_file_slice）。

只读、幂等。路径必须落在仓库根内（拒绝 ../ 逃逸 -> invalid_argument）。
行号 1-based 闭区间；越界夹取而非报错，但总数与截断标志如实返回。
返回 file_sha（内容 sha256），供 agent 之后校验代码是否漂移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..core import MemexError, Paths
from ..fsutil import sha256_bytes
from ..limits import SLICE_MAX_LINES_DEFAULT, SLICE_MAX_LINES_HARD
from ..store.db import connect


def _resolve(repo_root: Path, rel: str) -> Path:
    """把 rel 解析到 repo_root 之内；逃逸抛 invalid_argument。"""
    target = (repo_root / rel).resolve()
    root = repo_root.resolve()
    if target != root and root not in target.parents:
        raise MemexError("invalid_argument", "路径越界", {"path": rel})
    return target


def read_slice(
    cfg: Any,
    repo_id: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    max_lines: int = SLICE_MAX_LINES_DEFAULT,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """读取 [start_line, end_line] 行（1-based 闭区间），返回文本与元信息。"""
    if start_line < 1:
        raise MemexError("invalid_argument", "start_line 必须 >= 1", {"start_line": start_line})
    cap = min(max_lines, SLICE_MAX_LINES_HARD)
    paths = Paths(cfg.home)
    owns = conn is None
    if conn is None:
        if not paths.db.exists():
            raise MemexError("not_found", "知识库不存在，请先运行 memex init", {"db": str(paths.db)})
        conn = connect(str(paths.db))
    try:
        row = conn.execute("SELECT repo_path, subpath FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    finally:
        if owns:
            conn.close()
    if row is None:
        raise MemexError("not_found", "仓库未收录", {"repo_id": repo_id})
    repo_root = Path(row["repo_path"] or "")
    if not row["repo_path"] or not repo_root.is_dir():
        raise MemexError("not_found", "本地克隆缺失，请先 fetch_repo", {"repo_id": repo_id})

    target = _resolve(repo_root, path)
    if not target.is_file():
        raise MemexError("not_found", "文件不存在", {"path": path})
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise MemexError("internal", "读取文件失败", {"path": path, "reason": str(exc)}) from exc
    file_sha = sha256_bytes(data)
    lines = data.decode("utf-8", errors="replace").splitlines()
    total = len(lines)

    eff_end = total if end_line is None else min(end_line, total)
    if (eff_end - start_line + 1) > cap:
        eff_end = start_line + cap - 1
        truncated = True
    else:
        truncated = False
    if eff_end < start_line:
        eff_end = start_line - 1
    chunk = lines[start_line - 1: eff_end] if total >= start_line else []

    return {
        "path": path,
        "start_line": start_line,
        "end_line": eff_end,
        "text": "\n".join(chunk),
        "file_sha": file_sha,
        "total_lines": total,
        "truncated": truncated,
    }
