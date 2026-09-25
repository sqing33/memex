"""抓取层：git 封装、忽略清单、仓库登记、bundle 下发（T1 / T4 / T17）。"""

from __future__ import annotations

from .bundle import request_repo_bundle
from .gitutil import GitError
from .repo import (
    ensure_repo,
    fetch_repo,
    get_repo,
    repo_summary,
    upload_repo_bundle,
)

__all__ = [
    "GitError",
    "ensure_repo",
    "fetch_repo",
    "get_repo",
    "repo_summary",
    "request_repo_bundle",
    "upload_repo_bundle",
]
