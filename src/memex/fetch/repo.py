"""仓库登记与抓取（T1 fetch_repo / T17 upload_repo_bundle）。

职责：
- 把 GitHub/GitLab/Gitee URL 变成受控的本地克隆（凭据只在 server 侧注入）。
- 维护 repos 登记表，保证 (repo_url, ref) 幂等：同 ref 不重复下载；
  refresh 只比对 head_sha，绝不自动重分析（G8/G10/G17）。
- 本地克隆目录布局：$MEMEX_HOME/repos/<repo_id>/。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..core import Config, MemexError, Paths, RepoRef, parse_repo_url
from ..fsutil import sha256_file
from ..store.db import json_dumps, json_loads, utcnow
from .detect import detect_language
from . import gitutil

# 克隆并发闸（G7：max 3）。stdio 单进程即可生效；serve-http 由线程共享。
_CLONE_GATE = threading.Semaphore(3)


def _identity_key(ref: RepoRef) -> tuple[str, str | None]:
    """身份键：优先 host#<numeric id>，无 API 时退化为 host#owner/name（G10）。

    V1 不打 host 元数据 API，因此总是走 fallback —— 返回警告文本让上层透出。
    """
    key = f"{ref.host}#{ref.owner}/{ref.name}"
    warning = (
        "未能获取稳定数字 ID，identity_key 退化为 owner/name；"
        "仓库改名会被视为新仓库（G10）"
    )
    return key, warning


def _repo_dir(cfg: Config, repo_id: str) -> Path:
    return Paths(cfg.home).repo_dir(repo_id)


def get_repo(conn: sqlite3.Connection, repo_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row is not None else None


def repo_summary(row: dict[str, Any], *, analyzed_sha: str | None = None) -> dict[str, Any]:
    """Repos 摘要（mcp-tools §1.5 RepoSummary 形状）。"""
    return {
        "repo_id": row.get("repo_id"),
        "repo_full_name": row.get("full_name"),
        "url": row.get("url"),
        "host": row.get("host"),
        "language": row.get("language"),
        "stars": row.get("stars"),
        "license": row.get("license"),
        "head_sha": row.get("head_sha"),
        "analyzed_sha": analyzed_sha,
        "is_stale": bool(row.get("is_stale")),
        "is_fork": bool(row.get("is_fork")),
        "fork_of": row.get("fork_of"),
        "subpath": row.get("subpath"),
        "source": row.get("source"),
        "cloned_at": row.get("cloned_at"),
    }


def _upsert_repo(
    conn: sqlite3.Connection,
    *,
    repo_id: str,
    full_name: str,
    url: str,
    host: str,
    identity_key: str,
    source: str,
    repo_path: str,
    head_sha: str | None,
    default_branch: str | None,
    subpath: str | None,
    is_stale: bool,
    is_local: bool,
    language: str | None,
    aliases: list[str] | None = None,
) -> None:
    """写入或更新 repos 行。
    已存在的 aliases_json / fork_of / is_fork **不在** UPDATE 列表里，故被保留；
    language 每次按克隆目录重新统计（换 embedder 或 reindex 后仍与真实内容一致）。
    """
    existing = conn.execute("SELECT aliases_json FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    aliases_json = json_dumps(aliases if aliases is not None else json_loads(
        existing["aliases_json"] if existing else None, []))
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, default_branch, language, stars, license, "
        "description, subpath, identity_key, aliases_json, fork_of, is_fork, source, is_stale, "
        "head_sha, cloned_at, repo_path, is_local) "
        "VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?,?,?,NULL,0,?,?,?,?,?,?) "
        "ON CONFLICT(repo_id) DO UPDATE SET "
        "full_name=excluded.full_name, url=excluded.url, host=excluded.host, "
        "default_branch=excluded.default_branch, subpath=excluded.subpath, " 
        "language=excluded.language, identity_key=excluded.identity_key, source=excluded.source, " 
        "is_stale=excluded.is_stale, head_sha=excluded.head_sha, "
        "cloned_at=excluded.cloned_at, repo_path=excluded.repo_path, aliases_json=excluded.aliases_json",
        (
            repo_id, full_name, url, host, default_branch, language, subpath, identity_key,
            aliases_json, source, int(is_stale), head_sha, utcnow(), repo_path, int(is_local),
        ),
    )


def ensure_repo(
    cfg: Config,
    ref: RepoRef,
    *,
    ref_name: str | None = None,
    subpath: str | None = None,
    refresh: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """幂等获取仓库：已有且 head_sha 已知则直接复用；refresh 时重比对。

    返回 {"repo": summary, "is_new": bool, "repo_path": str|None, "warnings": [...]}。
    """
    repo_id = ref.repo_id
    path = _repo_dir(cfg, repo_id)
    identity_key, warning = _identity_key(ref)
    warnings: list[str] = [warning] if warning else []
    owns = conn is None
    if conn is None:
        from ..store.db import connect

        conn = connect(str(Paths(cfg.home).db))
    try:
        existing = get_repo(conn, repo_id)
        if existing is not None and not refresh and existing.get("head_sha") and Path(existing["repo_path"] or "").is_dir():
            # 同 ref 直接复用，不再触网（T1 幂等）
            return {
                "repo": repo_summary(existing),
                "is_new": False,
                "repo_path": None,
                "warnings": warnings,
            }

        with _CLONE_GATE:
            result = gitutil.clone(cfg, ref, path, ref_name=ref_name)

        stale = False
        is_new = existing is None
        if existing is not None and existing.get("head_sha") and existing["head_sha"] != result.head_sha:
            stale = True  # refresh 发现新 head：已有分析标记 stale，绝不自动重分析
        _upsert_repo(
            conn,
            repo_id=repo_id,
            full_name=ref.full_name,
            url=ref.url,
            host=ref.host,
            identity_key=identity_key,
            source="clone",
            repo_path=result.repo_path,
            head_sha=result.head_sha,
            default_branch=result.default_branch,
            subpath=subpath,
            is_stale=stale or bool(existing and existing.get("is_stale")),
            language=detect_language(result.repo_path),
            is_local=False,
        )
        row = get_repo(conn, repo_id)
        assert row is not None
        return {
            "repo": repo_summary(row),
            "is_new": is_new,
            "repo_path": result.repo_path if cfg.allow_local_paths else None,
            "warnings": warnings,
        }
    finally:
        if owns:
            conn.close()


def fetch_repo(
    cfg: Config,
    repo_url: str,
    *,
    ref: str | None = None,
    subpath: str | None = None,
    refresh: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """T1 入口：解析 URL -> 校验允许清单 -> 幂等克隆（G6）。"""
    parsed = parse_repo_url(repo_url, allow_hosts=cfg.hosts)
    return ensure_repo(cfg, parsed, ref_name=ref, subpath=subpath, refresh=refresh, conn=conn)


def upload_repo_bundle(
    cfg: Config,
    bundle_path: str,
    repo_url: str,
    *,
    ref: str | None = None,
    subpath: str | None = None,
    sha256: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """T17：上传 git bundle -> verify -> clone；source='upload'（§1.5）。

    大小上限 MEMEX_MAX_BUNDLE_BYTES；sha256 不符直接 invalid_argument，绝不落半行。
    """
    p = Path(bundle_path).expanduser()
    if not p.is_file():
        raise MemexError("invalid_argument", "bundle 文件不存在", {"bundle_path": str(p)})
    size = p.stat().st_size
    if size > cfg.max_bundle_bytes:
        raise MemexError(
            "invalid_argument",
            "bundle 超过内存上限",
            {"bytes": size, "max_bundle_bytes": cfg.max_bundle_bytes},
        )
    if sha256 is not None:
        actual = sha256_file(p)
        if actual.lower() != sha256.lower():
            raise MemexError(
                "invalid_argument",
                "bundle sha256 校验失败",
                {"expected": sha256.lower(), "actual": actual},
            )
    if not gitutil.verify_bundle(p):
        raise MemexError("invalid_argument", "git bundle verify 失败", {"bundle_path": str(p)})

    parsed = parse_repo_url(repo_url, allow_hosts=cfg.hosts)
    repo_id = parsed.repo_id
    dest = _repo_dir(cfg, repo_id)
    owns = conn is None
    if conn is None:
        from ..store.db import connect

        conn = connect(str(Paths(cfg.home).db))
    try:
        result = gitutil.clone_from_bundle(cfg, p, dest)
        identity_key, warning = _identity_key(parsed)
        _upsert_repo(
            conn,
            repo_id=repo_id,
            full_name=parsed.full_name,
            url=parsed.url,
            host=parsed.host,
            identity_key=identity_key,
            source="upload",
            repo_path=result.repo_path,
            head_sha=result.head_sha,
            default_branch=result.default_branch,
            subpath=subpath,
            is_stale=False,
            language=detect_language(result.repo_path),
            is_local=False,
        )
        row = get_repo(conn, repo_id)
        assert row is not None
        return {
            "repo": repo_summary(row),
            "commit_sha": result.head_sha,
            "bytes": size,
            "repo_path": result.repo_path if cfg.allow_local_paths else None,
            "is_new": True,
            "warnings": [warning] if warning else [],
        }
    finally:
        if owns:
            conn.close()
