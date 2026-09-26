"""主语言检测：数克隆目录里的文件后缀，不联网、不引第三方库。

规则见 docs/tech-design.md §4.6.1「仓库元数据的来源边界」：
- 只统计占比 >= 1% 的语言，取文件数最多者；平局按语言名字典序（保证可复现）。
- 跳过 .git、构建产物与常见 vendored 目录——vendored 依赖不该把宿主仓的主语言带偏，
  这与 patterns 的 min_repos 按 source_group 判独立来源是同一个动机。
- 认不出 / 空仓 / 目录不存在一律返回 None（留空，不猜）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 后缀 -> 语言。只收真正能定位实现语言的扩展名；README.md 之类不收。
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".m": "objective-c",
    ".mm": "objective-c",
    ".sh": "shell",
    ".bash": "shell",
    ".lua": "lua",
    ".pl": "perl",
    ".r": "r",
    ".ex": "elixir",
    ".exs": "elixir",
    ".dart": "dart",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".clj": "clojure",
    ".zig": "zig",
    ".nim": "nim",
    ".vue": "vue",
    ".svelte": "svelte",
}

# 目录名 -> True 表示整棵子树跳过。
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        # 构建产物
        "node_modules", "target", "build", "dist", "out", ".next", ".nuxt",
        "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
        ".gradle", ".idea", ".vscode", "vendor", "third_party", "thirdparty",
        "Pods", "Carthage", "deps", "_build", "elm-stuff", ".terraform",
    }
)

# 占比下限：低于它的语言不参与定案（避免单个 vendored 残留文件左右结果）。
_MIN_SHARE = 0.01

# 遍历上限：巨型仓（monorepo / 带历史二进制）不至于把一次 fetch 拖死。
_MAX_FILES = 20000


def _scan(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            lang = _EXT_LANG.get(Path(fn).suffix.lower())
            if lang is None:
                continue
            counts[lang] = counts.get(lang, 0) + 1
            seen += 1
            if seen >= _MAX_FILES:
                return counts
    return counts


def detect_language(repo_path: str | os.PathLike[str]) -> str | None:
    """返回仓库主语言的小写名；判断不了就返回 None（不猜）。"""
    root = Path(repo_path)
    if not root.is_dir():
        return None
    counts = _scan(root)
    if not counts:
        return None
    total = sum(counts.values())
    eligible = [(n, c) for n, c in counts.items() if c / total >= _MIN_SHARE]
    if not eligible:
        return None
    # 数量优先；数量相同按名字字典序，保证同一仓每次结果一致（reindex 不会来回翻）。
    best = sorted(eligible, key=lambda kv: (-kv[1], kv[0]))[0]
    return best[0]
