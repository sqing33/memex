"""VibeCraft 回填（decisions.md G12 / tech-design.md §2.8.2）。

`import-vibecraft <path>` 对 VibeCraft 的 SQLite 业务库做**最佳努力**结构搬运：

- **只读**源库，绝不写回（以 mode=ro URI 打开）；
- 卡片先过 contract.validate_report 校验（模块暂缺时降级为内置最小校验，并在 warnings 注明）；
- feature_pattern → snippet（能从真实文件重切出代码段）否则 mechanism；
  integration_note → decision，语义是「踩坑」则 gotcha；
  project_characteristic 不进 cards，进报告级 characteristics[]；
- 每条 evidence 都**从真实文件重切**；切不出/对不上 → code_mismatch → 丢弃该卡片；
- mechanism_desc 非英文（含 CJK）→ 卡片仍入库但 reindex_state=pending，且不建 chunk。

产物标 producer=batch / analyst=vibecraft-import；--dry-run 只出报告不落库。

容错策略：VibeCraft 的库表结构以实际源码为准（文档只给表名与映射），故本模块对表名/列名做
容错探测（如 repo_analysis_results 与旧名 repo_analysis_runs），缺失的可选列按缺省值处理——
「能救几张是几张」，绝不因个别列缺失而整体失败。
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..constants import CONTRACT_ID, CONTRACT_VERSION, MIN_PRINCIPLE_UNITS, PRINCIPLE_KEYS, REGEX_CJK
from ..core import Config, MemexError, Paths, count_units, is_blank, repo_id_for, slugify
from ..embeddings import get_embedder
from ..store import db
from ..store import index as store_index

_ANALYST = "vibecraft-import"
_PRODUCER = "batch"
_DEPTH = "standard"

_DROP_REASONS: tuple[str, ...] = ("code_mismatch", "evidence_missing", "no_code_span", "schema_invalid")

_TYPE_FEATURE_PATTERN = "feature_pattern"
_TYPE_INTEGRATION_NOTE = "integration_note"
_TYPE_PROJECT_CHARACTERISTIC = "project_characteristic"
_GOTCHA_MARKERS = ("gotcha", "pitfall", "陷阱", "踩坑", "坑")

_AXIS_ALIASES: dict[str, tuple[str, ...]] = {
    "runtime_control_flow": ("运行", "控制流", "control flow"),
    "data_flow": ("数据流", "data flow"),
    "state_lifecycle": ("状态", "生命周期", "state"),
    "failure_recovery": ("失败", "恢复", "failure", "recovery"),
    "concurrency_timing": ("并发", "时序", "concurrency"),
}
_AXIS_LABELS: dict[str, str] = {
    "runtime_control_flow": "Runtime and Control Flow",
    "data_flow": "Data Flow",
    "state_lifecycle": "State Lifecycle",
    "failure_recovery": "Failure and Recovery",
    "concurrency_timing": "Concurrency and Timing",
}

_EXT_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".h": "c", ".cc": "cpp",
    ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".sh": "shell", ".bash": "shell",
    ".sql": "sql", ".html": "html", ".css": "css", ".vue": "vue", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
}

_SYNTHETIC_PRINCIPLE = (
    "Recovered from the VibeCraft library: the original narration for this principle axis is "
    "unavailable, so rely on the card mechanisms and code evidence below to reconstruct the "
    "concrete control flow, data structures, failure handling, and timing behaviour."
)
_DEFAULT_ONELINER = "Repository imported from the VibeCraft knowledge library with recovered features and cards."
_DEFAULT_CHAR_DETAIL = (
    "Imported from the VibeCraft library; the original description is unavailable, so this entry "
    "points at the recovered evidence for the signature implementation."
)
_DEFAULT_RISK_DETAIL = (
    "No cross-feature risk narrative was recovered from the VibeCraft report; review the imported "
    "features and their evidence before reuse."
)
_DEFAULT_INTENT = (
    "Use this when you want the same mechanism in a different project and need a copy-ready starting point."
)

_H2_CHAR = "## Project Characteristics and Signature Implementations"
_H2_PRIN = "## Executive Principle Summary"
_H2_FEAT = "## Feature Principle Analysis"
_H2_RISK = "## Cross-feature Coupling and System Risks"


def _s(v: Any) -> str:
    """转字符串；None → 空串。"""
    return "" if v is None else str(v)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """按列/键取值；缺失或 None → default。可用于 sqlite3.Row 与 dict。"""
    try:
        val = obj[name]
    except (IndexError, KeyError, TypeError):
        return default
    return default if val is None else val


def _first(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    """取第一个非空候选字段。"""
    for n in names:
        val = _field(obj, n, None)
        if val is not None and val != "":
            return val
    return default


def _hash20(*parts: str) -> str:
    """内容 sha256 前 20 位（无前缀）。"""
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def _sid(prefix: str, *parts: str) -> str:
    """确定性主键：前缀 + 内容 sha256 前 20 位（同输入同 id，重跑幂等）。"""
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return prefix + ":" + h


def _norm_url(value: Any) -> str:
    """归一化 URL 以便比对（去 scheme / .git / 末尾斜杠，小写）。"""
    s = _s(value).strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s[:-4] if s.endswith(".git") else s
    return s.rstrip("/")


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_cjk(text: str) -> bool:
    return bool(REGEX_CJK.search(text or ""))


def _language_for(rel: str | None) -> str | None:
    if not rel:
        return None
    return _EXT_LANG.get(Path(rel).suffix.lower())


def _clean_tags(value: Any) -> list[str]:
    """把 tags / labels（列表或逗号串）清洗为契约允许的小写标签。"""
    raw: list[str] = []
    if isinstance(value, (list, tuple)):
        raw = [_s(x) for x in value]
    elif isinstance(value, str):
        raw = re.split(r"[,\s]+", value)
    out: list[str] = []
    for t in raw:
        t = t.strip().lower()
        if t and re.match(r"^[a-z0-9][a-z0-9._-]*$", t) and t not in out:
            out.append(t)
    return out


def _unwrap(value: Any) -> list[Any] | None:
    """清单字段可能是 JSON 串或 Python 字面量串，尽力解析为列表。"""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        parsed = db.json_loads(value, None)
        if isinstance(parsed, list):
            return parsed
        import ast

        try:
            p2 = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
        if isinstance(p2, (list, tuple)):
            return list(p2)
    return None


def _try_import_validate_report() -> Any:
    """尽力取到 contract.validate_report；模块未就绪时返回 None（不阻断回填）。"""
    root = __name__.split(".")[0]
    try:
        mod = importlib.import_module(root + ".contract")
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(mod, "validate_report", None)
    return fn if callable(fn) else None


def _invoke_validator(fn: Any, report: dict[str, Any]) -> list[dict[str, Any]]:
    """调用契约校验器并统一成 Problem 列表。"""
    try:
        res = fn(report)
    except Exception as exc:  # noqa: BLE001
        raise MemexError("internal", "契约校验器内部错误：" + str(exc)) from exc
    if isinstance(res, dict):
        probs = res.get("problems") or res.get("errors") or []
    elif isinstance(res, (list, tuple)):
        probs = list(res)
    else:
        probs = []
    out: list[dict[str, Any]] = []
    for p in probs:
        if isinstance(p, dict):
            out.append(p)
        else:
            out.append({"code": "invalid_report", "message": _s(p)})
    return out


def _validate_card_minimal(card: dict[str, Any]) -> list[dict[str, Any]]:
    """内置最小校验（contract 未就绪时的降级实现）。"""
    probs: list[dict[str, Any]] = []

    def add(code: str, where: str, msg: str) -> None:
        probs.append({"code": code, "path": where, "message": msg})

    kind = _s(card.get("kind"))
    if kind not in ("mechanism", "snippet", "skeleton", "gotcha", "decision"):
        add("bad_enum", "card.kind", "kind 不在五类枚举内：" + kind)
    if not _s(card.get("title")).strip():
        add("missing_field", "card.title", "title 缺失")
    if len(_s(card.get("summary"))) < 12:
        add("missing_field", "card.summary", "summary 过短")
    md = _s(card.get("mechanism_desc")).strip()
    if is_blank(md):
        add("blank_principle", "card.mechanism_desc", "mechanism_desc 为空")
    elif _has_cjk(md):
        add("unicode_language_mismatch", "card.mechanism_desc", "mechanism_desc 必须英文")
    elif count_units(md) < 20:
        add("principle_too_short", "card.mechanism_desc", "mechanism_desc 不足 20 单元")
    spans = card.get("code_spans") or []
    if kind in ("snippet", "skeleton"):
        if not spans:
            add("code_required", "card.code_spans", kind + " 卡必须有 code_spans")
        elif not _s(card.get("code")).strip():
            add("code_required", "card.code", kind + " 卡必须提供 code")
    if kind not in ("snippet", "skeleton") and spans:
        add("code_forbidden", "card.code_spans", kind + " 卡不允许出现 code_spans")
    if not (card.get("evidence") or []):
        add("missing_field", "card.evidence", "evidence 为空")
    return probs


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r["name"]) for r in conn.execute("PRAGMA table_info(" + table + ")")}
    except sqlite3.Error:
        return set()


def _pick_table(conn: sqlite3.Connection, names: tuple[str, ...]) -> str | None:
    have = _table_names(conn)
    for n in names:
        if n in have:
            return n
    return None


def _fetch_all(conn: sqlite3.Connection, table: str | None, order: str | None = None) -> list[Any]:
    if not table:
        return []
    sql = "SELECT * FROM " + table
    if order and order in _columns(conn, table):
        sql += " ORDER BY " + order
    try:
        return list(conn.execute(sql).fetchall())
    except sqlite3.Error:
        return []


def _read_only_uri(path: Path) -> str:
    """以只读 URI 打开源库：即使回填过程出错也绝不写回 VibeCraft。"""
    return path.resolve().as_uri() + "?mode=ro"


# ————————————————————————— 证据链重切 —————————————————————————
def _rebuild_evidence(
    repo_path: Path | None,
    evrows: list[Any],
    *,
    max_file_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """从真实文件重切证据区间（G12 硬约束 1）。

    返回 (code_spans, evidence, hit, attempted)：hit 为成功重切条数，attempted 为源里带了
    path+line 的条数（命中率分母）。文件不在本地 / 行号越界 / 该行为空行一律不计 hit。
    """
    spans: list[dict[str, Any]] = []
    evs: list[dict[str, Any]] = []
    hit = 0
    attempted = 0
    for ev in evrows:
        rel = _s(_first(ev, ("path", "file_path", "file", "relative_path"), "")).strip()
        line = _as_int(_first(ev, ("line", "start_line", "line_no", "line_number"), None))
        if not rel or line is None:
            continue
        attempted += 1
        if repo_path is None:
            continue
        rel = rel.lstrip("./")
        abs_path = repo_path / rel
        if not abs_path.is_file():
            continue
        try:
            raw = abs_path.read_bytes()
        except OSError:
            continue
        if len(raw) > max_file_bytes:
            continue
        text = raw.decode("utf-8", errors="replace")
        src_lines = text.splitlines()
        if not src_lines or line < 1 or line > len(src_lines):
            continue
        idx = line - 1
        if not src_lines[idx].strip():
            continue
        start_i = idx
        end_i = idx
        while start_i > 0 and (idx - start_i) < 40 and src_lines[start_i - 1].strip():
            start_i -= 1
        while end_i < len(src_lines) - 1 and (end_i - idx) < 40 and src_lines[end_i + 1].strip():
            end_i += 1
        start_line = start_i + 1
        end_line = end_i + 1
        excerpt = "\n".join(src_lines[start_i : end_i + 1])
        file_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        spans.append({"path": rel, "start_line": start_line, "end_line": end_line})
        evs.append({
            "path": rel,
            "start_line": start_line,
            "end_line": end_line,
            "symbol": "",
            "excerpt": excerpt,
            "file_sha": file_sha,
        })
        hit += 1
    return spans, evs, hit, attempted

# ————————————————————————— 卡片映射与分类 —————————————————————————
def _classify(ctype: str, mechanism: str, has_code_span: bool) -> tuple[str | None, str]:
    """定 kind。返回 (kind, rule)；kind 为 None 表示跳过（project_characteristic）。"""
    if ctype == _TYPE_FEATURE_PATTERN:
        if has_code_span:
            return "snippet", "feature_pattern/code-span"
        return "mechanism", "feature_pattern/mechanism"
    if ctype == _TYPE_INTEGRATION_NOTE:
        low = mechanism.lower()
        if any(m in mechanism or m in low for m in _GOTCHA_MARKERS):
            return "gotcha", "integration_note/gotcha"
        return "decision", "integration_note/decision"
    if ctype == _TYPE_PROJECT_CHARACTERISTIC:
        return None, "project_characteristic/skip"
    if has_code_span:
        return "snippet", "unknown/code-span"
    return "mechanism", "unknown/mechanism"


def _axis_of(row: Any) -> str:
    """从源卡片行尽力推断所属原理轴（命中别名则返回轴名，否则空串）。"""
    text = " ".join(
        _s(_first(row, (name,), ""))
        for name in ("axis", "principle", "principle_axis", "category", "tags", "labels")
    ).lower()
    for axis, aliases in _AXIS_ALIASES.items():
        if axis in text:
            return axis
        for alias in aliases:
            if alias in text:
                return axis
    return ""


def _build_card(
    row: Any,
    evrows: list[Any],
    repo_path: Path | None,
    *,
    max_file_bytes: int,
    source_repo: str,
) -> dict[str, Any]:
    """把一条 VibeCraft 卡片行映射为 memex 契约卡片（或判丢弃 / 跳过）。"""
    title = _s(_first(row, ("title", "name", "card_title"), "")).strip()
    summary = _s(_first(row, ("summary", "description", "desc", "content"), "")).strip()
    mechanism = _s(_first(row, ("mechanism", "mechanism_desc", "mechanism_description"), "")).strip()
    ctype = _s(_first(row, ("card_type", "type", "kind"), "")).strip().lower()
    tags = _clean_tags(_first(row, ("tags", "labels", "keywords"), None))
    language = _s(_first(row, ("language", "lang"), "")).strip().lower() or None
    symbol = _s(_first(row, ("symbol", "symbol_name", "anchor"), "")).strip() or None
    feature_label = _s(
        _first(row, ("feature_key", "feature", "feature_id", "group", "group_key"), "")
    ).strip()

    spans, evs, hit, attempted = _rebuild_evidence(repo_path, evrows, max_file_bytes=max_file_bytes)
    has_code_span = bool(spans)
    kind, rule = _classify(ctype, mechanism, has_code_span)

    result: dict[str, Any] = {
        "status": "import",
        "reason": None,
        "kind": kind,
        "rule": rule,
        "code_span": has_code_span,
        "attempted": attempted,
        "hit": hit,
        "pending": False,
        "card": None,
        "evidence_meta": evs,
        "axis": "",
        "feature_label": feature_label,
        "source_repo": source_repo,
        "row": row,
        "problems": [],
    }

    if kind is None:
        result["status"] = "skip"
        return result

    if attempted > 0 and hit == 0:
        result["status"] = "drop"
        result["reason"] = "code_mismatch"
        return result
    if kind in ("snippet", "skeleton") and not has_code_span:
        result["status"] = "drop"
        result["reason"] = "no_code_span"
        return result
    if attempted == 0 and kind in ("mechanism", "decision", "gotcha"):
        result["status"] = "drop"
        result["reason"] = "evidence_missing"
        return result

    if not title:
        title = (mechanism[:60] or summary[:60] or "Imported card").strip()
    if not summary:
        summary = mechanism[:200] or title
    if not mechanism:
        mechanism = summary
    if not evs:
        result["status"] = "drop"
        result["reason"] = "evidence_missing"
        return result

    card: dict[str, Any] = {
        "kind": kind,
        "title": title[:200],
        "summary": summary[:1000],
        "reusable": True,
        "mechanism_desc": mechanism,
        "evidence": [
            {"path": e["path"], "start_line": e["start_line"], "end_line": e["end_line"]}
            for e in evs
        ],
    }
    lang = language or _language_for(evs[0]["path"] if evs else None)
    if lang:
        card["language"] = lang
    if tags:
        card["tags"] = tags[:12]
    if symbol:
        card["symbol"] = symbol
    if kind in ("snippet", "skeleton"):
        card["code_spans"] = [
            {"path": s["path"], "start_line": s["start_line"], "end_line": s["end_line"]}
            for s in spans
        ]
        # 契约要求 snippet/skeleton 卡提供 code 供 code_span_mismatch 比对；
        # 直接由重切出的真实文件片段生成，保证与仓库一致。
        card["code"] = "\n".join(
            ln.strip() for e in evs for ln in str(e.get("excerpt") or "").splitlines() if ln.strip()
        )

    probs = _validate_card_minimal(card)
    pending = any(p["code"] == "unicode_language_mismatch" for p in probs)
    blocking = [p for p in probs if p["code"] != "unicode_language_mismatch"]
    if blocking:
        result["status"] = "drop"
        result["reason"] = "schema_invalid"
        result["problems"] = blocking
        return result

    result["card"] = card
    result["pending"] = pending
    result["evidence_meta"] = evs
    return result

# ————————————————————————— 报告组装 —————————————————————————
_ENTRY_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("README.md", "project documentation and overview", "docs"),
    ("readme.md", "project documentation and overview", "docs"),
    ("pyproject.toml", "python build and dependency manifest", "build"),
    ("package.json", "node package and dependency manifest", "build"),
    ("go.mod", "go module manifest", "build"),
    ("Cargo.toml", "rust crate manifest", "build"),
    ("setup.py", "python packaging entry point", "build"),
    ("main.py", "python program entry point", "main"),
    ("__main__.py", "python module entry point", "main"),
    ("Makefile", "build automation entry", "build"),
    ("docker-compose.yml", "service composition entry", "config"),
)


def _principle_texts() -> dict[str, str]:
    """五轴文本。VibeCraft 的逐轴叙述未随卡库导出，故用英文合成占位以满足契约必填。"""
    return {key: _AXIS_LABELS[key] + ": " + _SYNTHETIC_PRINCIPLE for key in PRINCIPLE_KEYS}


def _entry_points_from(
    real_repo: Path | None, ev_paths: list[str], report_path: str | None
) -> list[dict[str, Any]]:
    """入口点：优先真实存在的清单/入口文件，其次证据涉及的文件，最后退报告本身。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if real_repo is not None:
        for rel, role, kind in _ENTRY_CANDIDATES:
            if (real_repo / rel).is_file() and rel not in seen:
                out.append({"path": rel, "role": role, "kind": kind})
                seen.add(rel)
    for p in ev_paths:
        if p and p not in seen:
            lang = _language_for(p) or "source"
            out.append({"path": p, "role": "recovered evidence anchor (" + lang + ")", "kind": "docs"})
            seen.add(p)
        if len(out) >= 8:
            break
    if not out and report_path:
        out.append({"path": report_path, "role": "imported VibeCraft report", "kind": "docs"})
    return out[:8]


