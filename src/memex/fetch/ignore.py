"""抓取层的忽略清单适配：直接复用顶层 fsutil（单一真源）。

原先计划在 fetch/ 与 evidence/ 各放一份，后合并到 memex.fsutil，避免两处
对「哪些文件算数」各判一套。本模块只做入口转发，方便抓取层 import 路径稳定。
"""

from __future__ import annotations

from ..fsutil import (  # noqa: F401
    IGNORE_DIRS,
    IGNORE_NAMES,
    IGNORE_SUFFIXES,
    GitIgnore,
    detect_language,
    is_binary_path,
    is_code_file,
    is_ignored,
    load_gitignore,
    walk_files,
)

__all__ = [
    "IGNORE_DIRS",
    "IGNORE_NAMES",
    "IGNORE_SUFFIXES",
    "GitIgnore",
    "detect_language",
    "is_binary_path",
    "is_code_file",
    "is_ignored",
    "load_gitignore",
    "walk_files",
]
