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
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..core import Config, MemexError, Paths, RepoRef, parse_repo_url
from ..fsutil import sha256_file
from ..store.db import json_dumps, json_loads, utcnow
from . import gitutil
from .detect import detect_language
from .hostmeta import HostMeta, fetch_host_meta

# 克隆并发闸（G7）：按 cfg.clone_concurrency 建闸并复用。
# 旧实现是模块级 `Semaphore(3)` 硬编码——`MEMEX_CLONE_CONCURRENCY` 声明了却永远不生效。
_GATES: dict[int, threading.Semaphore] = {}
_GATES_LOCK = threading.Lock()


def _clone_gate(cfg: Config) -> threading.Semaphore:
    """取（或建）该并发度对应的闸。同一进程内 n 相同的调用者共享一个闸。"""
    n = max(1, int(cfg.clone_concurrency or 3))
    with _GATES_LOCK:
        gate = _GATES.get(n)
        if gate is None:
            gate = threading.Semaphore(n)
            _GATES[n] = gate
        return gate


def _merge_renamed(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    owner: sqlite3.Row,
    ref: RepoRef,
    identity_key: str,
    head_sha: str | None,
    language: str | None,
    meta: HostMeta,
) -> dict[str, Any]:
    """改名/转移后的合并：沿用既有 repo_id，旧名进 aliases_json。

    只动 repos 一行，绝不碰 analyses / features / cards / chunks——
    历史分析保留在原 repo_id 下，这是 G10 的原意。
    """
    target_id = owner["repo_id"]
    old_names = json_loads(owner["aliases_json"], [])
    if owner["full_name"] and owner["full_name"] != ref.full_name:
        old_names = [n for n in old_names if n != owner["full_name"]]
        old_names.append(owner["full_name"])
    stale = bool(owner["head_sha"] and head_sha and owner["head_sha"] != head_sha)
    conn.execute(
        "UPDATE repos SET full_name = ?, url = ?, host = ?, language = ?, head_sha = ?, "
        "aliases_json = ?, is_stale = CASE WHEN ? THEN 1 ELSE is_stale END, "
        "stars = COALESCE(?, stars), license = COALESCE(?, license), "
        "description = COALESCE(?, description) "
        "WHERE repo_id = ?",
        (
            ref.full_name, ref.url, ref.host, language, head_sha, json_dumps(old_names),
            int(stale), meta.stars, meta.license, meta.description, target_id,
        ),
    )
    row = get_repo(conn, target_id)
    assert row is not None
    return {
        "repo": repo_summary(row),
        "is_new": False,
        "repo_path": None,
        "merged_into": target_id,
        "renamed_from": owner["full_name"],
        "aliases": old_names,
        "warnings": [
            f"仓库已改名为 {ref.full_name}：沿用既有 repo_id {target_id}，历史分析保持不变"
        ] + (["检测到新的提交，既有分析已标记 stale，需重新分析"] if stale else []),
    }


