"""仓库打包下发（T4 request_repo_bundle，G18）。

把本地克隆打成 git bundle，供无法直连外网的 agent 取用。V1 为本地形态：
url 即 bundle 文件路径（file:// 形式），bytes 为实际大小，sha256 供下行校验。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import Config, MemexError, Paths
from ..fsutil import sha256_file
from ..store.db import connect
from . import gitutil
from .repo import get_repo

BUNDLE_TTL_SECONDS = 3600


def request_repo_bundle(cfg: Config, repo_id: str, *, ref: str | None = None) -> dict[str, Any]:
    """为已登记仓库生成 bundle，返回 {url, sha256, commit_sha, bytes, expires_at, usage}。"""
    if not repo_id or "__" not in repo_id:
        raise MemexError("invalid_argument", "repo_id 形状不合法", {"repo_id": repo_id})
    db_path = Paths(cfg.home).db
    if not db_path.exists():
        raise MemexError("not_found", "知识库不存在，请先运行 memex init", {"db": str(db_path)})
    conn = connect(str(db_path))
    try:
        row = get_repo(conn, repo_id)
    finally:
        conn.close()
    if row is None:
        raise MemexError("not_found", "仓库未收录", {"repo_id": repo_id})
    repo_path = row.get("repo_path")
    if not repo_path or not Path(repo_path).is_dir():
        raise MemexError("not_found", "本地克隆缺失，请先 fetch_repo", {"repo_id": repo_id})

    commit_sha = gitutil.rev_parse(repo_path, ref or "HEAD")
    out_dir = Paths(cfg.home).repos_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / f"{repo_id}.{commit_sha[:12]}.bundle"
    proc = gitutil.run_git(
        ["bundle", "create", str(bundle_path), "--all"], cwd=repo_path, timeout=cfg.clone_timeout
    )
    if proc.returncode != 0 or not bundle_path.is_file():
        raise MemexError(
            "internal", "生成 bundle 失败", {"stderr": proc.stderr.strip()[:500], "repo_id": repo_id}
        )
    size = bundle_path.stat().st_size
    return {
        "url": "file://" + str(bundle_path),
        "sha256": sha256_file(bundle_path),
        "commit_sha": commit_sha,
        "bytes": size,
        "expires_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + BUNDLE_TTL_SECONDS)
        ),
        "usage": "git clone <url> <dir>  然后 git checkout " + commit_sha,
    }
