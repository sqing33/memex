"""符号抽取：分级解析（tech-design §2.4 / D3）。

- Python：ast 精确解析（tier1）。
- 其他语言：若装了 tree-sitter 则用（[analysis] extra）；否则退化为
  「行存在 + 非空 + 非注释」的正则启发式，并给符号打 syntax_unverified=True。

解析器只用于**校验**（校验 agent 给的代码切片是否落在真实符号上），
不用于生成知识——知识仍由 agent 写。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SYMBOL_KINDS: frozenset[str] = frozenset({
    "function", "class", "method", "interface", "type",
    "constant", "variable", "module", "enum", "struct", "trait", "signature",
})


@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    exported: bool
    lang: str
    signature: str = ""
    syntax_unverified: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "exported": self.exported,
            "lang": self.lang,
            "signature": self.signature,
        }


def _try_tree_sitter() -> object | None:
    """可选依赖：装了才用，绝不进核心零依赖路径。"""
    try:  # pragma: no cover - 环境相关
        import tree_sitter  # type: ignore

        return tree_sitter  # type: ignore[no-any-return]
    except Exception:
        return None


# —— Python：ast 精确解析 ——

def _py_signature(node: ast.AST, lines: list[str]) -> str:
    seg = ast.get_source_segment("\n".join(lines), node)
    if not seg:
        return ""
    head = seg.splitlines()[0].strip()
    return head[:200]


def extract_python(path: str, text: str) -> list[Symbol]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return []
    out: list[Symbol] = []

    def exported(name: str) -> bool:
        return not name.startswith("_")

    def visit(node: ast.AST, cls_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if cls_name else "function"
                out.append(Symbol(
                    name=child.name,
                    kind=kind,
                    path=path,
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    exported=exported(child.name),
                    lang="python",
                    signature=_py_signature(child, lines),
                ))
            elif isinstance(child, ast.ClassDef):
                out.append(Symbol(
                    name=child.name,
                    kind="class",
                    path=path,
                    start_line=child.lineno,
                    end_line=getattr(child, "end_lineno", child.lineno),
                    exported=exported(child.name),
                    lang="python",
                    signature=_py_signature(child, lines),
                ))
                visit(child, child.name)
            elif isinstance(child, ast.Assign) and cls_name is None:
                for tgt in child.targets:
                    if isinstance(tgt, ast.Name):
                        is_const = tgt.id.isupper()
                        out.append(Symbol(
                            name=tgt.id,
                            kind="constant" if is_const else "variable",
                            path=path,
                            start_line=child.lineno,
                            end_line=getattr(child, "end_lineno", child.lineno),
                            exported=exported(tgt.id),
                            lang="python",
                            signature=(tgt.id + " = ..."),
                        ))
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                continue

    visit(tree, None)
    return out


# —— 其他语言：正则启发式（line-exists 兜底）——

_DECL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()")),
    ("function", re.compile(r"^\s*(?:public|private|protected|static|async|\s)*func\s+\(?\s*[\w*]*\s*\)?\s*([A-Za-z_]\w*)\s*\(")),
    ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)\s*[(<]")),
    ("class", re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("class", re.compile(r"^\s*(?:public\s+|final\s+|abstract\s+)*class\s+([A-Za-z_]\w*)")),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("struct", re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)")),
    ("trait", re.compile(r"^\s*(?:pub\s+)?trait\s+([A-Za-z_]\w*)")),
    ("enum", re.compile(r"^\s*(?:pub\s+)?enum\s+([A-Za-z_]\w*)")),
    ("type", re.compile(r"^(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")),
    ("constant", re.compile(r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=")),
]

_COMMENT_PREFIXES = ("//", "#", "*", "/*", "--", ";;", "%")


def _is_meaty_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return not s.startswith(_COMMENT_PREFIXES)


def extract_heuristic(path: str, text: str, lang: str) -> list[Symbol]:
    """非 Python 语言的兜底抽取：逐行 match 声明正则，打 syntax_unverified。"""
    out: list[Symbol] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if not _is_meaty_line(line):
            continue
        for kind, pat in _DECL_PATTERNS:
            m = pat.match(line)
            if m:
                name = m.group(1)
                out.append(Symbol(
                    name=name,
                    kind=kind if kind in SYMBOL_KINDS else "function",
                    path=path,
                    start_line=i,
                    end_line=i,
                    exported=not name.startswith("_"),
                    lang=lang,
                    signature=line.strip()[:200],
                    syntax_unverified=True,
                ))
                break
    return out


def extract_symbols(path: str, text: str, lang: str) -> list[Symbol]:
    """按语言分派。Python 走 ast；其余走 tree-sitter（若有）或启发式。"""
    if lang == "python":
        return extract_python(path, text)
    return extract_heuristic(path, text, lang)


def source_segment(root: str | Path, path: str, start_line: int) -> str:
    """读取 root/path 的指定行（供校验用）；越界返回空串。"""
    p = Path(root) / path
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if 1 <= start_line <= len(lines):
        return lines[start_line - 1]
    return ""
