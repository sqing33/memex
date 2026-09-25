"""深度档位与全局上限（G5）。

三个深度 fast/standard/deep 决定遍历文件数、README 截断、最小功能数、符号上限。
全局硬上限（文件 ≤1MiB、bundle ≤512MiB、仓库 ≤2GiB）与深度无关。
超限一律「截断 + 打标」，绝不因为大就整体拒绝（G5）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthProfile:
    depth: str
    max_files: int
    max_readme: int
    min_features: int
    symbol_cap: int


DEPTH_PROFILES: dict[str, DepthProfile] = {
    "fast": DepthProfile("fast", max_files=400, max_readme=3000, min_features=3, symbol_cap=500),
    "standard": DepthProfile("standard", max_files=2000, max_readme=6000, min_features=3, symbol_cap=2000),
    "deep": DepthProfile("deep", max_files=4000, max_readme=12000, min_features=5, symbol_cap=8000),
}

DEFAULT_DEPTH = "standard"

# 全局上限（字节）
MAX_FILE_BYTES = 1024 * 1024            # 1 MiB
MAX_BUNDLE_BYTES = 512 * 1024 * 1024    # 512 MiB
MAX_REPO_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# read_file_slice（T3）
SLICE_MAX_LINES_DEFAULT = 400
SLICE_MAX_LINES_HARD = 2000

READMES = ("README.md", "README.rst", "README.txt", "readme.md", "Readme.md")


def depth_profile(depth: str) -> DepthProfile:
    if depth not in DEPTH_PROFILES:
        raise KeyError(depth)
    return DEPTH_PROFILES[depth]
