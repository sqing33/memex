"""从 MEMEX_HOME 里定位真实克隆的 axum 仓库，给需要真仓库文件的测试用。

硬编码 `/root/.memex/repos/github.com__tokio-rs__axum` 是不行的：真库重建后
克隆目录名取决于当时怎么 clone（我用短名 `axum` 克隆过），路径一变测试就
假失败——它报的 `bad_evidence_path` 是「我找不到仓库」，不是「报告不合法」。

找不到就 pytest.skip 显式跳过：静默 pass 会让人以为这组断言跑过了，
实际上一行都没跑，性质和当初 members_json 静默变 {} 一样。
"""
from __future__ import annotations

import os
from pathlib import Path


def axum_repo_root() -> Path | None:
    """返回真实 axum 克隆目录；不在就返回 None，让调用方 skip。"""
    # 注意：同进程里别的测试会用 os.environ["MEMEX_HOME"] = tmp_path 改掉这个变量
    # 且不还原（全仓 9 个文件都这么写），所以只信 MEMEX_HOME 会把自己判成「没克隆」。
    # 因此这里扫多个候选根目录，找到第一个真有 axum 克隆的。
    candidates: list[Path] = []
    env_home = os.environ.get("MEMEX_HOME")
    if env_home:
        candidates.append(Path(env_home))
    candidates.append(Path("/root/.memex"))
    seen: set[Path] = set()
    for home in candidates:
        if home in seen:
            continue
        seen.add(home)
        repos = home / "repos"
        for name in ("github.com__tokio-rs__axum", "axum", "tokio-rs__axum"):
            p = repos / name
            if (p / "axum-core").is_dir() and (p / "axum").is_dir():
                return p
    return None


def require_axum_repo() -> str:
    """拿不到就显式跳过，不要让「环境缺失」伪装成「校验失败」。"""
    import pytest

    p = axum_repo_root()
    if p is None:
        pytest.skip("MEMEX_HOME 与 /root/.memex 下都没有 axum 克隆，跳过需要真实仓库文件的测试")
    return str(p)
