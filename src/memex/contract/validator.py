"""报告校验器：把 agent 交回的 report 对象按契约逐条检查（G3 / A5）。

契约来源：docs/report-contract.md §6 与 docs/report.schema.json。
- 纯只读、幂等、可独立调用（A5）；不写库。
- is_valid=false 时仍返回 ok=true（业务失败≠协议错误）。
- Problem.code 是**封闭枚举**，与 docs 完全一致。
- 有 repo 根目录时做文件级检查（bad_evidence_path / line_out_of_range /
  code_span_mismatch）；无则跳过并给出 warning。

度量口径（x-counting）：units = CJK 字符数 + 拉丁/数字词段数（core.count_units）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..constants import (
    AXIS_NEGLECT,
    BLANK_PLACEHOLDERS,
    CARD_KINDS,
    CODE_REQUIRED_KINDS,
    CONTRACT_ID,
    MIN_INTENT_UNITS,
    MIN_PRINCIPLE_UNITS,
    MIN_SUMMARY_UNITS,
    MAX_AXIS_REUSE,
    PRINCIPLE_KEYS,
)
from ..core import count_units, is_blank, json_pointer_escape

ENTRY_POINT_KINDS = ("main", "cli", "server", "worker", "test", "config", "build", "docs")

_TOP_REQUIRED = ("schema_id", "one_liner", "characteristics", "entry_points", "features", "cross_feature_risks")
_TOP_ALLOWED = set(_TOP_REQUIRED)
_FEATURE_REQUIRED = ("key", "title", "summary", "principles", "evidence", "cards", "intent")
_FEATURE_ALLOWED = set(_FEATURE_REQUIRED)
_CARD_REQUIRED = ("kind", "title", "summary", "reusable", "mechanism_desc", "evidence")
_CARD_ALLOWED = set(_CARD_REQUIRED) | {"tags", "code_spans", "code", "symbol", "language"}
_EVIDENCE_ALLOWED = {"path", "start_line", "end_line", "symbol", "note"}
_CHARACTERISTIC_REQUIRED = ("title", "detail", "evidence")
_CHARACTERISTIC_ALLOWED = set(_CHARACTERISTIC_REQUIRED)
# schema EntryPoint.required = [path, role]；kind 是可选枚举
_ENTRY_REQUIRED = ("path", "role")
_ENTRY_ALLOWED = set(_ENTRY_REQUIRED) | {"kind"}
_RISK_REQUIRED = ("title", "detail", "evidence")
_RISK_ALLOWED = set(_RISK_REQUIRED)
_SPAN_ALLOWED = {"path", "start_line", "end_line", "symbol"}

_ELISION_RE = re.compile(r"^\s*(?://|#|/\*|\*|--)?\s*[.…]{2,}.*(elided|省略|中间省略).*$", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


class _Ctx:
    def __init__(self, repo_root: Path | None, subpath: str | None):
        self.repo_root = repo_root
        self.subpath = subpath
        self.problems: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []
        self.code_checked = 0
        self.code_mismatch = 0
        self._file_lines: dict[str, list[str] | None] = {}

    def problem(self, code: str, where: str, message: str) -> None:
        self.problems.append({"code": code, "where": where, "message": message})

    def warn(self, code: str, where: str, message: str) -> None:
        self.warnings.append({"code": code, "where": where, "message": message})

    def lines(self, rel_path: str) -> list[str] | None:
        if rel_path in self._file_lines:
            return self._file_lines[rel_path]
        result: list[str] | None = None
        if self.repo_root is not None:
            base = self.repo_root / self.subpath if self.subpath else self.repo_root
            target = (base / rel_path).resolve()
            try:
                target.relative_to(base.resolve())
            except ValueError:
                target = None  # type: ignore[assignment]  # 逃逸
            if target is not None and target.is_file():
                try:
                    result = target.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    result = None
        self._file_lines[rel_path] = result
        return result


def _ptr(*parts: str | int) -> str:
    return "/" + "/".join(json_pointer_escape(str(p)) for p in parts)


def _check_unknown(obj: dict[str, Any], allowed: set[str], where: str, ctx: _Ctx) -> None:
    for k in obj:
        if k not in allowed:
            ctx.problem("unknown_field", _ptr(*_seg(where), k), f"未知字段 {k}")


def _seg(where: str) -> list[str]:
    return [s for s in where.strip("/").split("/") if s] if where else []


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def validate_report(
    report: Any,
    *,
    repo_root: str | Path | None = None,
    subpath: str | None = None,
) -> dict[str, Any]:
    """校验报告。返回 {ok, is_valid, problems, warnings, counts}。"""
    ctx = _Ctx(Path(repo_root) if repo_root else None, subpath)
    if not isinstance(report, dict):
        ctx.problem("missing_field", "", "report 必须是对象")
        return _result(ctx, report)

    # —— 顶层 ——
    for key in _TOP_REQUIRED:
        if key not in report:
            ctx.problem("missing_field", _ptr(key), f"缺少字段 {key}")
    _check_unknown(report, _TOP_ALLOWED, "", ctx)
    if report.get("schema_id") != CONTRACT_ID:
        ctx.problem("bad_enum", _ptr("schema_id"), f"schema_id 必须为 {CONTRACT_ID}")

    feats = report.get("features")
    if not isinstance(feats, list):
        feats = []

    _check_list_of_objs(report, "characteristics", _CHARACTERISTIC_REQUIRED, _CHARACTERISTIC_ALLOWED, (1, 8), ctx)
    _check_list_of_objs(report, "entry_points", _ENTRY_REQUIRED, _ENTRY_ALLOWED, (1, None), ctx)
    _check_list_of_objs(report, "cross_feature_risks", _RISK_REQUIRED, _RISK_ALLOWED, (1, None), ctx)

    if isinstance(feats, list) and len(feats) < 3:
        ctx.problem("too_few_features", _ptr("features"), f"features 至少 3 个，实为 {len(feats)}")

    seen_keys: set[str] = set()
    for i, feat in enumerate(feats):
        if isinstance(feat, dict):
            _check_feature(feat, i, seen_keys, ctx)
        else:
            ctx.problem("missing_field", _ptr("features", i), "feature 必须是对象")

    return _result(ctx, report)


def _result(ctx: _Ctx, report: Any) -> dict[str, Any]:
    counts = _counts(report)
    counts.update({
        "problems": len(ctx.problems),
        "warnings": len(ctx.warnings),
        "code_mismatch": ctx.code_mismatch,
        "code_checked": ctx.code_checked,
    })
    return {
        "ok": True,
        "is_valid": not ctx.problems,
        "problems": ctx.problems,
        "warnings": ctx.warnings,
        "counts": counts,
    }


def _check_list_of_objs(
    report: dict[str, Any],
    field: str,
    required: tuple[str, ...],
    allowed: set[str],
    bounds: tuple[int | None, int | None],
    ctx: _Ctx,
) -> None:
    """校验顶层三个对象数组：数量上下界 + 逐元素形状（必填/未知字段/标题长度/证据）。"""
    val = report.get(field)
    if not isinstance(val, list):
        return
    lo, hi = bounds
    if lo is not None and len(val) < lo:
        ctx.problem("missing_field", _ptr(field), f"{field} 至少 {lo} 个")
    if hi is not None and len(val) > hi:
        ctx.problem("unknown_field", _ptr(field), f"{field} 至多 {hi} 个")
    for i, item in enumerate(val):
        base = (field, i)
        if not isinstance(item, dict):
            ctx.problem("missing_field", _ptr(*base), f"{field}/{i} 必须是对象")
            continue
        _check_unknown(item, allowed, _ptr(*base), ctx)
        for key in required:
            if key not in item:
                ctx.problem("missing_field", _ptr(*base, key), f"缺少字段 {key}")
        if field == "entry_points":
            _check_entry_point(item, base, ctx)
        else:
            # characteristics / cross_feature_risks 同构：title + detail + evidence[]
            _check_min_chars(item.get("title"), 3, (*base, "title"), ctx)
            _check_min_chars(item.get("detail"), 15, (*base, "detail"), ctx)
            _check_evidence_list(item.get("evidence"), _ptr(*base, "evidence"), ctx, min_items=1)


def _check_min_chars(val: Any, minimum: int, base: tuple[Any, ...], ctx: _Ctx) -> None:
    """按 schema 的 minLength 语义查**字符数**。

    与 _check_len（信息单元）刻意不同：schema 里 characteristics.title / detail /
    entry_points.role 写的是 minLength 字符数，用信息单元会误杀短英文标题。
    """
    if not isinstance(val, str) or len(val) < minimum:
        ctx.problem("principle_too_short", _ptr(*base), f"字段过短（需 >= {minimum} 字符）")


def _check_entry_point(item: dict[str, Any], base: tuple[Any, ...], ctx: _Ctx) -> None:
    """EntryPoint：path 必须真实存在；role 至少 4 字符；kind 必须在封闭枚举内。"""
    path = _as_str(item.get("path"))
    if not path:
        ctx.problem("missing_field", _ptr(*base, "path"), "path 不能为空")
    elif ctx.repo_root is not None and ctx.lines(path) is None:
        ctx.problem("bad_evidence_path", _ptr(*base, "path"), f"entry_point 指向的文件不存在：{path}")
    _check_min_chars(item.get("role"), 4, (*base, "role"), ctx)
    kind = item.get("kind")
    if kind is not None and kind not in ENTRY_POINT_KINDS:
        ctx.problem("bad_enum", _ptr(*base, "kind"), f"entry_point.kind 非法：{kind!r}")


def _check_feature(feat: dict[str, Any], i: int, seen_keys: set[str], ctx: _Ctx) -> None:
    where = _ptr("features", i)
    for key in _FEATURE_REQUIRED:
        if key not in feat:
            ctx.problem("missing_field", _ptr("features", i, key), f"缺少字段 {key}")
    _check_unknown(feat, _FEATURE_ALLOWED, where, ctx)

    key = _as_str(feat.get("key"))
    if key:
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", key):
            ctx.problem("bad_enum", _ptr("features", i, "key"), "key 必须为 kebab-case")
        if key in seen_keys:
            ctx.problem("duplicate_feature_key", _ptr("features", i, "key"), f"feature key 重复：{key}")
        seen_keys.add(key)

    _check_len(feat.get("title"), 3, ("features", i, "title"), ctx)
    _check_len_units(feat.get("summary"), MIN_SUMMARY_UNITS, ("features", i, "summary"), ctx)

    intent = _as_str(feat.get("intent"))
    if intent:
        if count_units(intent) < MIN_INTENT_UNITS:
            ctx.problem("principle_too_short", _ptr("features", i, "intent"), "intent 过短")
        if _CJK_RE.search(intent):
            ctx.problem("unicode_language_mismatch", _ptr("features", i, "intent"), "intent 必须为英文")

    _check_evidence_list(feat.get("evidence"), _ptr("features", i, "evidence"), ctx, min_items=1)

    principles = feat.get("principles")
    if not isinstance(principles, dict):
        ctx.problem("missing_field", _ptr("features", i, "principles"), "principles 必须是对象")
    else:
        for axis in PRINCIPLE_KEYS:
            if axis not in principles:
                ctx.problem("missing_field", _ptr("features", i, "principles", axis), f"缺少轴 {axis}")
                continue
            val = principles[axis]
            if isinstance(val, dict):
                val = val.get("detail") or val.get("summary") or ""
            text = _as_str(val)
            if is_blank(text) or text.strip() in BLANK_PLACEHOLDERS:
                ctx.problem("blank_principle", _ptr("features", i, "principles", axis), "轴内容为空")
            elif count_units(text) < MIN_PRINCIPLE_UNITS:
                ctx.problem("principle_too_short", _ptr("features", i, "principles", axis), "轴内容过短")

        # G23：五轴雷同门禁——三轴以上正文同一句话，说明这一轴没被真正分析过。
        sigs = [_axis_signature(_as_str(p)) for p in principles.values()
                if isinstance(p, (str, dict))]
        sigs = [g for g in sigs if g]
        if sigs and len(set(sigs)) <= MAX_AXIS_REUSE:
            ctx.problem(
                "axis_reuse",
                _ptr("features", i, "principles"),
                f"五原理轴有 {len(sigs) - len(set(sigs))} 轴正文雷同（归一化后同一文本），"
                f"只有 {len(set(sigs))} 个不同维度",
            )

    cards = feat.get("cards")
    if not isinstance(cards, list):
        ctx.problem("missing_field", _ptr("features", i, "cards"), "cards 必须是列表")
        return
    if len(cards) < 1:
        ctx.problem("missing_field", _ptr("features", i, "cards"), "每个 feature 至少 1 张卡片")
    for j, card in enumerate(cards):
        if isinstance(card, dict):
            _check_card(card, i, j, ctx)
        else:
            ctx.problem("missing_field", _ptr("features", i, "cards", j), "card 必须是对象")


def _check_card(card: dict[str, Any], i: int, j: int, ctx: _Ctx) -> None:
    base = ("features", i, "cards", j)
    for key in _CARD_REQUIRED:
        if key not in card:
            ctx.problem("missing_field", _ptr(*base, key), f"缺少字段 {key}")
    _check_unknown(card, _CARD_ALLOWED, _ptr(*base), ctx)

    kind = _as_str(card.get("kind"))
    if kind not in CARD_KINDS:
        ctx.problem("bad_enum", _ptr(*base, "kind"), f"非法 kind：{kind!r}")

    _check_len(card.get("title"), 3, (*base, "title"), ctx)
    _check_len_units(card.get("summary"), MIN_SUMMARY_UNITS, (*base, "summary"), ctx)

    mech = _as_str(card.get("mechanism_desc"))
    if count_units(mech) < 20:
        ctx.problem("principle_too_short", _ptr(*base, "mechanism_desc"), "mechanism_desc 过短")
    if _CJK_RE.search(mech):
        ctx.problem("unicode_language_mismatch", _ptr(*base, "mechanism_desc"), "mechanism_desc 必须为英文")

    if not isinstance(card.get("reusable"), bool):
        ctx.problem("bad_enum", _ptr(*base, "reusable"), "reusable 必须为布尔")

    tags = card.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            ctx.problem("bad_enum", _ptr(*base, "tags"), "tags 必须是列表")
        else:
            for t in tags:
                if not isinstance(t, str) or not re.match(r"^[a-z0-9][a-z0-9._-]*$", t):
                    ctx.problem("bad_enum", _ptr(*base, "tags"), f"非法 tag：{t!r}")

    spans = card.get("code_spans")
    if kind in CODE_REQUIRED_KINDS:
        if not isinstance(spans, list) or not spans:
            ctx.problem("code_required", _ptr(*base, "code_spans"), f"kind={kind} 必须提供 code_spans")
        else:
            for s in spans:
                _check_span(s, base, ctx)
            code = _as_str(card.get("code"))
            if not code:
                ctx.problem("code_required", _ptr(*base, "code"), f"kind={kind} 必须提供 code")
            else:
                _check_code_mismatch(spans, code, base, ctx)
    else:
        if spans:
            ctx.problem("code_forbidden", _ptr(*base, "code_spans"), f"kind={kind} 不允许 code_spans")

    _check_evidence_list(card.get("evidence"), _ptr(*base, "evidence"), ctx, min_items=1)


def _check_span(span: Any, base: tuple[Any, ...], ctx: _Ctx) -> None:
    if not isinstance(span, dict):
        ctx.problem("missing_field", _ptr(*base, "code_spans"), "code_span 必须是对象")
        return
    for k in span:
        if k not in _SPAN_ALLOWED:
            ctx.problem("unknown_field", _ptr(*base, "code_spans", k), f"未知字段 {k}")
    path = _as_str(span.get("path"))
    if not path:
        ctx.problem("missing_field", _ptr(*base, "code_spans", "path"), "code_span 缺少 path")
        return
    start = span.get("start_line")
    end = span.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < 1:
        ctx.problem("bad_enum", _ptr(*base, "code_spans"), "code_span 行号必须为 >=1 的整数")
        return
    if end < start:
        ctx.problem("line_out_of_range", _ptr(*base, "code_spans"), "end_line < start_line")
        return
    lines = ctx.lines(path)
    if lines is not None and end > len(lines):
        ctx.problem("line_out_of_range", _ptr(*base, "code_spans"), f"行号超出文件范围（共 {len(lines)} 行）")


def _check_code_mismatch(spans: list[Any], code: str, base: tuple[Any, ...], ctx: _Ctx) -> None:
    """把 agent 提供的 code 与仓库实际切片逐段比对，不一致则 code_span_mismatch。"""
    ctx.code_checked += 1
    actual_parts: list[str] = []
    missing = False
    for span in spans:
        if not isinstance(span, dict):
            return
        path = _as_str(span.get("path"))
        lines = ctx.lines(path)
        if lines is None:
            missing = True
            continue
        s = max(1, int(span.get("start_line", 1)))
        e = min(len(lines), int(span.get("end_line", s)))
        actual_parts.append("\n".join(lines[s - 1 : e]))
    if missing:
        return  # 文件级问题已在 _check_span 里报过，不重复计入 mismatch
    actual = _norm(actual_parts)
    given = _norm([code])
    if actual and given and actual != given:
        ctx.code_mismatch += 1
        ctx.problem("code_span_mismatch", _ptr(*base, "code"), "提供的 code 与仓库代码不一致")


def _norm(parts: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        for line in part.splitlines():
            if _ELISION_RE.match(line):
                continue
            stripped = line.strip()
            if stripped:
                out.append(stripped)
    return "\n".join(out)


def _axis_signature(text: str) -> str:
    """五轴雷同判定的归一化指纹：去掉标点与空白后剩下的字符。

    只改标点或空格的改写不算回答了另一个维度的问题，那种改写是蒙混
    （G23 的实测动机：urllib3 报告五轴一字不差，校验器当时不报错）。
    """
    return "".join(ch for ch in text if ch not in AXIS_NEGLECT).lower()


def _check_evidence_list(evs: Any, where: str, ctx: _Ctx, *, min_items: int) -> None:
    if not isinstance(evs, list):
        return
    if len(evs) < min_items:
        ctx.problem("missing_field", where, f"evidence 至少 {min_items} 条")
    for k, ev in enumerate(evs):
        if not isinstance(ev, dict):
            ctx.problem("missing_field", f"{where}/{k}", "evidence 必须是对象")
            continue
        path = _as_str(ev.get("path"))
        if not path:
            ctx.problem("missing_field", f"{where}/{k}/path", "evidence 缺少 path")
            continue
        ctx.lines(path)  # 触发一次解析
        for f in ev:
            if f not in _EVIDENCE_ALLOWED:
                ctx.problem("unknown_field", f"{where}/{k}/{f}", f"未知字段 {f}")
        s = ev.get("start_line")
        e = ev.get("end_line")
        if s is not None and (not isinstance(s, int) or s < 1):
            ctx.problem("bad_enum", f"{where}/{k}/start_line", "start_line 必须为 >=1 的整数")
        if e is not None and (not isinstance(e, int) or e < 1):
            ctx.problem("bad_enum", f"{where}/{k}/end_line", "end_line 必须为 >=1 的整数")
        _check_evidence_path(path, f"{where}/{k}/path", s, e, ctx)


def _check_evidence_path(path: str, where: str, s: Any, e: Any, ctx: _Ctx) -> None:
    if ctx.repo_root is None:
        return
    lines = ctx.lines(path)
    if lines is None:
        ctx.problem("bad_evidence_path", where, f"仓库中不存在文件：{path}")
        return
    if isinstance(s, int) and s > len(lines):
        ctx.problem("line_out_of_range", where, f"start_line 超出文件范围（共 {len(lines)} 行）")
    if isinstance(e, int) and e > len(lines):
        ctx.problem("line_out_of_range", where, f"end_line 超出文件范围（共 {len(lines)} 行）")


def _check_len(val: Any, minimum: int, base: tuple[Any, ...], ctx: _Ctx) -> None:
    text = _as_str(val)
    if count_units(text) < minimum:
        ctx.problem("principle_too_short", _ptr(*base), f"字段过短（需 >= {minimum} 单位）")


def _check_len_units(val: Any, minimum: int, base: tuple[Any, ...], ctx: _Ctx) -> None:
    text = _as_str(val)
    if count_units(text) < minimum:
        ctx.problem("principle_too_short", _ptr(*base), f"字段过短（需 >= {minimum} 单位）")


def _counts(report: Any) -> dict[str, Any]:
    """汇总计数（docs/mcp-tools.md T6 counts 形状）。"""
    feats = report.get("features", []) if isinstance(report, dict) and isinstance(report.get("features"), list) else []
    n_cards = 0
    n_spans = 0
    n_ev = 0
    # G23：质量比率全部真算，不写死（写死=把定义锁成常量，将来放宽校验会静默说谎）
    axes_total = 0
    axes_filled = 0
    axes_distinct = 0
    cards_with_ev = 0
    cards_reusable = 0
    principle_units: dict[str, int] = {axis: 0 for axis in PRINCIPLE_KEYS}
    for feat in feats:
        if not isinstance(feat, dict):
            continue
        n_ev += len(feat.get("evidence") or [])
        principles = feat.get("principles")
        if isinstance(principles, dict):
            sigs: list[str] = []
            for axis in PRINCIPLE_KEYS:
                val = principles.get(axis)
                if isinstance(val, dict):
                    val = val.get("detail") or val.get("summary") or ""
                text = _as_str(val)
                principle_units[axis] += count_units(text)
                axes_total += 1
                sig = _axis_signature(text)
                if sig:
                    axes_filled += 1
                    sigs.append(sig)
            axes_distinct += len(set(sigs))
        for card in feat.get("cards") or []:
            if not isinstance(card, dict):
                continue
            n_cards += 1
            n_spans += len(card.get("code_spans") or [])
            card_ev = card.get("evidence") or []
            n_ev += len(card_ev)
            if card_ev:
                cards_with_ev += 1
            if card.get("reusable"):
                cards_reusable += 1
    for key in ("characteristics", "cross_feature_risks"):
        for item in report.get(key, []) or []:
            if isinstance(item, dict):
                n_ev += len(item.get("evidence") or [])
    return {
        "features": len(feats),
        "cards": n_cards,
        "code_spans": n_spans,
        "evidence_refs": n_ev,
        "principle_units": principle_units,
        "axis_completeness": axes_filled / axes_total if axes_total else 0.0,
        "evidence_coverage": cards_with_ev / n_cards if n_cards else 0.0,
        "axis_diversity": axes_distinct / axes_total if axes_total else 0.0,
        "reusable_rate": cards_reusable / n_cards if n_cards else 0.0,
    }
