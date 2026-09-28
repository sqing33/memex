"""仓库打包下发（T4 request_repo_bundle，G18）。

把本地克隆打成 git bundle，供 agent 取用。两种形态：

- **远程形态**（``cfg.public_base_url`` 已配置）：url 是带 HMAC 短时签名的下载票据
  ``{public_base_url}/bundles/{repo_id}?commit=..&exp=..&sig=..``。
  **票据即凭证**——agent 的 ``git clone`` / curl 不便带 Authorization 头，
  所以校验靠签名而不是 Bearer（HMAC 密钥就是 MEMEX_TOKEN，换 token 即轮换全部在途票据，
  不需要为票据单开一个 secret）。
- **本地形态**（无 base_url）：url 仍是 ``file://`` 绝对路径。

两种形态都**不删产物**：下载后删文件会破坏网络中断后的重试（deployment.md §10）。
有界性靠 LRU：保留最近 20 个，且总量超 ``cfg.max_bundle_bytes`` 时删最老。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from pathlib import Path
from typing import Any

from ..core import Config, MemexError, Paths
from ..fsutil import sha256_file
from ..store.db import connect
from . import gitutil
from .repo import get_repo

# 默认票据有效期（秒）；实参优先取 cfg.bundle_ttl_seconds。
BUNDLE_TTL_SECONDS = 3600
# LRU 保留个数上限。
BUNDLE_KEEP = 20
# 签名十六进制截断长度（deployment.md §4.2）。
_SIG_HEX = 32


_SAFE_REPO_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def repo_id_is_safe(repo_id: str) -> bool:
    """repo_id 只允许 [A-Za-z0-9._-]——它会被拼进文件路径与 URL。

    没有这道闸，``/bundles/../../etc/passwd`` 这类请求就会穿过
    ``bundle_path_for`` 去读盘（该函数只做拼接，不做校验）。
    这里拒绝 ``..``、``/``、反斜杠 与空串，比黑名单更稳。
    """
    return bool(repo_id) and bool(_SAFE_REPO_ID.match(repo_id)) and ".." not in repo_id


def _ticket_payload(repo_id: str, commit_sha: str, exp: int) -> str:
    """票据签名原文。换任何字段都会让签名失配。"""
    return f"{repo_id}\n{commit_sha}\n{exp}"


def sign_ticket(cfg: Config, repo_id: str, commit_sha: str, exp: int) -> str:
    """算票据签名。没有 token 就不签——远程缺 token 本就该拒启（startup.py）。"""
    if not cfg.token:
        return ""
    mac = hmac.new(
        cfg.token.encode("utf-8"),
        _ticket_payload(repo_id, commit_sha, exp).encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()[:_SIG_HEX]


def verify_ticket(cfg: Config, repo_id: str, commit_sha: str, exp: str, sig: str) -> bool:
    """校验下载票据。**先比 exp 再比 sig**：签名对但已过期也要拒。

    返回 False 的含义是「别再试了」，所以调用方一律回 403，不区分原因——
    区分开等于把「这个 bundle 存在」泄露给未持有票据的一方。
    """
    if not cfg.token or not sig or not exp:
        return False
    try:
        exp_i = int(exp)
    except ValueError:
        return False
    if exp_i <= int(time.time()):
        return False
    return hmac.compare_digest(sign_ticket(cfg, repo_id, commit_sha, exp_i), sig)


def bundle_path_for(cfg: Config, repo_id: str, commit_sha: str) -> Path:
    """bundle 产物路径。按 repo_id + commit 前 12 位命名，天然可按 ref 复用。"""
    return Paths(cfg.home).bundles / f"{repo_id}.{commit_sha[:12]}.bundle"


def prune_bundles(cfg: Config) -> list[str]:
    """LRU 清理：只留最近 BUNDLE_KEEP 个，且总量不超 cfg.max_bundle_bytes。

    返回被删掉的文件名（测试用）。
    """
    d = Paths(cfg.home).bundles
    if not d.is_dir():
        return []
    try:
        items = [p for p in d.iterdir() if p.is_file() and p.name.endswith(".bundle")]
    except OSError:
        return []
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # 新→旧
    keep, total, dropped = items[:BUNDLE_KEEP], 0, items[BUNDLE_KEEP:]
    for p in keep:
        total += p.stat().st_size
    # 总量超限就从最老的开始丢
    while total > cfg.max_bundle_bytes and keep:
        victim = keep.pop()
        total -= victim.stat().st_size
        dropped.append(victim)
    removed: list[str] = []
    for p in dropped:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def _remote_url(cfg: Config, repo_id: str, commit_sha: str, exp: int) -> str | None:
    base = (cfg.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    sig = sign_ticket(cfg, repo_id, commit_sha, exp)
    if not sig:  # 远程没 token：签不出票据，宁可不给 URL 也不给一个必然 403 的
        return None
    from urllib.parse import quote

    # 注意：3.11 的 f-string 不能内嵌同型引号（PEP 701 要 3.12），故先转义再拼。
    rid = quote(repo_id, safe="")
    sha = quote(commit_sha, safe="")
    return f"{base}/bundles/{rid}?commit={sha}&exp={exp}&sig={sig}"


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
    out_dir = Paths(cfg.home).bundles
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_path_for(cfg, repo_id, commit_sha)
    # 同 ref 复用（B5）：路径含 commit 前 12 位，命中即说明这个 ref 打过包了。
    # 重新 bundle create 在大仓上要几十秒，而 agent 常常对同一个 HEAD 反复要。
    if not bundle_path.is_file():
        proc = gitutil.run_git(
            ["bundle", "create", str(bundle_path), "--all"], cwd=repo_path, timeout=cfg.clone_timeout
        )
        if proc.returncode != 0 or not bundle_path.is_file():
            raise MemexError(
                "internal", "生成 bundle 失败", {"stderr": proc.stderr.strip()[:500], "repo_id": repo_id}
            )
        prune_bundles(cfg)

    size = bundle_path.stat().st_size
    ttl = cfg.bundle_ttl_seconds or BUNDLE_TTL_SECONDS
    exp = int(time.time()) + ttl
    url = _remote_url(cfg, repo_id, commit_sha, exp)
    if url is None:
        url = "file://" + str(bundle_path)
    return {
        "url": url,
        "sha256": sha256_file(bundle_path),
        "commit_sha": commit_sha,
        "bytes": size,
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)),
        "usage": "git clone <url> <dir>  然后 git checkout " + commit_sha,
    }