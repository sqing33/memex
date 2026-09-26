# -*- coding: utf-8 -*-
"""静态目录页 + 单仓详情页 + 跨仓模式页（decisions.md C11 / tech-design.md §2.7）。

三层页面，纯静态 HTML（内联 CSS、无外链资源、无 JS 依赖）：
- `out/index.html`：知识库总览 + 全部仓库一行一个；
- `out/<repo_id>.html`：单仓详情（元数据 + 历次分析 + 每次分析的报告正文）；
- `out/patterns.html`：跨仓模式（成员卡片 + 分值 + 来源仓）。

不引入 Node/Vite，也不引入模板引擎——用标准库 string.Template 手写。
"""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from string import Template
from typing import Any

from ..core import Config, MemexError, Paths
from ..store import db

# —— 缺值占位：用破折号，避免把「无 / N/A」这类被禁占位符写进产物 ——
_DASH = "—"


def _e(value: Any) -> str:
    """HTML 转义（含引号）；None 视作空串。"""
    return html.escape("" if value is None else str(value), quote=True)


def _dash(value: Any) -> str:
    """标量字段渲染：空值显示破折号，其余转义。"""
    if value is None or value == "":
        return _DASH
    return _e(value)


def _score_line(member: dict[str, Any]) -> str:
    """成员脚注：相似度 + 可选符号。两个都要有才用间隔点连。"""
    parts = ["相似度 " + _score(member.get("score"))]
    symbol = str(member.get("symbol") or "")
    if symbol:
        parts.append(_e(symbol))
    return " · ".join(parts)


def _score(value: Any) -> str:
    """相似度渲染：三位小数。

    直接把 REAL 列交给 str() 会印出 `0.6734730638210827`——
    这种全精度浮点在页面上没有任何信息量，只会把两行挤成一样长。
    """
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return _DASH

