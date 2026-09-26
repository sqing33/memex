"""静态目录页导出（decisions.md C11 / tech-design.md §2.7）。

只做「已收录仓库清单」这一页（catalog）：从真源库读 repos 与 analyses，
生成纯静态 HTML（内联 CSS、无外链资源、无 JS 依赖）。
不引入 Node/Vite，也不引入模板引擎——用标准库 string.Template 手写。

输出：
- `out/index.html`：知识库总览 + 全部仓库一行一个；
- `out/<repo_id>.html`：单仓详情（元数据 + 历次分析）。
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
.analysis { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:0 0 12px; }
.footer { margin-top:40px; color:var(--muted); font-size:13px; border-top:1px solid var(--line); padding-top:12px; }
dl { display:grid; grid-template-columns:160px 1fr; gap:4px 12px; margin:0; }
dt { color:var(--muted); }
dd { margin:0; }
</style>
</head>
<body>
$body
<p class="footer">由 <span class="mono">memex export-site</span> 生成 · 静态目录页（C11）· 生成时间 $generated</p>
</body>
</html>
"""
)

_INDEX_BODY = Template(
    """<h1>memex 仓库目录</h1>
<p class="sub">已收录仓库清单（静态目录页，C11）</p>
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
    """<p class="crumb"><a href="index.html">← 仓库目录</a></p>
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
</div>
"""
)


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


def _render_analysis_block(a: sqlite3.Row) -> str:
    counts = _as_dict(a["counts_json"])
    quality = _as_dict(a["quality_json"])
    q = " · ".join(_e(k) + "=" + _e(v) for k, v in sorted(quality.items())) or _DASH
    reindex = ' <span class="tag warn">reindex pending</span>' if a["reindex_state"] == "pending" else ""
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


def _page_filename(repo_id: str) -> str | None:
    """把 repo_id 重写成一个安全的产物文件名。

    库里的 repo_id 没有 DDL 约束（TEXT PRIMARY KEY），本函数是最后一道护栏：
    返回 None 表示这个 repo_id 写不进文件名（调用方应跳过并计数），
    返回的字符串保证不含路径分隔符、不是 . / ..、且不超过 255 字节。

    正常仓的 repo_id 是 host__owner__name，原样返回，行为不变。
    """
    name = repo_id
    if not name or name in ('.', '..'):
        return None
    # 只保留文件名里合法的字符；其余换成下划线而不是丢掉（丢掉会把 a/b 与
    # a_b 都压成 ab）。真正的防碰撞在调用方：改写结果与原值不一致的一律跳过，
    # 因此真正被写盘的 id 集合上这个映射是恒等映射，天然不碰撞。
    cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    if cleaned in ('', '.', '..'):
        return None
    # 截断按 UTF-8 字节算（文件系统上限是 255 字节，不是 255 个字符），
    # 且回退到字符边界，别把多字节字符切成乱码。
    if len(cleaned.encode('utf-8')) <= 255:
        return cleaned
    cut = cleaned.encode('utf-8')[:255]
    while cut:
        try:
            return cut.decode('utf-8')
        except UnicodeDecodeError:
            cut = cut[:-1]
    return None


def export_site(paths: Paths | None = None, cfg: Config | None = None, *, out: str | None = None) -> dict[str, Any]:
    """导出静态「仓库目录页」（C11）。

    生成 `out/index.html` 与 `out/<repo_id>.html`，纯静态、内联 CSS、
    无外链资源、无 JS 依赖。返回 {out_dir, repos, files, skipped_repo_ids}；repos 是真正导出的仓数，脏 repo_id 计入 skipped_repo_ids 而不计入 repos。

    `out` 缺省为 `$MEMEX_HOME/site/`（Paths.site）。
    `paths` / `cfg` 可省略（CLI 只传 out 时自动取默认环境配置）。
    """
    # CLI 形态 export_site(out=...) 会省略 paths/cfg；此处补默认值，
    # 与任务给定的三参签名兼容（显式传入时行为不变）。
    del cfg  # 目录页只读真源库，不需要配置快照；保留入参以对齐接口。
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
            "counts_json, quality_json, created_at, finished_at, " + reindex_expr + " FROM analyses "
            "ORDER BY created_at DESC"
        ).fetchall())
        n_cards = int(conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"])
        n_patterns = int(conn.execute("SELECT COUNT(*) AS n FROM patterns").fetchone()["n"])
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
    index_page = _PAGE.substitute(title="memex · 仓库目录", body=index_body, generated=generated)
    index_path = out_dir / "index.html"
    index_path.write_text(index_page, encoding="utf-8")
    files.insert(0, str(index_path))

    return {"out_dir": str(out_dir), "repos": len(repo_rows) - len(skipped), "files": files,
                "skipped_repo_ids": skipped}
