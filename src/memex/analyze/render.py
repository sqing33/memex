"""把报告对象渲染成 Markdown（固定 4 个 H2，report-contract.md）。"""

from __future__ import annotations

from typing import Any

AXIS_LABELS = [
    ("runtime_control_flow", "Runtime / Control Flow"),
    ("data_flow", "Data Flow"),
    ("state_lifecycle", "State & Lifecycle"),
    ("failure_recovery", "Failure Recovery"),
    ("concurrency_timing", "Concurrency & Timing"),
]

H1_CHARACTERISTICS = "Project Characteristics and Signature Implementations"
H2_SUMMARY = "Executive Principle Summary"
H3_FEATURES = "Feature Principle Analysis"
H4_RISKS = "Cross-feature Coupling and System Risks"
BT = chr(96)


def _axis_text(val: Any) -> str:
    if isinstance(val, dict):
        return str(val.get("detail") or val.get("summary") or "")
    return str(val or "")


def render_report_md(report: dict[str, Any]) -> str:
    out: list[str] = []
    out.append("## " + H1_CHARACTERISTICS + "\n")
    if report.get("one_liner"):
        out.append(str(report["one_liner"]) + "\n")
    for c in report.get("characteristics", []):
        out.append("### " + str(c.get("title", "")) + "\n")
        out.append(str(c.get("detail", "")) + "\n")
        out.extend(_render_evidence(c.get("evidence", [])))
        out.append("")

    out.append("## " + H2_SUMMARY + "\n")
    for f in report.get("features", []):
        axes = "; ".join(label + ": " + _axis_text((f.get("principles") or {}).get(key, "")) for key, label in AXIS_LABELS)
        out.append("- **" + str(f.get("title", "")) + "** — " + axes)
    out.append("")

    out.append("## " + H3_FEATURES + "\n")
    for f in report.get("features", []):
        out.append("### " + str(f.get("title", "")) + "\n")
        out.append(str(f.get("summary", "")) + "\n")
        out.append("*Intent:* " + str(f.get("intent", "")) + "\n")
        for key, label in AXIS_LABELS:
            out.append("- **" + label + "**: " + _axis_text((f.get("principles") or {}).get(key, "")))
        out.append("")
        for card in f.get("cards", []):
            out.append("#### " + str(card.get("title", "")) + " (" + str(card.get("kind", "")) + ")")
            out.append(str(card.get("summary", "")) + "\n")
            out.append(str(card.get("mechanism_desc", "")))
            out.extend(_render_evidence(card.get("evidence", [])))
            out.append("")

    out.append("## " + H4_RISKS + "\n")
    for r in report.get("cross_feature_risks", []):
        out.append("### " + str(r.get("title", "")) + "\n")
        out.append(str(r.get("detail", "")) + "\n")
        out.extend(_render_evidence(r.get("evidence", [])))
        out.append("")
    return "\n".join(out).strip() + "\n"


def _render_evidence(evs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if evs:
        lines.append("")
        for ev in evs:
            loc = str(ev.get("path")) + ":" + str(ev.get("start_line")) + "-" + str(ev.get("end_line"))
            lines.append("- evidence: " + BT + loc + BT)
    return lines