def _host_meta(cfg: Config, ref: RepoRef) -> HostMeta:
    """查宿主元数据；关掉开关或查不到时返回一个「什么都没查到」的实例。

    返回值永远不是 None，调用方不必到处判空——「查不到」本身就是一种合法结果。
    """
    if not cfg.host_meta:
        return HostMeta(reason="MEMEX_HOST_META=off：身份按 owner/name 判定，未校验")
    return fetch_host_meta(cfg, ref)


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
        "merged_into": row.get("merged_into"),
        "aliases": json_loads(row.get("aliases_json"), []),
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
    is_fork: bool = False,
    fork_of: str | None = None,
    stars: int | None = None,
    license: str | None = None,
    description: str | None = None,
    merged_into: str | None = None,
) -> None:
    """写入或更新 repos 行。

    **别名 / fork 关系是只增不减的**（G10）：旧名的轨迹一旦记下就不该被下一次 fetch 抹掉，
    所以 aliases_json 传 None 表示「沿用库里已有的」，只有显式传列表才覆盖。
    fork_of / is_fork / stars / license / description 则是**快照**：宿主说有就写，
    宿主说「没查到」（None）就沿用旧值而不是清空——把「不知道」写成「没有」是假信号。

    language 每次按克隆目录重新统计（换 embedder 或 reindex 后仍与真实内容一致）。
    """
    existing = conn.execute(
        "SELECT aliases_json, is_fork, fork_of, stars, license, description, merged_into "
        "FROM repos WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if aliases is None:
        aliases = json_loads(existing["aliases_json"] if existing else None, [])
    # 新值为 None（宿主这次没给）就保留库里已知的，只有宿主明确说了新值才覆盖。
    # 代价：仓库若真的从 fork 变回上游，本函数不会自动把 fork_of 清空——
    # 这比反向误判（把上游当成 fork 去重掉）安全得多，宁可留旧值让人看见。
    fork_of = fork_of if fork_of is not None else (existing["fork_of"] if existing else None)
    stars = stars if stars is not None else (existing["stars"] if existing else None)
    license = license if license is not None else (existing["license"] if existing else None)
    description = (
        description if description is not None else (existing["description"] if existing else None)
    )
    merged_into = merged_into if merged_into is not None else (
        existing["merged_into"] if existing else None
    )
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, default_branch, language, stars, license, "
        "description, subpath, identity_key, aliases_json, fork_of, is_fork, merged_into, source, "
        "is_stale, head_sha, cloned_at, repo_path, is_local) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(repo_id) DO UPDATE SET "
        "full_name=excluded.full_name, url=excluded.url, host=excluded.host, "
        "default_branch=excluded.default_branch, subpath=excluded.subpath, "
        "language=excluded.language, identity_key=excluded.identity_key, source=excluded.source, "
        "is_stale=excluded.is_stale, head_sha=excluded.head_sha, "
        "cloned_at=excluded.cloned_at, repo_path=excluded.repo_path, aliases_json=excluded.aliases_json, "
        "stars=excluded.stars, license=excluded.license, description=excluded.description, "
        "is_fork=excluded.is_fork, fork_of=excluded.fork_of, merged_into=excluded.merged_into",
        (
            repo_id, full_name, url, host, default_branch, language, stars, license,
            description, subpath, identity_key, json_dumps(aliases), fork_of, int(is_fork),
            merged_into, source, int(is_stale), head_sha, utcnow(), repo_path, int(is_local),
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
    meta = _host_meta(cfg, ref)
    identity_key, degraded = meta.identity_key(ref)
    warnings: list[str] = [meta.reason] if degraded and meta.reason else []
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

        with _clone_gate(cfg):
            result = gitutil.clone(cfg, ref, path, ref_name=ref_name)

        stale = False
        is_new = existing is None
        if existing is not None and existing.get("head_sha") and existing["head_sha"] != result.head_sha:
            stale = True  # refresh 发现新 head：已有分析标记 stale，绝不自动重分析

        # 改名/转移：identity_key 命中了另一个 repo_id，说明是同一个仓换了名字。
        # 沿用既有句柄，目录/chunks/cards 全部不动（G10：历史分析一律保留）。
        owner = conn.execute(
            "SELECT repo_id, full_name, head_sha, aliases_json FROM repos " 
            "WHERE identity_key = ? AND repo_id <> ?",
            (identity_key, repo_id),
        ).fetchone()
        if owner is not None and not degraded:
            return _merge_renamed(
                conn,
                cfg,
                owner=owner,
                ref=ref,
                identity_key=identity_key,
                head_sha=result.head_sha,
                language=detect_language(result.repo_path),
                meta=meta,
            )
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
            is_fork=bool(meta.fork),
            fork_of=meta.fork_of,
            stars=meta.stars,
            license=meta.license,
            description=meta.description,
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


def _download_bundle(cfg: Config, url: str) -> Path:
    """T17 远程变体：把 agent 给的 bundle_url 拉到本地临时文件。

    两处刻意的取舍：

    - **边下边数**：不能先落盘再查大小——一个超大响应会先把磁盘写满再报错。
      每读一块即累加，超 cfg.max_bundle_bytes 立刻中止并删残片。
    - **只接受 http(s)**：file:// 会被 urllib 当成读本地文件，那等于给远程 agent
      一个「读服务器任意路径」的入口（T17 是离线/私有仓的补口，不是本地读文件器），直接拒。
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise MemexError("invalid_argument", "bundle_url 只接受 http(s) 直链", {"bundle_url": url})
    limit = max(1, int(cfg.max_bundle_bytes))
    fd, tmp = tempfile.mkstemp(prefix="memex-bundle-", suffix=".bundle")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "memex"})
        with urllib.request.urlopen(req, timeout=120) as fh:  # noqa: S310 (调用方给的直链)
            total = 0
            with tmp_path.open("wb") as out:
                while True:
                    block = fh.read(1 << 20)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise MemexError(
                            "invalid_argument",
                            "bundle 超过内存上限",
                            {"bytes": total, "max_bundle_bytes": limit},
                        )
                    out.write(block)
        return tmp_path
    except MemexError:
        tmp_path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise MemexError(
            "invalid_argument", "bundle_url 下载失败", {"bundle_url": url, "reason": str(exc)}
        ) from exc


def upload_repo_bundle(
    cfg: Config,
    bundle_path: str | None,
    repo_url: str,
    *,
    bundle_url: str | None = None,
    ref: str | None = None,
    subpath: str | None = None,
    sha256: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """T17 入口：两种投喂方式二选一（deployment.md A2/D3）。

    - bundle_path：**仅本地形态**——server 与 agent 同机，给服务器上的路径。
    - bundle_url：**远程形态主路径**——agent 在有凭据的一端 git bundle 后传到自己的
      对象存储（预签名 OSS/S3 直链），server 匿名 GET 拉回。远程形态下 agent 手里没有
      服务器路径，bundle_path 送不进来，这正是补 bundle_url 的原因。

    选定的文件交给 _ingest_bundle 走 verify -> clone -> 登记。
    """
    if (bundle_path is None) == (bundle_url is None):
        raise MemexError(
            "invalid_argument",
            "bundle_path 与 bundle_url 必须二选一",
            {"bundle_path": bundle_path, "bundle_url": bundle_url},
        )
    if bundle_path is not None and cfg.is_http:
        raise MemexError(
            "invalid_argument",
            "远程形态不接受 bundle_path（那是服务器本地路径）；请改用 bundle_url",
            {"hint": "bundle_url"},
        )
    downloaded: Path | None = None
    try:
        if bundle_url is not None:
            downloaded = _download_bundle(cfg, bundle_url)
            local = str(downloaded)
        else:
            assert bundle_path is not None
            local = bundle_path
        return _ingest_bundle(
            cfg, local, repo_url, ref=ref, subpath=subpath, sha256=sha256, conn=conn
        )
    finally:
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)


def _ingest_bundle(
    cfg: Config,
    bundle_path: str,
    repo_url: str,
    *,
    ref: str | None = None,
    subpath: str | None = None,
    sha256: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """T17 的后半段（对本地文件）：verify -> clone -> 登记；source='upload'（§1.5）。

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
        meta = _host_meta(cfg, parsed)
        identity_key, degraded = meta.identity_key(parsed)
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
            is_fork=bool(meta.fork),
            fork_of=meta.fork_of,
            stars=meta.stars,
            license=meta.license,
            description=meta.description,
        )
        row = get_repo(conn, repo_id)
        assert row is not None
        return {
            "repo": repo_summary(row),
            "commit_sha": result.head_sha,
            "bytes": size,
            "repo_path": result.repo_path if cfg.allow_local_paths else None,
            "is_new": True,
            "warnings": [meta.reason] if degraded and meta.reason else [],
        }
    finally:
        if owns:
            conn.close()