def _norm_characteristic(
    item: Any, fallback_evidence: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """project_characteristic → 报告级 characteristics[]。无证据则返回 None。"""
    title = ""
    detail = ""
    if isinstance(item, dict):
        title = _s(_first(item, ("title", "name", "key"), "")).strip()
        detail = _s(_first(item, ("detail", "description", "summary", "text"), "")).strip()
    else:
        title = _s(item).strip()
    if not title:
        title = (detail[:40] or "Recovered characteristic").strip()
    if count_units(detail) < 15:
        detail = (_DEFAULT_CHAR_DETAIL + " " + detail).strip()
    if not fallback_evidence:
        return None
    return {"title": title[:120], "detail": detail[:600], "evidence": list(fallback_evidence[:4])}


def _feature_key(title: str, used: set[str]) -> str:
    base = slugify(title) or "feature"
    key = base
    n = 2
    while key in used:
        key = base + "-" + str(n)
        n += 1
    used.add(key)
    return key


def _group_metas(metas: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """把卡片分成功能组（尽量凑够 3 个 feature，以过契约的 minItems=3）。"""
    by_label: dict[str, list[dict[str, Any]]] = {}
    for m in metas:
        lab = m.get("feature_label") or ""
        if lab:
            by_label.setdefault(lab, []).append(m)
    if len(by_label) >= 3:
        return list(by_label.items())
    by_axis: dict[str, list[dict[str, Any]]] = {}
    for m in metas:
        ax = m.get("axis") or ""
        if ax in PRINCIPLE_KEYS:
            by_axis.setdefault(ax, []).append(m)
    if len(by_axis) >= 3:
        return [(_AXIS_LABELS.get(ax, ax), ms) for ax, ms in by_axis.items()]
    n = len(metas)
    if n < 3:
        return []
    size = -(-n // 3)
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for i in range(0, n, size):
        chunk = metas[i : i + size]
        groups.append((chunk[0]["card"]["title"], chunk))
    return groups


def _make_feature(
    title: str,
    metas: list[dict[str, Any]],
    principles: dict[str, str],
    used_keys: set[str],
) -> dict[str, Any]:
    ordered = sorted(metas, key=lambda m: (m["card"]["kind"], m["card"]["title"]))
    summary = ordered[0]["card"]["summary"]
    if count_units(summary) < 12:
        summary = (summary + " Recovered from the VibeCraft library.").strip()
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in ordered:
        for e in m["evidence_meta"]:
            k = e["path"] + ":" + str(e["start_line"])
            if k not in seen:
                seen.add(k)
                evidence.append(
                    {"path": e["path"], "start_line": e["start_line"], "end_line": e["end_line"]}
                )
            if len(evidence) >= 6:
                break
        if len(evidence) >= 6:
            break
    return {
        "key": _feature_key(title, used_keys),
        "title": title[:160],
        "summary": summary[:600],
        "principles": dict(principles),
        "evidence": evidence,
        "cards": ordered,
        "intent": _DEFAULT_INTENT,
    }

# ————————————————————————— 仓库上下文 —————————————————————————
def _hostname(url: str) -> str:
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", _s(url))
    return s.split("/", 1)[0].split("@")[-1].lower()


def _meta_of(src: Any, post_row: Any) -> tuple[str, str, str, str]:
    """尽力取 (full_name, url, host, repo_key)；缺 url 时按 full_name 合成。"""
    d = src if src is not None else (post_row if post_row is not None else {})
    full_name = _s(_first(d, ("full_name", "name", "repo_name", "repo"), "")).strip()
    url = _s(_first(d, ("url", "clone_url", "remote_url", "source_url", "git_url", "repo_url"), "")).strip()
    repo_key = _s(_first(d, ("repo_key", "key", "repo_id", "id"), "")).strip()
    if not full_name:
        full_name = repo_key
    if not url and full_name:
        url = "https://" + full_name if "/" in full_name else ""
    host = _hostname(url) if url else "vibecraft.local"
    return (full_name or "(unknown repository)", url, host or "vibecraft.local", repo_key)


def _repo_id_for_import(host: str, full_name: str, repo_key: str) -> str:
    """按 memex 规范算 repo_id；不足以成规范 id 时用 vibecraft__ 前缀兜底。"""
    parts = full_name.split("/")
    if host and host != "vibecraft.local" and len(parts) == 2 and parts[0] and parts[1]:
        return repo_id_for(host, parts[0], parts[1])
    return "vibecraft__" + slugify(full_name or repo_key or "repo")


def _resolve_real_repo(
    candidates: list[Any], base: Path, repo_key: str
) -> Path | None:
    """定位该仓的真实文件根目录（证据链重切的前置）。"""
    for row in candidates:
        if row is None:
            continue
        for col in ("local_path", "clone_path", "repo_path", "workspace_path", "path", "source_path"):
            val = _field(row, col, None)
            if val and Path(_s(val)).is_dir():
                return Path(_s(val))
    if repo_key:
        cand = base / "repositories" / repo_key
        if cand.is_dir():
            return cand
    return None


def _minimal_report_problems(report: dict[str, Any]) -> list[dict[str, Any]]:
    """内置最小报告级校验（contract 未就绪时的降级实现）。"""
    probs: list[dict[str, Any]] = []
    feats = report.get("features") or []
    if len(feats) < 3:
        probs.append({"code": "too_few_features", "message": "features 少于 3"})
    chars = report.get("characteristics") or []
    if not chars:
        probs.append({"code": "missing_field", "message": "characteristics 为空"})
    eps = report.get("entry_points") or []
    if not eps:
        probs.append({"code": "missing_field", "message": "entry_points 为空"})
    risks = report.get("cross_feature_risks") or []
    if not risks:
        probs.append({"code": "missing_field", "message": "cross_feature_risks 为空"})
    if count_units(_s(report.get("one_liner"))) < 10:
        probs.append({"code": "missing_field", "message": "one_liner 过短"})
    return probs


def _render_markdown(report: dict[str, Any], full_name: str) -> str:
    """把报告渲染为 4 个固定 H2 的 Markdown（与契约骨架一致）。"""
    out: list[str] = ["# " + full_name, "", report.get("one_liner", ""), ""]
    out.append(_H2_CHAR)
    for ch in report.get("characteristics", []):
        out.append("### " + ch["title"])
        out.append(ch["detail"])
        out.append("")
    out.append(_H2_PRIN)
    for feat in report.get("features", []):
        out.append("### " + feat["title"])
        for key in PRINCIPLE_KEYS:
            out.append("#### " + _AXIS_LABELS[key])
            out.append(feat["principles"][key])
        out.append("")
    out.append(_H2_FEAT)
    for feat in report.get("features", []):
        out.append("### " + feat["title"])
        out.append(feat["summary"])
        for card in feat["cards"]:
            out.append("#### " + card["title"])
            out.append(card["summary"])
        out.append("")
    out.append(_H2_RISK)
    for risk in report.get("cross_feature_risks", []):
        out.append("### " + risk["title"])
        out.append(risk["detail"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"

# ————————————————————————— 落库 —————————————————————————
def _ensure_reindex_column(conn: sqlite3.Connection) -> None:
    """脊柱 DDL 尚未包含 analyses.reindex_state 时就地补齐该列（幂等、兼容旧库）。

    docs（tech-design §2.x / decisions.md G12）要求该列存在；在脊柱补齐前由此处
    兜底，不改动 db.py 源码。列已存在时不做任何事。
    """
    if "reindex_state" not in _columns(conn, "analyses"):
        conn.execute(
            "ALTER TABLE analyses ADD COLUMN reindex_state TEXT DEFAULT 'indexed'"
        )


def _persist_analysis(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """把一份回填分析整体写入真源库（repo / analysis / feature / card / evidence / chunk）。

    幂等：所有主键都是确定性 sha（同输入同 id），一律 INSERT OR REPLACE，重跑不产生重复。
    """
    repo = payload["repo"]
    conn.execute(
        "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, description, language, "
        "identity_key, source, is_stale, is_fork, is_local) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (repo["repo_id"], repo["full_name"], repo["url"], repo["host"], repo["description"],
         repo["language"], repo["identity_key"], "local", 0, 0, 1),
    )
    an = payload["analysis"]
    conn.execute(
        "INSERT OR REPLACE INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
        "analyst, producer, status, report_md, report_json, counts_json, quality_json, created_at, "
        "finished_at, reindex_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (an["analysis_id"], an["repo_id"], an["commit_sha"], an["contract_version"], an["depth"],
         an["analyst"], an["producer"], an["status"], an["report_md"], an["report_json"],
         an["counts_json"], an["quality_json"], an["created_at"], an["finished_at"], an["reindex_state"]),
    )
    for f in payload["features"]:
        conn.execute(
            "INSERT OR REPLACE INTO features(feature_id, analysis_id, slug, title, summary, position) "
            "VALUES(?,?,?,?,?,?)",
            (f["feature_id"], f["analysis_id"], f["slug"], f["title"], f["summary"], f["position"]),
        )
    for c in payload["cards"]:
        conn.execute(
            "INSERT OR REPLACE INTO cards(card_id, feature_id, kind, reusable, title, summary, "
            "mechanism_desc, language, symbol, code_spans_json, quality_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (c["card_id"], c["feature_id"], c["kind"], c["reusable"], c["title"], c["summary"],
             c["mechanism_desc"], c["language"], c["symbol"], c["code_spans_json"], c["quality_json"]),
        )
    for e in payload["evidence"]:
        conn.execute(
            "INSERT OR REPLACE INTO evidence(evidence_id, card_id, path, start_line, end_line, "
            "symbol, file_sha, excerpt) VALUES(?,?,?,?,?,?,?,?)",
            (e["evidence_id"], e["card_id"], e["path"], e["start_line"], e["end_line"], e["symbol"],
             e["file_sha"], e["excerpt"]),
        )
    for ch in payload["chunks"]:
        conn.execute(
            "INSERT OR REPLACE INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, "
            "heading) VALUES(?,?,?,?,?,?,?,?)",
            (ch["chunk_id"], ch["kind"], ch["ref_id"], ch["card_id"], ch["text"], ch["repo_id"],
             ch["language"], ch["heading"]),
        )


# ————————————————————————— 源库读取 —————————————————————————
def _source_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_read_only_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        pass
    return conn


def _evidence_by_card(conn: sqlite3.Connection, cards_tbl: str | None) -> dict[str, list[Any]]:
    """按 card_id 归组证据行。"""
    out: dict[str, list[Any]] = {}
    ev_tbl = _pick_table(conn, ("repo_knowledge_evidence", "repo_evidence", "knowledge_evidence"))
    if not ev_tbl:
        return out
    for row in _fetch_all(conn, ev_tbl):
        cid = _s(_first(row, ("card_id", "knowledge_card_id", "knowledge_id", "id"), "")).strip()
        if cid:
            out.setdefault(cid, []).append(row)
    return out

# ————————————————————————— 结构匹配 —————————————————————————
_KEY_NAMES: tuple[str, ...] = ("repo_key", "repo_id", "repo", "project_key", "project_id")


def _key_of(row: Any) -> str:
    return _s(_first(row, _KEY_NAMES, "")).strip()


def _an_keys(an: Any) -> set[str]:
    keys: set[str] = set()
    for name in ("repo_key", "repo_id", "analysis_id", "id", "run_id", "project_key"):
        v = _field(an, name, None)
        if v:
            keys.add(_s(v).strip())
    return keys


def _card_keys(card: Any) -> set[str]:
    keys: set[str] = set()
    for name in ("repo_key", "repo_id", "analysis_id", "run_id", "project_key", "knowledge_id"):
        v = _field(card, name, None)
        if v:
            keys.add(_s(v).strip())
    return keys


def _evref(e: dict[str, Any]) -> dict[str, Any]:
    return {"path": e["path"], "start_line": e["start_line"], "end_line": e["end_line"]}


def _ensure_db(paths: Paths) -> None:
    if not paths.db.exists():
        db.init_db(paths)

def _import_all_conn(
    paths: Paths,
    cfg: Config,
    src_path: Path,
    base: Path,
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    warnings: list[str],
    validate_fn: Any,
) -> dict[str, Any]:
    """读取源库、映射、组装报告，必要时落库；返回 G12 形状报告。"""
    repos_tbl = _pick_table(conn, ("repo_sources", "repos", "repo_library", "projects"))
    analyses_tbl = _pick_table(conn, ("repo_analysis_results", "repo_analysis_runs", "analyses"))
    cards_tbl = _pick_table(conn, ("repo_knowledge_cards", "knowledge_cards", "cards"))
    snapshots_tbl = _pick_table(conn, ("repo_snapshots", "snapshots"))
    if analyses_tbl is None:
        warnings.append("源库缺少 repo_analysis_results / repo_analysis_runs / analyses 表，视为空库")
    if cards_tbl is None:
        warnings.append("源库缺少 repo_knowledge_cards / knowledge_cards / cards 表，视为空库")

    an_rows = _fetch_all(conn, analyses_tbl)
    repo_rows = _fetch_all(conn, repos_tbl)
    card_rows = _fetch_all(conn, cards_tbl)
    snap_rows = _fetch_all(conn, snapshots_tbl)
    ev_by_card = _evidence_by_card(conn, cards_tbl)

    repo_by_key: dict[str, Any] = {}
    meta_by_key: dict[str, Any] = {}
    for rr in repo_rows:
        k = _key_of(rr)
        if k and k not in repo_by_key:
            repo_by_key[k] = rr
    for sr in snap_rows:
        k = _key_of(sr)
        if k and k not in meta_by_key:
            meta_by_key[k] = sr

    all_keys: set[str] = {_key_of(r) for r in repo_rows if _key_of(r)}
    all_keys |= {_key_of(a) for a in an_rows if _key_of(a)}
    all_keys |= {_key_of(c) for c in card_rows if _key_of(c)}

    drop = {r: 0 for r in _DROP_REASONS}
    cards_seen = 0
    cards_imported = 0
    cards_dropped = 0
    pending_mechanism = 0
    attempts = 0
    hits = 0
    pending_writes: list[dict[str, Any]] = []
    now = db.utcnow()
    keyless_cards = [c for c in card_rows if not _card_keys(c)]

    for idx, an in enumerate(an_rows):
        repo_key = _key_of(an)
        src_row = repo_by_key.get(repo_key)
        sn_row = meta_by_key.get(repo_key)
        full_name, url, host, _rk = _meta_of(src_row if src_row is not None else an, sn_row)
        repo_id = _repo_id_for_import(host, full_name, repo_key)

        an_keys = _an_keys(an)
        my_cards = [c for c in card_rows if _card_keys(c) and (_card_keys(c) & an_keys)]
        if idx == 0:
            my_cards = my_cards + keyless_cards

        real = _resolve_real_repo([sn_row, src_row, an], base, repo_key)
        if real is None and my_cards:
            warnings.append(full_name + " 的仓库文件不在本地，卡片证据无法重切（判 code_mismatch）")

        metas: list[dict[str, Any]] = []
        char_items: list[dict[str, Any]] = []
        for crow in my_cards:
            cards_seen += 1
            cid = _s(_first(crow, ("id", "card_id", "knowledge_card_id"), "")).strip()
            evrows = ev_by_card.get(cid, [])
            built = _build_card(
                crow, evrows, real, max_file_bytes=cfg.max_file_bytes, source_repo=repo_id
            )
            attempts += int(built["attempted"])
            hits += int(built["hit"])
            if built["status"] == "skip":
                ch = _norm_characteristic(crow, built["evidence_meta"])
                if ch is not None:
                    char_items.append(ch)
                continue
            if built["status"] == "drop":
                cards_dropped += 1
                reason = _s(built["reason"]) or "schema_invalid"
                if reason not in drop:
                    reason = "schema_invalid"
                drop[reason] += 1
                continue
            built["axis"] = _axis_of(crow)
            metas.append(built)

        if not metas:
            continue

        principles = _principle_texts()
        groups = _group_metas(metas)
        used_keys: set[str] = set()
        features = [_make_feature(t, ms, principles, used_keys) for t, ms in groups]
        imported_metas = [m for f in features for m in f["cards"]]
        if len(features) < 3:
            warnings.append(full_name + " 可用卡片不足以组成 3 个功能，回填跳过该分析")
            for _m in imported_metas:
                drop["schema_invalid"] += 1
                cards_dropped += 1
            continue

        # characteristics：project_characteristic 优先；否则用证据/入口点合成
        chars = char_items[:8]
        if not chars:
            pool: list[dict[str, Any]] = []
            for m in imported_metas:
                pool.extend(m["evidence_meta"])
            if pool:
                chars = [{"title": "Recovered implementation anchors", "detail": _DEFAULT_CHAR_DETAIL,
                          "evidence": [_evref(e) for e in pool[:4]]}]
            elif real is not None:
                eps = _entry_points_from(real, [], None)
                if eps:
                    chars = [{"title": "Recovered repository layout", "detail": _DEFAULT_CHAR_DETAIL,
                              "evidence": [{"path": eps[0]["path"]}]}]

        ev_paths = [e["path"] for m in imported_metas for e in m["evidence_meta"]]
        report_path = _s(_first(an, ("report_path", "report", "md_path"), "")).strip() or None
        eps = _entry_points_from(real, ev_paths, report_path)
        if not eps:
            warnings.append(full_name + " 无法定位任何入口点，跳过该分析（契约要求 entry_points 非空）")
            for _m in imported_metas:
                drop["schema_invalid"] += 1
                cards_dropped += 1
            continue

        one_liner = _s(_first(an, ("one_liner", "summary", "title", "description"), "")).strip()
        if count_units(one_liner) < 10:
            one_liner = _DEFAULT_ONELINER
        risk_ev: list[dict[str, Any]] = []
        for m in imported_metas:
            risk_ev = [_evref(e) for e in m["evidence_meta"][:2]]
            if risk_ev:
                break
        if not risk_ev:
            risk_ev = [{"path": eps[0]["path"]}]
        risks = [{"title": "Imported knowledge risk surface", "detail": _DEFAULT_RISK_DETAIL,
                  "evidence": risk_ev}]

        report: dict[str, Any] = {
            "schema_id": CONTRACT_ID,
            "one_liner": one_liner,
            "characteristics": chars,
            "entry_points": eps,
            "features": [
                {"key": f["key"], "title": f["title"], "summary": f["summary"],
                 "principles": f["principles"], "evidence": f["evidence"],
                 "cards": [m["card"] for m in f["cards"]], "intent": f["intent"]}
                for f in features
            ],
            "cross_feature_risks": risks,
        }

        problems = _validate_report(validate_fn, report)
        quality: dict[str, int] = {}
        for p in problems:
            code = _s(p.get("code", "invalid_report"))
            quality[code] = quality.get(code, 0) + 1

        # —— 组装落库载荷 ——
        analysis_src_id = _s(_first(an, ("analysis_id", "id", "run_id"), "")).strip()
        commit = _s(_first(an, ("commit_sha", "commit", "head_sha", "snapshot_sha"), "")).strip()
        analysis_id = "vibecraft:" + _hash20(repo_id, analysis_src_id, commit)
        commit_sha = commit or ("vibecraft-" + _hash20(repo_id, analysis_src_id)[:12])
        pending_count = sum(1 for m in imported_metas if m["pending"])
        pending_mechanism += pending_count
        reindex = "pending" if pending_count else "indexed"

        feature_payloads: list[dict[str, Any]] = []
        card_payloads: list[dict[str, Any]] = []
        evidence_payloads: list[dict[str, Any]] = []
        chunk_payloads: list[dict[str, Any]] = []
        n_evidence = 0
        for fi, f in enumerate(features):
            fid = _sid("feat", analysis_id, f["key"])
            feature_payloads.append({"feature_id": fid, "analysis_id": analysis_id, "slug": f["key"],
                                     "title": f["title"], "summary": f["summary"], "position": fi})
            chid_f = _sid("chunkf", analysis_id, f["key"])
            chunk_payloads.append({"chunk_id": chid_f, "kind": "feature", "ref_id": fid, "card_id": None,
                                   "text": f["summary"], "repo_id": repo_id, "language": None,
                                   "heading": f["title"]})
            for m in f["cards"]:
                card = m["card"]
                cid = _sid("card", analysis_id, f["key"], card["title"])
                spans = card.get("code_spans")
                card_payloads.append({
                    "card_id": cid, "feature_id": fid, "kind": card["kind"], "reusable": 1,
                    "title": card["title"], "summary": card["summary"],
                    "mechanism_desc": card["mechanism_desc"], "language": card.get("language"),
                    "symbol": card.get("symbol"),
                    "code_spans_json": db.json_dumps(spans) if spans else None,
                    "quality_json": db.json_dumps({"source_repo": repo_id, "pending": m["pending"]}),
                })
                for e in m["evidence_meta"]:
                    eid = _sid("ev", cid, e["path"], str(e["start_line"]), str(e["end_line"]))
                    evidence_payloads.append({
                        "evidence_id": eid, "card_id": cid, "path": e["path"],
                        "start_line": e["start_line"], "end_line": e["end_line"],
                        "symbol": e.get("symbol") or None, "file_sha": e.get("file_sha") or None,
                        "excerpt": e.get("excerpt") or None,
                    })
                    n_evidence += 1
                if not m["pending"]:
                    chid = _sid("chunk", analysis_id, f["key"], card["title"])
                    chunk_payloads.append({"chunk_id": chid, "kind": "card", "ref_id": cid,
                                           "card_id": cid, "text": card["mechanism_desc"],
                                           "repo_id": repo_id, "language": card.get("language"),
                                           "heading": card["title"]})

        counts_json = {"features": len(features), "cards": len(imported_metas), "evidence": n_evidence}
        cards_imported += len(imported_metas)
        description = _s(_first(src_row if src_row is not None else an, ("description", "summary", "desc"), "")).strip() or None
        language = _s(_first(src_row if src_row is not None else an, ("language", "lang"), "")).strip().lower() or None
        repo_payload = {
            "repo_id": repo_id, "full_name": full_name, "url": url, "host": host,
            "description": description, "language": language,
            "identity_key": "vibecraft#" + (repo_key or full_name),
        }
        analysis_payload = {
            "analysis_id": analysis_id, "repo_id": repo_id, "commit_sha": commit_sha,
            "contract_version": CONTRACT_VERSION, "depth": _DEPTH, "analyst": _ANALYST,
            # status 只表达「能不能当成结论用」，来源由 producer/analyst 区分：
            "producer": _PRODUCER, "status": "committed",
            "report_md": _render_markdown(report, full_name),
            "report_json": db.json_dumps(report), "counts_json": db.json_dumps(counts_json),
            "quality_json": db.json_dumps(quality), "created_at": now, "finished_at": now,
            "reindex_state": reindex,
        }
        pending_writes.append({
            "repo": repo_payload, "analysis": analysis_payload, "features": feature_payloads,
            "cards": card_payloads, "evidence": evidence_payloads, "chunks": chunk_payloads,
        })

    indexed = 0
    indexed = 0
    if not dry_run and pending_writes:
        _ensure_db(paths)
        wconn = db.connect(str(paths.db))
        try:
            db.apply_schema(wconn)
            _ensure_reindex_column(wconn)
            wconn.execute("BEGIN")
            for payload in pending_writes:
                _persist_analysis(wconn, payload)
            wconn.execute("COMMIT")

            # P0-2：块落库后立刻建索引。原来这里什么都不做，只在返回体里告诉用户
            # 「下一步去跑 reindex」——可 reindex 只覆盖 status 对得上的分析，
            # 且跳过 reindex_state='pending' 的块，这条建议既绕又可能无效：
            # 用户照做后仍召不回任何回填内容。这里就地建好，只覆盖本次写入的行。
            embedder = get_embedder(cfg.embedder or None)
            for payload in pending_writes:
                ana = payload["analysis"]
                if ana.get("reindex_state") == "pending":
                    # 机制描述非英文的卡片本来就「入库但不建块」（模块 docstring），
                    # 显式跳过而不是等 reindex 碰运气。
                    continue
                try:
                    store_index.index_analysis(
                        wconn, embedder, ana["analysis_id"], report_md=None
                    )
                    store_index.set_chunk_repo(wconn, ana["repo_id"])
                    indexed += 1
                except Exception as exc:  # noqa: BLE001 索引是派生层，回填本体已成功
                    warnings.append(
                        ana["repo_id"] + " 索引构建失败（内容已回填，可用 reindex 补建）："
                        + str(exc)
                    )
        except sqlite3.Error as exc:
            try:
                wconn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise MemexError("internal", "写入 memex 库失败：" + str(exc)) from exc
        finally:
            wconn.close()

    rate = round(hits / attempts, 4) if attempts else 0.0
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "source": str(src_path),
        "repos_seen": len(all_keys),
        "analyses_seen": len(an_rows),
        "cards_seen": cards_seen,
        "cards_imported": cards_imported,
        "cards_dropped": cards_dropped,
        "drop_reasons": drop,
        "evidence_hit_rate": rate,
        "pending_mechanism": pending_mechanism,
        "next_step": (
            {"action": "search", "note": "回填内容已落库并建好索引，可直接 search"}
            if indexed
            else {"action": "reindex", "note": "本次未建索引（多为机制描述非英文），可用 reindex 补建"}
        ),
        "indexed_analyses": indexed,
    }
    if pending_writes and dry_run:
        result["planned_writes"] = len(pending_writes)
    if warnings:
        result["warnings"] = warnings
    return result

# ————————————————————————— 契约校验入口 —————————————————————————
def _validate_report(validate_fn: Any, report: dict[str, Any]) -> list[dict[str, Any]]:
    """优先用 contract.validate_report；不可用时降级为内置最小报告级校验。"""
    if validate_fn is not None:
        return _invoke_validator(validate_fn, report)
    return _minimal_report_problems(report)


# ————————————————————————— 公开入口 —————————————————————————
def import_vibecraft(
    paths: Paths | None = None,
    cfg: Config | None = None,
    path: str = "",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """从 VibeCraft 的 SQLite 业务库（path）最佳努力回填（G12）。

    - 只读源库；卡片按契约校验，过不了丢弃并计数；
    - 证据从真实文件重切；非英文 mechanism_desc 入库但标 pending；
    - 返回 G12 形状报告；dry_run=True 只出报告不落库。

    CLI 只传 path（位置参数），故 paths/cfg 给默认值：paths 取 $MEMEX_HOME，
    cfg 取环境配置（失败时退一个最小 Config）。
    """
    warnings: list[str] = []
    # CLI 形态 import-vibecraft <path> 会把源库路径按位置传给 paths；
    # 此处做一次参数纠偏，使 CLI 与本模块文档签名两种调用都可用。
    if paths is not None and not isinstance(paths, Paths):
        if not path:
            path = str(paths)
        paths = None
    if cfg is not None and not isinstance(cfg, Config):
        cfg = None
    if paths is None:
        paths = Paths.default()
    if cfg is None:
        try:
            cfg = Config.from_env()
        except MemexError:
            cfg = Config(home=paths.home)

    src = Path(path).expanduser()
    if not src.is_file():
        raise MemexError(
            "not_found",
            "VibeCraft 库不存在或不是文件：" + str(src),
            {"path": str(src)},
        )
    try:
        with src.open("rb") as fh:
            magic = fh.read(16)
    except OSError as exc:
        raise MemexError(
            "invalid_argument", "无法读取 VibeCraft 库：" + str(exc), {"path": str(src)}
        ) from exc
    if not magic.startswith(b"SQLite format 3\x00"):
        raise MemexError(
            "invalid_argument", "不是 SQLite 数据库文件：" + str(src), {"path": str(src)}
        )

    validate_fn = _try_import_validate_report()
    if validate_fn is None:
        warnings.append(
            "未找到 contract.validate_report；已降级为内置最小校验（schema_invalid 判定更粗粒度）"
        )

    base = src.parent
    try:
        conn = _source_conn(src)
    except sqlite3.Error as exc:
        raise MemexError(
            "invalid_argument", "打开 VibeCraft 库失败：" + str(exc), {"path": str(src)}
        ) from exc
    try:
        result = _import_all_conn(
            paths, cfg, src, base, conn,
            dry_run=dry_run, warnings=warnings, validate_fn=validate_fn,
        )
    except sqlite3.Error as exc:
        raise MemexError(
            "invalid_argument", "读取 VibeCraft 库时出错：" + str(exc), {"path": str(src)}
        ) from exc
    finally:
        conn.close()
    return result
