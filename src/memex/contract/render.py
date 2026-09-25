"""报告渲染：把契约 JSON 渲染成固定 4 个 H2 的 Markdown。

骨架顺序固定（docs/report-contract.md §2），保证跨仓报告可比、可聚类：
  1. Project Characteristics and Signature Implementations   <- characteristics
  2. Executive Principle Summary                              <- features 汇总（五轴速览）
  3. Feature Principle Analysis                               <- features 全量（含卡片）
  4. Cross-feature Coupling and System Risks                  <- cross_feature_risks

卡片的多段 code_spans 之间插入 `// … (elided) …`（A1）。
本模块只做「JSON -> 文本」，不做校验；渲染前应已过 validate_report。
"""
from __future__ import annotations

from typing import Any

from ..constants import PRINCIPLE_KEYS

H2_CHARACTERISTICS = "Project Characteristics and Signature Implementations"
H2_EXECUTIVE = "Executive Principle Summary"
H2_FEATURES = "Feature Principle Analysis"
H2_RISKS = "Cross-feature Coupling and System Risks"

# 五轴的中文名（渲染给人读的正文用母语，docs/report-contract.md §3）
AXIS_LABELS: dict[str, str] = {
    "runtime_control_flow": "运行/控制流",
    "data_flow": "数据流",
    "state_lifecycle": "状态生命周期",
    "failure_recovery": "失败与恢复",
    "concurrency_timing": "并发与时序",
}


def _evidence_line(ev: dict[str, Any]) -> str:
    """把 EvidenceRef 渲染成一行紧凑串（展示允许紧凑形式，契约里是对象）。"""
    path = ev.get("path", "?")
    s, e = ev.get("start_line"), ev.get("end_line")
    loc = path
    if isinstance(s, int):
        loc = f"{path}:{s}" + (f"-{e}" if isinstance(e, int) and e != s else "")
    bits = [loc]
    if ev.get("symbol"):
        bits.append(f"`{ev['symbol']}`")
    if ev.get("note"):
        bits.append(str(ev["note"]))
    return "- " + " — ".join(bits)


def _evidence_block(evs: Any) -> list[str]:
    out: list[str] = []
    if isinstance(evs, list):
        for ev in evs:
            if isinstance(ev, dict):
                out.append(_evidence_line(ev))
    return out


def _first_sentence(text: str, *, limit: int = 80) -> str:
    """取第一句，并截到 limit 字符（用于速览，避免与 §3 全量重复）。"""
    s = text.strip()
    for sep in ("。", ". ", "；", ";", "\n"):
        idx = s.find(sep)
        if 0 <= idx < limit:
            s = s[:idx]
            break
    return s[:limit] + ("…" if len(s) > limit else "")


def _span_text(sp: dict[str, Any]) -> str:
    path = sp.get("path", "?")
    s, e = sp.get("start_line"), sp.get("end_line")
    loc = path
    if isinstance(s, int):
        loc = f"{path}:{s}" + (f"-{e}" if isinstance(e, int) and e != s else "")
    sym = f" `{sp['symbol']}`" if sp.get("symbol") else ""
    return f"{loc}{sym}"


def _render_card(card: dict[str, Any]) -> list[str]:
    kind = card.get("kind", "?")
    reusable = "可复用" if card.get("reusable") else "不可复用"
    lines = [f"#### [{kind}] {card.get('title', '')}（{reusable}）"]
    if card.get("summary"):
        lines.append(str(card["summary"]))
    if card.get("mechanism_desc"):
        lines.append(f"> {card['mechanism_desc']}")
    if card.get("tags"):
        lines.append("标签：" + ", ".join(f"`{t}`" for t in card["tags"]))
    if card.get("language") or card.get("symbol"):
        meta = []
        if card.get("language"):
            meta.append(f"语言={card['language']}")
        if card.get("symbol"):
            meta.append(f"符号=`{card['symbol']}`")
        lines.append(" · ".join(meta))
    spans = card.get("code_spans")
    if isinstance(spans, list) and spans:
        segs = []
        for sp in spans:
            if isinstance(sp, dict):
                segs.append(_span_text(sp))
        lines.append("代码切片：" + " ⟂ ".join(segs))
        if len(segs) > 1:
            lines.append("（切片之间渲染时插 `// … (elided) …`）")
    ev = _evidence_block(card.get("evidence"))
    if ev:
        lines.append("证据：")
        lines.extend("  " + x for x in ev)
    return lines