# —— 页面外壳：内联 CSS，无任何外部资源引用（C11）——
_PAGE = Template(
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
:root { --fg:#1f2328; --muted:#59636e; --line:#d8dee4; --bg:#fff; --accent:#0969da; --warn:#9a6700; }
* { box-sizing: border-box; }
body { margin:0 auto; max-width:1040px; padding:24px 20px 48px; color:var(--fg); background:var(--bg);
       font:15px/1.6 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:18px; margin:28px 0 10px; }
h3 { font-size:16px; margin:20px 0 8px; }
h4 { font-size:15px; margin:18px 0 6px; }
h5 { font-size:14px; margin:0; font-weight:600; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.sub { color:var(--muted); margin:0 0 20px; }
.overview { display:flex; flex-wrap:wrap; gap:12px; margin:0 0 24px; }
.stat { flex:1 1 140px; border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
.stat .num { display:block; font-size:22px; font-weight:600; }
.stat .label { color:var(--muted); font-size:13px; }
table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-size:13px; font-weight:600; }
.muted { color:var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:13px; }
.tag { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:0 8px; font-size:12px; color:var(--muted); }
.tag.warn { color:var(--warn); border-color:var(--warn); }
.crumb { margin:0 0 16px; }
.footer { margin-top:40px; color:var(--muted); font-size:13px; border-top:1px solid var(--line); padding-top:12px; }
dl { display:grid; grid-template-columns:160px 1fr; gap:4px 12px; margin:0; }
dt { color:var(--muted); }
dd { margin:0; }
$extra_css
</style>
</head>
<body>
$body
<p class="footer">由 <span class="mono">memex export-site</span> 生成 · 静态站点（C11）· 生成时间 $generated</p>
</body>
</html>
"""
)

# —— 正文 CSS（分析内容与模式）——
_EXTRA_CSS = """
.analysis { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:0 0 16px; }
.report { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
.lead { font-size:15px; }
.feature-block { border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin:0 0 16px; }
.feature-block h4.feature { margin:0 0 6px; font-size:16px; }
.cards { margin-top:10px; }
.card { border-left:3px solid var(--line); padding:6px 0 6px 12px; margin:0 0 12px; }
.card-head { display:flex; align-items:baseline; flex-wrap:wrap; gap:8px; }
.badge { border:1px solid var(--line); border-radius:4px; padding:0 6px; font-size:12px; color:var(--muted); }
.en { color:var(--muted); font-size:14px; }
.intent { color:var(--muted); font-size:14px; }
dl.axes { grid-template-columns:140px 1fr; }
ul.ev { margin:4px 0 8px; padding-left:20px; }
ul.ev li { margin:0 0 2px; }
ul.eps { margin:4px 0 8px; padding-left:20px; }
.note { color:var(--muted); font-size:13px; }
pre.md { background:#f6f8fa; border:1px solid var(--line); border-radius:8px; padding:12px;
        overflow-x:auto; font-size:13px; line-height:1.5; white-space:pre-wrap; }
details.raw { margin:0 0 12px; }
details.raw > summary { cursor:pointer; color:var(--muted); }
"""

_INDEX_BODY = Template(
    """<h1>memex 仓库目录</h1>
<p class="sub">已收录仓库清单 · <a href="patterns.html">跨仓模式</a></p>
<section class="overview">
<div class="stat"><span class="num">$n_repos</span><span class="label">仓库</span></div>
<div class="stat"><span class="num">$n_cards</span><span class="label">卡片</span></div>
<div class="stat"><span class="num">$n_patterns</span><span class="label">模式</span></div>
</section>
<table>
<thead><tr><th>仓库</th><th>语言</th><th>Stars</th><th>License</th><th>分析</th><th>状态</th></tr></thead>
<tbody>
$rows</tbody>
</table>
"""
)

_INDEX_ROW = Template(
    """<tr>
<td><a href="$href">$full_name</a><div class="muted">$description</div></td>
<td>$language</td>
<td>$stars</td>
<td>$license</td>
<td><span class="mono">$commit</span><div class="muted">$counts</div></td>
<td>$tags</td>
</tr>
"""
)

_REPO_BODY = Template(
    """<p class="crumb"><a href="index.html">← 仓库目录</a> · <a href="patterns.html">跨仓模式</a></p>
<h1>$full_name</h1>
<p class="sub"><a href="$url">$url</a></p>
<p>$flags</p>
<dl>
<dt>语言</dt><dd>$language</dd>
<dt>Stars</dt><dd>$stars</dd>
<dt>License</dt><dd>$license</dd>
<dt>托管</dt><dd>$host</dd>
<dt>默认分支</dt><dd>$default_branch</dd>
<dt>来源</dt><dd>$source</dd>
</dl>
$description_block
<h2>分析</h2>
$analyses
"""
)

_ANALYSIS_BLOCK = Template(
    """<div class="analysis">
<p><span class="mono">$commit</span> · $created_at · <span class="tag">$producer</span> <span class="tag">$status</span>$reindex_tag</p>
<dl>
<dt>分析者</dt><dd>$analyst</dd>
<dt>功能数</dt><dd>$features</dd>
<dt>卡片数</dt><dd>$cards</dd>
<dt>证据数</dt><dd>$evidence</dd>
<dt>质量</dt><dd>$quality</dd>
</dl>
<div class="report">$report</div>
$raw
</div>
"""
)

_PATTERNS_BODY = Template(
    """<p class="crumb"><a href="index.html">← 仓库目录</a></p>
<h1>跨仓模式</h1>
<p class="sub">出现在两个及以上仓库里的同类机制 · 阈值 $threshold · 共 $n_patterns 个</p>
$blocks
"""
)

_PATTERN_BLOCK = Template(
    """<section class="feature-block">
<h4 class="feature">$title</h4>
<p class="muted">来自 $n_repos 个仓库 · $card_count 张卡片</p>
<ul class="ev">
$members
</ul>
</section>
"""
)

_PATTERN_MEMBER = Template(
    """<li><a href="$href">$repo</a> <span class="badge">$kind</span> $title
<div class="note">$score_line</div>
<div class="note">$summary</div>
</li>
"""
)

# —— 五轴 / 卡片 kind 的中文名（顺序即契约固定顺序）——
_AXIS_LABELS: tuple[tuple[str, str], ...] = (
    ("runtime_control_flow", "运行 / 控制流"),
    ("data_flow", "数据流"),
    ("state_lifecycle", "状态与生命周期"),
    ("failure_recovery", "失败恢复"),
    ("concurrency_timing", "并发与时序"),
)

_KIND_LABELS: dict[str, str] = {
    "mechanism": "机制",
    "snippet": "片段",
    "skeleton": "骨架",
    "gotcha": "陷阱",
    "decision": "决策",
}


def _short_sha(sha: Any) -> str:
    """commit 短哈希（前 10 位）；空则破折号。"""
    s = str(sha or "")
    return s[:10] if s else _DASH


def _as_dict(value: Any) -> dict[str, Any]:
    parsed = db.json_loads(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _repo_tags(r: sqlite3.Row, a: sqlite3.Row | None) -> str:
    """状态标记：stale / fork / reindex pending。"""
    tags: list[str] = []
    if int(r["is_stale"] or 0):
        tags.append('<span class="tag warn">stale</span>')
    if int(r["is_fork"] or 0):
        tags.append('<span class="tag">fork</span>')
    if a is not None and a["reindex_state"] == "pending":
        tags.append('<span class="tag warn">reindex pending</span>')
    return " ".join(tags) if tags else '<span class="muted">' + _DASH + "</span>"


def _counts_brief(a: sqlite3.Row | None) -> str:
    """目录页用的一行 counts（来自 analyses.counts）：功能 / 卡片 / 证据。"""
    if a is None:
        return "尚未分析"
    c = _as_dict(a["counts_json"])
    return "功能 " + _dash(c.get("features")) + " · 卡片 " + _dash(c.get("cards")) + " · 证据 " + _dash(c.get("evidence"))


def _render_index_row(r: sqlite3.Row, a: sqlite3.Row | None, fname: str) -> str:
    desc = _e(r["description"]) if r["description"] else '<span class="muted">' + _DASH + "</span>"
    return _INDEX_ROW.substitute(
        href=_e(fname + ".html"),
        full_name=_e(r["full_name"]),
        description=desc,
        language=_dash(r["language"]),
        stars=_dash(r["stars"]),
        license=_dash(r["license"]),
        commit=_short_sha(a["commit_sha"]) if a is not None else _DASH,
        counts=_counts_brief(a),
        tags=_repo_tags(r, a),
    )


def _render_evidence(ev: Any) -> str:
    """证据列表：路径:起-止 · symbol · 说明。空与非列表都当没有。"""
    if not isinstance(ev, list):
        return ""
    items = [x for x in ev if isinstance(x, dict)]
    if not items:
        return ""
    lines: list[str] = []
    for e in items:
        path = str(e.get("path") or "")
        start = e.get("start_line")
        end = e.get("end_line")
        if start is None:
            loc = _DASH
        elif end is None or end == start:
            loc = str(start)
        else:
            loc = str(start) + "-" + str(end)
        head = '<span class="mono">' + _e(path) + "</span>:" + _e(loc)
        sym = str(e.get("symbol") or "")
        if sym:
            head += ' <span class="muted">' + _e(sym) + "</span>"
        note = str(e.get("note") or "")
        if note:
            head += '<div class="note">' + _e(note) + "</div>"
        lines.append("<li>" + head + "</li>")
    return '<ul class="ev">' + "".join(lines) + "</ul>"


def _render_tags(tags: Any) -> str:
    if not isinstance(tags, list):
        return ""
    return "".join('<span class="tag">' + _e(t) + "</span>" for t in tags if str(t or ""))

def _text_of(value: Any) -> str:
    """从 characteristics/principles 这类节点取正文。

    报告里同一类节点有两种形态：纯字符串，或 {detail|summary, evidence}。两种都接住；
    都取不到就返回空串，交调用方显示破折号（而不是编一句「暂无」）。
    """
    if isinstance(value, dict):
        return str(value.get("detail") or value.get("summary") or "")
    return str(value or "")


def _card_block(card: Any) -> str:
    """一张卡片：kind 标记 + 标题 + 中文摘要 + 英文机制说明 + 证据 + 标签。"""
    if not isinstance(card, dict):
        return ""
    kind = str(card.get("kind") or "")
    label = _KIND_LABELS.get(kind, kind)
    badge = '<span class="badge">' + _e(label) + "</span>" if label else ""
    parts: list[str] = ['<div class="card-head">', badge, "<h5>" + _e(card.get("title")) + "</h5>",
                       _render_tags(card.get("tags")), "</div>"]
    summary = str(card.get("summary") or "")
    if summary:
        parts.append("<p>" + _e(summary) + "</p>")
    mech = str(card.get("mechanism_desc") or "")
    if mech:
        # 机制说明强制英文（要建向量才语言中立），标注出来免得读者当成漏翻
        parts.append('<p class="en">' + _e(mech) + "</p>")
    parts.append(_render_evidence(card.get("evidence")))
    return '<div class="card">' + "".join(parts) + "</div>"


def _feature_block(feature: Any) -> str:
    """一个功能：摘要 + intent + 五轴正文 + 该功能下的卡片。"""
    if not isinstance(feature, dict):
        return ""
    parts: list[str] = ['<h4 class="feature">' + _e(feature.get("title")) + "</h4>"]
    summary = str(feature.get("summary") or "")
    if summary:
        parts.append("<p>" + _e(summary) + "</p>")
    intent = str(feature.get("intent") or "")
    if intent:
        parts.append('<p class="intent"><span class="muted">意图</span> ' + _e(intent) + "</p>")
    principles = feature.get("principles")
    if isinstance(principles, dict):
        rows: list[str] = []
        for key, label in _AXIS_LABELS:
            raw = principles.get(key)
            text = _text_of(raw)
            ev = _render_evidence(raw.get("evidence") if isinstance(raw, dict) else None)
            if not text and not ev:
                continue
            rows.append("<dt>" + _e(label) + "</dt><dd>" + (_e(text) if text else _DASH) + ev + "</dd>")
        if rows:
            parts.append('<dl class="axes">' + "".join(rows) + "</dl>")
    cards = [c for c in (feature.get("cards") or []) if isinstance(c, dict)]
    if cards:
        parts.append('<div class="cards">' + "".join(_card_block(c) for c in cards) + "</div>")
    return '<section class="feature-block">' + "".join(parts) + "</section>"


def _render_report_body(report: Any) -> str:
    """一次分析的报告正文：一句话特征 + 入口 + 功能卡片 + 跨功能风险。"""
    if not isinstance(report, dict):
        return '<p class="muted">该次分析没有结构化报告正文。</p>'
    parts: list[str] = []
    one = str(report.get("one_liner") or "")
    if one:
        parts.append('<p class="lead">' + _e(one) + "</p>")
    chars = [c for c in (report.get("characteristics") or []) if isinstance(c, dict)]
    if chars:
        parts.append("<h4>项目特征</h4>")
        for c in chars:
            title = str(c.get("title") or "")
            if title:
                parts.append("<p><b>" + _e(title) + "</b></p>")
            detail = str(c.get("detail") or "")
            if detail:
                parts.append("<p>" + _e(detail) + "</p>")
            parts.append(_render_evidence(c.get("evidence")))
    eps = [e for e in (report.get("entry_points") or []) if isinstance(e, dict)]
    if eps:
        parts.append("<h4>入口</h4>")
        parts.append('<ul class="eps">')
        for e in eps:
            item = ['<li><span class="mono">' + _e(e.get("path")) + "</span>"]
            if e.get("kind"):
                item.append(' <span class="muted">' + _e(e.get("kind")) + "</span>")
            role = str(e.get("role") or "")
            if role:
                item.append('<div class="note">' + _e(role) + "</div>")
            item.append("</li>")
            parts.append("".join(item))
        parts.append("</ul>")
    feats = [f for f in (report.get("features") or []) if isinstance(f, dict)]
    if feats:
        parts.append("<h4>功能与卡片</h4>")
        parts.extend(_feature_block(f) for f in feats)
    risks = [x for x in (report.get("cross_feature_risks") or []) if isinstance(x, dict)]
    if risks:
        parts.append("<h4>跨功能耦合与系统风险</h4>")
        for x in risks:
            title = str(x.get("title") or "")
            if title:
                parts.append("<p><b>" + _e(title) + "</b></p>")
            detail = str(x.get("detail") or "")
            if detail:
                parts.append("<p>" + _e(detail) + "</p>")
            parts.append(_render_evidence(x.get("evidence")))
    if not parts:
        return '<p class="muted">该次分析没有结构化报告正文。</p>'
    return "".join(parts)


def _render_raw_md(md: Any) -> str:
    """report_md 收进折叠区：它是产物快照，不是结构化内容的替代品。"""
    text = str(md or "").strip()
    if not text:
        return ""
    return ('<details class="raw"><summary>完整 Markdown 报告原文（' + _e(str(len(text)))
            + " 字符）</summary><pre class=" + chr(34) + "md" + chr(34) + ">" + _e(text) + "</pre></details>")

def _render_analysis_block(a: sqlite3.Row) -> str:
    counts = _as_dict(a["counts_json"])
    quality = _as_dict(a["quality_json"])
    q = " · ".join(_e(k) + "=" + _e(v) for k, v in sorted(quality.items())) or _DASH
    reindex = ' <span class="tag warn">reindex pending</span>' if a["reindex_state"] == "pending" else ""
    report = db.json_loads(a["report_json"], None)
    return _ANALYSIS_BLOCK.substitute(
        commit=_short_sha(a["commit_sha"]),
        created_at=_dash(a["created_at"]),
        producer=_e(a["producer"]),
        status=_e(a["status"]),
        reindex_tag=reindex,
        analyst=_dash(a["analyst"]),
        features=_dash(counts.get("features")),
        cards=_dash(counts.get("cards")),
        evidence=_dash(counts.get("evidence")),
        quality=q,
        report=_render_report_body(report),
        raw=_render_raw_md(a["report_md"]),
    )


def _render_repo_page(r: sqlite3.Row, analyses: list[sqlite3.Row]) -> str:
    blocks = "".join(_render_analysis_block(x) for x in analyses) or '<p class="muted">尚未分析。</p>'
    desc = "<p>" + _e(r["description"]) + "</p>" if r["description"] else ""
    return _REPO_BODY.substitute(
        full_name=_e(r["full_name"]),
        url=_e(r["url"]),
        flags=_repo_tags(r, analyses[0] if analyses else None),
        language=_dash(r["language"]),
        stars=_dash(r["stars"]),
        license=_dash(r["license"]),
        host=_e(r["host"]),
        default_branch=_dash(r["default_branch"]),
        source=_dash(r["source"]),
        description_block=desc,
        analyses=blocks,
    )


def _render_pattern_page(rows: list[sqlite3.Row]) -> str:
    """跨仓模式页。

    每个模式一行块：标题、覆盖仓数、成员卡片（来源仓 + kind + 相似度 + 摘要）。
    模式是跨仓侧的单位，成员必然来自不同仓库——单仓页里看不到这个视角。
    """
    blocks: list[str] = []
    for r in rows:
        # members_json 是**数组**：_as_dict 遇到非 dict 一律返回 {}，
        # 拿它读列表会静默渲染出空模式页（第一版就这么翻的）。
        # 要什么形态就用什么形态的读取器。
        parsed = db.json_loads(r["members_json"], [])
        members = parsed if isinstance(parsed, list) else []
        items: list[str] = []
        for m in members:
            if not isinstance(m, dict):
                continue
            fname = _page_filename(str(m.get("repo_id") or ""))
            href = _e(fname + ".html") if fname else _e("index.html")
            kind = str(m.get("kind") or "")
            items.append(_PATTERN_MEMBER.substitute(
                href=href,
                repo=_e(m.get("repo_full_name")),
                kind=_e(_KIND_LABELS.get(kind, kind)),
                title=_e(m.get("title")),
                score_line=_score_line(m),
                summary=_e(m.get("summary")),
            ))
        blocks.append(_PATTERN_BLOCK.substitute(
            title=_e(r["title"]),
            n_repos=_dash(r["repo_count"]),
            card_count=_dash(r["card_count"]),
            members="".join(items),
        ))
    body = "".join(blocks) or '<p class="muted">还没有聚出跨仓模式（至少需要两个仓库各贡献一张卡片）。</p>'
    return _PATTERNS_BODY.substitute(
        threshold=_e(r"0.60"),
        n_patterns=len(rows),
        blocks=body,
    )


def _load_patterns(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """把 patterns + pattern_members 拼成渲染要的行（members 放在 members_json）。"""
    pat_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(patterns)")}
    if not {"pattern_id", "title", "card_count", "repo_count"} <= pat_cols:
        return []
    pats = list(conn.execute(
        "SELECT pattern_id, title, card_count, repo_count FROM patterns ORDER BY card_count DESC, title ASC"
    ).fetchall())
    if not pats:
        return []
    by_id: dict[str, dict[str, Any]] = {str(p["pattern_id"]): {"members": []} for p in pats}
    for m in conn.execute(
        "SELECT pm.pattern_id AS pattern_id, pm.score AS score, c.card_id AS card_id, c.kind AS kind, "
        "c.title AS title, c.summary AS summary, c.symbol AS symbol, "
        "a.repo_id AS repo_id, r.full_name AS repo_full_name "
        "FROM pattern_members pm "
        "JOIN cards c ON c.card_id = pm.card_id "
        "JOIN features f ON f.feature_id = c.feature_id "
        "JOIN analyses a ON a.analysis_id = f.analysis_id "
        "LEFT JOIN repos r ON r.repo_id = a.repo_id "
        "ORDER BY pm.score DESC"
    ):
        target = by_id.get(str(m["pattern_id"]))
        if target is None:
            continue
        target["members"].append({
            "card_id": m["card_id"], "score": m["score"], "kind": m["kind"],
            "title": m["title"], "summary": m["summary"], "symbol": m["symbol"],
            "repo_id": m["repo_id"], "repo_full_name": m["repo_full_name"],
        })
    out: list[sqlite3.Row] = []
    conn.row_factory = sqlite3.Row
    for p in pats:
        row = conn.execute("SELECT ? AS title, ? AS card_count, ? AS repo_count, ? AS members_json",
                           (p["title"], p["card_count"], p["repo_count"],
                            db.json_dumps(by_id[str(p["pattern_id"])]["members"])),
        ).fetchone()
        if row is not None:
            out.append(row)
    return out

def _page_filename(repo_id: str) -> str | None:
    """把 repo_id 重写成一个安全的产物文件名。

    库里的 repo_id 没有 DDL 约束（TEXT PRIMARY KEY），本函数是最后一道护栏：
    返回 None 表示这个 repo_id 写不进文件名（调用方应跳过并计数），
    返回的字符串保证不含路径分隔符、不是 . / ..、且不超过 255 字节。

    正常仓的 repo_id 是 host__owner__name，原样返回，行为不变。
    """
    name = repo_id
    if not name or name in (".", ".."):
        return None
    # 只保留文件名里合法的字符；其余换成下划线而不是丢掉（丢掉会把 a/b 与
    # a_b 都压成 ab）。真正的防碰撞在调用方：改写结果与原值不一致的一律跳过，
    # 因此真正被写盘的 id 集合上这个映射是恒等映射，天然不碰撞。
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if cleaned in ("", ".", ".."):
        return None
    # 截断按 UTF-8 字节算（文件系统上限是 255 字节，不是 255 个字符），
    # 且回退到字符边界，别把多字节字符切成乱码。
    if len(cleaned.encode("utf-8")) <= 255:
        return cleaned
    cut = cleaned.encode("utf-8")[:255]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return None


def export_site(paths: Paths | None = None, cfg: Config | None = None, *, out: str | None = None) -> dict[str, Any]:
    """导出静态站点（C11）：目录页 + 单仓详情页 + 跨仓模式页。

    生成 `out/index.html`、`out/patterns.html` 与 `out/<repo_id>.html`，纯静态、内联 CSS、
    无外链资源、无 JS 依赖。
    返回 {out_dir, repos, files, skipped_repo_ids, patterns}；repos 是真正导出的仓数，
    脏 repo_id 计入 skipped_repo_ids 而不计入 repos。

    `out` 缺省为 `$MEMEX_HOME/site/`（Paths.site）。
    `paths` / `cfg` 可省略（CLI 只传 out 时自动取默认环境配置）。
    """
    # CLI 形态 export_site(out=...) 会省略 paths/cfg；此处补默认值，
    # 与任务给定的三参签名兼容（显式传入时行为不变）。
    del cfg  # 站点只读真源库，不需要配置快照；保留入参以对齐接口。
    if paths is None:
        paths = Paths.default()
    out_dir = Path(out).expanduser() if out else paths.site
    if not paths.db.exists():
        raise MemexError(
            "not_found",
            "知识库不存在，请先运行 memex init 建库",
            {"db": str(paths.db)},
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = db.utcnow()

    conn = db.connect(str(paths.db))
    try:
        repo_rows: list[sqlite3.Row] = list(conn.execute(
            "SELECT repo_id, full_name, url, host, description, language, stars, license, "
            "is_stale, is_fork, fork_of, default_branch, source FROM repos "
            "ORDER BY full_name COLLATE NOCASE ASC"
        ).fetchall())
        # 脊柱 DDL 是否已含 reindex_state 尚不确定（docs 要求、db.py 视版本而定）：
        # 读侧按实际列动态选择，列缺失时以 NULL 顶替，站点始终可用。
        an_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(analyses)")}
        reindex_expr = "reindex_state" if "reindex_state" in an_cols else "NULL AS reindex_state"
        analysis_rows: list[sqlite3.Row] = list(conn.execute(
            "SELECT repo_id, commit_sha, contract_version, analyst, producer, status, "
            "counts_json, quality_json, created_at, finished_at, report_json, report_md, "
            + reindex_expr + " FROM analyses "
            "ORDER BY created_at DESC"
        ).fetchall())
        n_cards = int(conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"])
        n_patterns = int(conn.execute("SELECT COUNT(*) AS n FROM patterns").fetchone()["n"])
        pattern_rows = _load_patterns(conn)
    finally:
        conn.close()

    # 每个仓库取最新一次分析（SQL 已按 created_at 降序，首次命中即最新）
    by_repo: dict[str, list[sqlite3.Row]] = {}
    for row in analysis_rows:
        by_repo.setdefault(str(row["repo_id"]), []).append(row)

    files: list[str] = []
    rows_html: list[str] = []
    skipped: list[str] = []
    for r in repo_rows:
        rid = str(r["repo_id"])
        fname = _page_filename(rid)
        if fname is None or fname != rid:
            # 脏 repo_id：显式跳过并记账，绝不静默写出一个可疑文件。
            skipped.append(rid)
            continue
        latest = by_repo[rid][0] if rid in by_repo else None
        rows_html.append(_render_index_row(r, latest, fname))
        body = _render_repo_page(r, by_repo.get(rid, []))
        page = _PAGE.substitute(
            title=_e(str(r["full_name"]) + " · memex"),
            body=body,
            generated=generated,
            extra_css=_EXTRA_CSS,
        )
        p = out_dir / (fname + ".html")
        p.write_text(page, encoding="utf-8")
        files.append(str(p))

    index_body = _INDEX_BODY.substitute(
        n_repos=len(repo_rows) - len(skipped),
        n_cards=n_cards,
        n_patterns=n_patterns,
        rows="".join(rows_html),
    )
    index_page = _PAGE.substitute(title="memex · 仓库目录", body=index_body, generated=generated,
                                 extra_css=_EXTRA_CSS)
    index_path = out_dir / "index.html"
    index_path.write_text(index_page, encoding="utf-8")
    files.insert(0, str(index_path))

    pat_body = _render_pattern_page(pattern_rows)
    pat_page = _PAGE.substitute(title="memex · 跨仓模式", body=pat_body, generated=generated,
                              extra_css=_EXTRA_CSS)
    pat_path = out_dir / "patterns.html"
    pat_path.write_text(pat_page, encoding="utf-8")
    files.insert(1, str(pat_path))

    return {"out_dir": str(out_dir), "repos": len(repo_rows) - len(skipped), "files": files,
                "patterns": len(pattern_rows), "skipped_repo_ids": skipped}
