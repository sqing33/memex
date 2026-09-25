"""git 调用封装：认证注入、镜像改写、带重试的克隆（G4 / G17 / G22）。

只暴露给抓取层用。三条铁律：
1. 凭据只在 server 侧注入（MEMEX_GIT_TOKEN__<host> 或 credentials.json），
   绝不落盘、绝不走 MCP 参数（G4）。这里通过环境变量 GIT_CONFIG_* 注入
   http.extraheader，避免 token 出现在进程 argv 里。
2. 失败要显式区分可重试与不可重试：网络/5xx 才重试，404/403 直接放弃（G17）。
3. 镜像只做前缀改写（G22）；不改仓库身份（repo_id）。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import Config, RepoRef


class GitError(Exception):
    """git 调用失败；retryable 决定上层是否重试。"""

    def __init__(self, message: str, *, retryable: bool, returncode: int = -1, stderr: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class CloneResult:
    repo_path: str
    head_sha: str
    default_branch: str


_RETRYABLE_HTTP = re.compile(r"\b(429|500|502|503|504)\b")


def _is_retryable(stderr: str, returncode: int) -> bool:
    """网络故障或 5xx/429 可重试；404/403 等鉴权/缺失错误不重试（G17）。"""
    s = stderr.lower()
    if "could not resolve host" in s or "connection timed out" in s:
        return True
    if "connection reset" in s or "temporary failure in name resolution" in s:
        return True
    if "early eof" in s or "rpc failed" in s or "the remote end hung up" in s:
        return True
    if "could not read from remote repository" in s:
        return True
    if _RETRYABLE_HTTP.search(stderr):
        return True
    return False


def git_env(cfg: Config, host: str) -> dict[str, str]:
    """构造注入认证与镜像的 git 环境变量（token 不进 argv）。"""
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_ASKPASS", "true")
    token = cfg.git_tokens.get(host)
    if token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {token}"
    else:
        env.pop("GIT_CONFIG_COUNT", None)
        env.pop("GIT_CONFIG_KEY_0", None)
        env.pop("GIT_CONFIG_VALUE_0", None)
    return env


def run_git(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    """执行 git 子进程。超时抛 GitError(retryable=True)。"""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git {' '.join(args[:2])} 超时（{timeout}s）", retryable=True, stderr=str(exc)
        ) from exc
    except FileNotFoundError as exc:  # git 未安装
        raise GitError("未找到 git 可执行文件", retryable=False) from exc


def mirror_url(cfg: Config, ref: RepoRef) -> str:
    """按 MEMEX_GIT_MIRROR 改写克隆 URL（G22）。约定镜像按 host 分路径。"""
    if not cfg.git_mirror:
        return ref.url
    base = cfg.git_mirror.rstrip("/")
    return f"{base}/{ref.host}/{ref.owner}/{ref.name}.git"


def clone(
    cfg: Config,
    ref: RepoRef,
    dest: str | os.PathLike[str],
    *,
    ref_name: str | None = None,
) -> CloneResult:
    """克隆到 dest（先克隆到临时目录再原子切换，G17）。

    ref_name 为 None 时用远端默认分支。返回 head_sha / default_branch。
    """
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.parent / (dest_path.name + ".tmp")
    if tmp.exists():
        _rmtree(tmp)
    url = mirror_url(cfg, ref)
    env = git_env(cfg, ref.host)
    last: GitError | None = None
    for attempt in range(3):
        proc = run_git(
            ["clone", "--quiet", "--no-tags", url, str(tmp)],
            env=env,
            timeout=cfg.clone_timeout,
        )
        if proc.returncode == 0:
            break
        last = GitError(
            f"克隆失败：{ref.full_name}",
            retryable=_is_retryable(proc.stderr, proc.returncode),
            returncode=proc.returncode,
            stderr=proc.stderr.strip(),
        )
        if not last.retryable:
            raise last
        if tmp.exists():
            _rmtree(tmp)
        if attempt < 2:
            time.sleep(2**attempt)
    else:
        assert last is not None
        raise last

    if ref_name:
        co = run_git(["checkout", "--quiet", ref_name], cwd=tmp, env=env, timeout=cfg.clone_timeout)
        if co.returncode != 0:
            _rmtree(tmp)
            raise GitError(
                f"检出 ref 失败：{ref_name}",
                retryable=False,
                returncode=co.returncode,
                stderr=co.stderr.strip(),
            )

    head_sha = rev_parse(tmp, "HEAD")
    default_branch = _default_branch(tmp, ref_name)
    if dest_path.exists():
        _rmtree(dest_path)
    tmp.rename(dest_path)
    return CloneResult(repo_path=str(dest_path), head_sha=head_sha, default_branch=default_branch)


def clone_from_bundle(
    cfg: Config, bundle_path: str | os.PathLike[str], dest: str | os.PathLike[str]
) -> CloneResult:
    """从本地 git bundle 克隆（uplink 路径，T17）。"""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.parent / (dest_path.name + ".tmp")
    if tmp.exists():
        _rmtree(tmp)
    proc = run_git(["clone", "--quiet", str(bundle_path), str(tmp)], timeout=cfg.clone_timeout)
    if proc.returncode != 0:
        raise GitError(
            "从 bundle 克隆失败",
            retryable=False,
            returncode=proc.returncode,
            stderr=proc.stderr.strip(),
        )
    head_sha = rev_parse(tmp, "HEAD")
    if dest_path.exists():
        _rmtree(dest_path)
    tmp.rename(dest_path)
    return CloneResult(repo_path=str(dest_path), head_sha=head_sha, default_branch="HEAD")


def verify_bundle(bundle_path: str | os.PathLike[str]) -> bool:
    """git bundle verify（T17 前置校验）。"""
    p = Path(bundle_path)
    if not p.is_file():
        return False
    proc = run_git(["bundle", "verify", str(p)], timeout=60)
    return proc.returncode == 0


def rev_parse(repo_path: str | os.PathLike[str], rev: str = "HEAD") -> str:
    proc = run_git(["rev-parse", rev], cwd=repo_path, timeout=60)
    if proc.returncode != 0:
        raise GitError(
            f"rev-parse {rev} 失败",
            retryable=False,
            returncode=proc.returncode,
            stderr=proc.stderr.strip(),
        )
    return proc.stdout.strip()


def _default_branch(repo_path: Path, ref_name: str | None) -> str:
    if ref_name:
        return ref_name
    proc = run_git(["symbolic-ref", "--short", "HEAD"], cwd=repo_path, timeout=60)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "HEAD"


def _rmtree(path: Path) -> None:
    """删除目录树（忽略只读位，兼容 Windows 语义）。"""
    import shutil

    shutil.rmtree(path, ignore_errors=True)