def _render_feature_detail(feat: dict[str, Any], index: int) -> list[str]:
    lines = [f"### {index}. {feat.get('title', '')}", f"`{feat.get('key', '')}`"]
    if feat.get("summary"):
        lines.append(str(feat["summary"]))
    if feat.get("intent"):
        lines.append(f"> intent: {feat['intent']}")
    pr = feat.get("principles")
    if isinstance(pr, dict):
        for axis in PRINCIPLE_KEYS:
            if axis in pr:
                lines.append(f"- **{AXIS_LABELS.get(axis, axis)}**：{pr[axis]}")
    cards = feat.get("cards")
    if isinstance(cards, list) and cards:
        lines.append("")
        lines.append("**卡片**")
        for card in cards:
            if isinstance(card, dict):
                lines.extend(_render_card(card))
                lines.append("")
    ev = _evidence_block(feat.get("evidence"))
    if ev:
        lines.append("功能证据：")
        lines.extend("  " + x for x in ev)
    return lines


def render_report(report: dict[str, Any]) -> str:
    """契约 JSON -> Markdown（固定 4 个 H2，顺序不变）。"""
    lines: list[str] = ["# memex Report", ""]

    # ---- H2 #1 项目特征与招牌实现 ----
    lines.append(f"## {H2_CHARACTERISTICS}")
    lines.append("")
    if report.get("one_liner"):
        lines.append(str(report["one_liner"]))
        lines.append("")
    for ch in report.get("characteristics", []) or []:
        if not isinstance(ch, dict):
            continue
        lines.append(f"### {ch.get('title', '')}")
        if ch.get("detail"):
            lines.append(str(ch["detail"]))
        ev = _evidence_block(ch.get("evidence"))
        if ev:
            lines.extend(ev)
        lines.append("")
    eps = report.get("entry_points")
    if isinstance(eps, list) and eps:
        lines.append("### 入口与装配点")
        for ep in eps:
            if isinstance(ep, dict):
                kind = f" [{ep['kind']}]" if ep.get("kind") else ""
                lines.append(f"- `{ep.get('path', '')}`{kind} — {ep.get('role', '')}")
        lines.append("")

    # ---- H2 #2 执行摘要（五轴速览）----
    lines.append(f"## {H2_EXECUTIVE}")
    lines.append("")
    for feat in report.get("features", []) or []:
        if not isinstance(feat, dict):
            continue
        lines.append(f"### {feat.get('title', '')}（`{feat.get('key', '')}`）")
        if feat.get("summary"):
            lines.append(str(feat["summary"]))
        pr = feat.get("principles")
        if isinstance(pr, dict):
            for axis in PRINCIPLE_KEYS:
                if axis in pr:
                    lines.append(f"- **{AXIS_LABELS.get(axis, axis)}**：{_first_sentence(str(pr[axis]))}")
        lines.append("")

    # ---- H2 #3 功能原理分析（全量）----
    lines.append(f"## {H2_FEATURES}")
    lines.append("")
    for i, feat in enumerate(report.get("features", []) or [], start=1):
        if isinstance(feat, dict):
            lines.extend(_render_feature_detail(feat, i))
            lines.append("")

    # ---- H2 #4 跨功能耦合与系统风险 ----
    lines.append(f"## {H2_RISKS}")
    lines.append("")
    for rk in report.get("cross_feature_risks", []) or []:
        if not isinstance(rk, dict):
            continue
        lines.append(f"### {rk.get('title', '')}")
        if rk.get("detail"):
            lines.append(str(rk["detail"]))
        ev = _evidence_block(rk.get("evidence"))
        if ev:
            lines.extend(ev)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def check_headings(markdown: str) -> list[str]:
    """返回 Markdown 里出现的 H2 标题（用于校验「恰好 4 个、顺序正确」）。"""
    out: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            out.append(line[3:].strip())
    return out


EXPECTED_H2 = (H2_CHARACTERISTICS, H2_EXECUTIVE, H2_FEATURES, H2_RISKS)
