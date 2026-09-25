"""共享文件系统工具：语言识别、忽略清单、树遍历、哈希。

放在顶层是因为证据层（evidence/）与抓取层（fetch/）都要用：证据包要遍历
目录、识别语言；抓取层要落盘校验、算 file_sha。两处共用一份表，
避免「同一文件在两处被判定成不同语言」这类静默不一致。

约定：所有路径参数都是「相对仓库根」的 POSIX 风格路径，分隔符为 '/'。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

# —— 语言识别：扩展名 -> 语言码（G13 / tech-design §2.4）——
LANG_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".swift": "swift", ".m": "objc", ".mm": "objc",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell",
    ".lua": "lua", ".pl": "perl", ".r": "r", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".clj": "clojure", ".hs": "haskell", ".sql": "sql",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
    ".md": "markdown", ".rst": "markdown", ".txt": "text", ".xml": "xml", ".proto": "proto",
}

# 视为「代码」的语言（再往里留空行/注释做行存在性校验时，只有代码文件才配）
CODE_LANGS: frozenset[str] = frozenset({
    "python", "javascript", "typescript", "go", "rust", "java", "kotlin", "scala",
    "ruby", "php", "csharp", "c", "cpp", "swift", "objc", "shell", "lua", "perl",
    "r", "dart", "elixir", "erlang", "clojure", "haskell", "sql",
})

# 二进制扩展名（含在忽略清单里，永不进证据包）
BINARY_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff", ".svgz",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".class", ".pyc", ".pyo", ".so", ".dylib", ".dll", ".a", ".o", ".obj", ".bin",
    ".exe", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4",
    ".mov", ".avi", ".webm", ".ogg", ".wav", ".flac", ".psd", ".ai", ".sketch",
    ".db", ".sqlite", ".sqlite3", ".pack", ".idx", ".DS_Store",
})


def detect_language(path: str) -> str:
    """按扩展名识别语言；未知返回 "other"，无扩展名返回 "other"。"""
    name = Path(path).name
    if name in {"Makefile", "Dockerfile", "makefile"}:
        return "shell"
    suffix = Path(name).suffix.lower()
    return LANG_BY_EXT.get(suffix, "other")


def is_code_file(path: str) -> bool:
    return detect_language(path) in CODE_LANGS


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_EXTS


# —— 固定忽略清单（operations.md）：目录 ∪ 名字 ∪ 后缀 ——
IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "vendor", "third_party", "deps",
    "dist", "build", "target", "out", ".next", ".nuxt", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".idea", ".vscode", "coverage", ".cache",
    ".pytest_cache", ".ruff_cache", ".gradle", ".terraform",
})

IGNORE_SUFFIXES: frozenset[str] = frozenset({
    ".min.js", ".min.css", ".lock",
})

IGNORE_NAMES: frozenset[str] = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "composer.lock", "Cargo.lock", "go.sum", ".DS_Store",
})


def _glob_body(pattern: str) -> str:
    """把 gitignore 的 glob 片段转成正则片段（不含 ^ $ 锚）。支持 ** * ? []。"""
    i = 0
    out: list[str] = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** 跨目录：**/ -> 任意层级；尾随 ** -> 任意后缀
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
                i += 1
            else:
                inner = pattern[i + 1:j].replace("\\", "\\\\")
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append("[" + inner + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    negate: bool
    dir_only: bool


class GitIgnore:
    """极简 .gitignore 匹配器（尊重但不盲信；G16）。

    支持：通配 * ? ** []、否定 !、目录后缀 /、根锚定 /。规则尾置于「整路径」
    匹配：未锚定的模式默认允许出现在任意层级（前缀 (?:.*/)?）。目录规则额外
    允许其后跟 /任意内容，这样 gitignore 命中 build/ 時 build/a.py 一起被忽略。
    不支持的复杂语法退化为「按基名匹配」——宁可多忽略，也不放行可疑路径。
    """

    def __init__(self, text: str = "") -> None:
        self._rules: list[_Rule] = []
        for raw in text.splitlines():
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            line = line.replace("\\ ", " ")
            line = line.rstrip()
            if not line:
                continue
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            # 根锚定：以 / 开头，或（严格 git 语义）模式中含 / 时按根相对。
            anchored = line.startswith("/") or ("/" in line)
            if line.startswith("/"):
                line = line[1:]
            prefix = "" if anchored else "(?:.*/)?"
            tail = "(?:/.*)?$" if dir_only else "$"
            regex = re.compile("^" + prefix + _glob_body(line) + tail)
            self._rules.append(_Rule(regex, negate, dir_only))

    def match(self, rel_path: str) -> bool:
        """返回该路径是否被忽略（后出现的规则覆盖先出现的，与 git 一致）。"""
        ignored = False
        for rule in self._rules:
            if rule.pattern.match(rel_path):
                ignored = not rule.negate
        return ignored


def load_gitignore(root: str | os.PathLike[str]) -> GitIgnore:
    p = Path(root) / ".gitignore"
    if not p.is_file():
        return GitIgnore("")
    try:
        return GitIgnore(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return GitIgnore("")


def is_ignored(rel_path: str, gitignore: GitIgnore | None = None) -> bool:
    """固定清单 ∪ .gitignore（G16）。rel_path 用 '/' 分隔。"""
    parts = rel_path.split("/")
    if any(part in IGNORE_DIRS for part in parts[:-1]):
        return True
    name = parts[-1] if parts else rel_path
    if name in IGNORE_NAMES or name in IGNORE_DIRS:
        return True
    lower = name.lower()
    if any(lower.endswith(sfx) for sfx in IGNORE_SUFFIXES):
        return True
    if is_binary_path(name):
        return True
    if gitignore is not None and gitignore.match(rel_path):
        return True
    return False


@dataclass(frozen=True)
class WalkItem:
    """一条可入证据包的文件记录。"""

    rel_path: str
    size: int
    language: str


def walk_files(
    root: str | os.PathLike[str],
    *,
    gitignore: GitIgnore | None = None,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
    subpath: str | None = None,
) -> tuple[list[WalkItem], bool]:
    """遍历仓库，返回 (文件列表, 是否因 max_files 截断)。

    跳过固定忽略清单与 .gitignore 命中的路径；按路径字典序稳定排序，
    保证同一仓库多次运行证据包顺序一致。超过 max_files 时截断并置位。
    """
    base = Path(root)
    if subpath:
        base = base / subpath
    items: list[WalkItem] = []
    truncated = False
    if not base.is_dir():
        return items, truncated
    stack: list[tuple[Path, str]] = [(base, "")]
    while stack:
        cur, rel_prefix = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            rel = f"{rel_prefix}{entry.name}" if not rel_prefix else f"{rel_prefix}/{entry.name}"
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in IGNORE_DIRS:
                        continue
                    if gitignore is not None and gitignore.match(rel):
                        continue
                    stack.append((Path(entry.path), rel))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if is_ignored(rel, gitignore):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
                if max_file_bytes is not None and size > max_file_bytes:
                    continue
            except OSError:
                continue
            items.append(WalkItem(rel_path=rel, size=size, language=detect_language(rel)))
            if max_files is not None and len(items) >= max_files:
                truncated = True
                return items, truncated
    items.sort(key=lambda x: x.rel_path)
    return items, truncated


# —— 哈希与读取 ——


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    """文件内容 sha256（十六进制小写）。文件不存在抛 FileNotFoundError。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_lines(path: str | os.PathLike[str]) -> list[str]:
    """按行切分，不含换行符；用 splitlines 与行号 1-based 对齐。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return text.splitlines()
