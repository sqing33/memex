# -*- coding: utf-8 -*-
"""C11 改写后新增的渲染测试：单仓详情页的报告正文 + 跨仓模式页。

单独成文件是因为这组测试共用两样大种子：一棵完整的 report_json（报告契约六键齐全）
与 patterns/pattern_members 行。塞进 test_site_export.py 会冲淡那边「文件名安全」的
上下文。

要盯的三件事：
1. 报告正文的六个块（one_liner / characteristics / entry_points / features /
   cross_feature_risks）真的渲染了——这正是用户截图里「只有仓库信息」缺的东西；
2. report_md 以折叠区附在末尾，且报告里的 HTML 标签被转义而不是当成标记注入；
3. patterns.html 独立成页，成员能点回单仓页。
"""
from __future__ import annotations

import json
import sqlite3

# paths fixture 也从那边导入：同一套建库配置，重复定义会让两边的
# 种子逻辑悄悄分叉（之前已经踩过一次 identity_key NOT NULL 的坑）。
from test_site_export import _read, _seed_analysis, _seed_repo, paths  # noqa: F401

from memex.site.render import _score_line, export_site


def _ev(path, start, end, symbol, note):
    return {"path": path, "start_line": start, "end_line": end, "symbol": symbol, "note": note}


REPORT = {
    "schema_id": "memex/report/1",
    "one_liner": "站点渲染用的样例报告 ONELINER。",
    "characteristics": [
        {
            "title": "招牌实现",
            "detail": "特征正文标记 CHARSDETAIL。",
            "evidence": [_ev("src/a.py", 1, 9, "f", "特征证据说明")],
        },
    ],
    "entry_points": [
        {"kind": "main", "path": "src/main.py", "role": "入口角色说明 ENTRYROLE"},
    ],
    "features": [
        {
            "key": "retry-loop",
            "title": "重试循环",
            "summary": "功能摘要 FEATSUMMARY。",
            "intent": "Retry a flaky call with bounded attempts and exponential backoff.",
            "principles": {
                "runtime_control_flow": "运行控制流正文 AXIS1。",
                "data_flow": "数据流正文 AXIS2。",
                "state_lifecycle": "状态生命周期正文 AXIS3。",
                "failure_recovery": "失败与恢复正文 AXIS4。",
                "concurrency_timing": "并发与时序正文 AXIS5。",
            },
            "evidence": [_ev("src/retry.py", 12, 40, "run", "功能证据说明")],
            "cards": [
                {
                    "kind": "mechanism",
                    "title": "上限截断的指数退避",
                    "summary": "卡片摘要 CARDSUMMARY。",
                    "mechanism_desc": "Backoff grows multiplicatively and is clamped by a cap before sleeping.",
                    "reusable": True,
                    "tags": ["backoff", "retry"],
                    "evidence": [_ev("src/retry.py", 20, 28, "compute_backoff", "卡片证据说明")],
                },
                {
                    "kind": "snippet",
                    "title": "退避计算片段",
                    "summary": "第二张卡 CARD2。",
                    "mechanism_desc": "Compute delay as base times factor to the attempt, then clamp.",
                    "reusable": True,
                    "code_spans": [{"path": "src/retry.py", "start_line": 20, "end_line": 22}],
                    "tags": ["backoff"],
                    "evidence": [_ev("src/retry.py", 20, 22, "compute_backoff", "片段证据")],
                },
            ],
        },
    ],
    "cross_feature_risks": [
        {
            "title": "跨功能耦合",
            "detail": "风险正文 RISKDETAIL。",
            "evidence": [_ev("src/a.py", 3, 5, "g", "风险证据说明")],
        },
    ],
}


RAW_MD = "# Markdown 报告\n\n> 引用行 QTEXT。\n\n```go\nfunc f() {}\n```\n"


def _seed_full(paths, repo_id, *, aid, report=None, md=None, **cols):
    _seed_analysis(
        paths,
        repo_id,
        aid=aid,
        report=json.dumps(REPORT if report is None else report, ensure_ascii=False),
        md=RAW_MD if md is None else md,
        **cols,
    )


def test_repo_page_renders_full_report_body(paths, tmp_path):
    """单仓页必须把报告六个块都渲染出来——用户截图里缺的就是这一段。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    for marker in (
        "ONELINER",
        "招牌实现",
        "CHARSDETAIL",
        "src/main.py",
        "ENTRYROLE",
        "重试循环",
        "FEATSUMMARY",
        "AXIS1",
        "AXIS2",
        "AXIS3",
        "AXIS4",
        "AXIS5",
        "意图",
        "Retry a flaky call",
        "上限截断的指数退避",
        "CARDSUMMARY",
        "CARD2",
        "Backoff grows multiplicatively",
        "跨功能耦合",
        "RISKDETAIL",
        "src/retry.py",
        "compute_backoff",
        "卡片证据说明",
    ):
        assert marker in html, "单仓页缺少：%s" % marker


def test_repo_page_shows_five_axes_in_contract_order(paths, tmp_path):
    """五轴标签用中文固定轴名，顺序按契约来。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    labels = ["运行 / 控制流", "数据流", "状态与生命周期", "失败恢复", "并发与时序"]
    pos = [html.index(x) for x in labels]
    assert pos == sorted(pos), "五轴顺序应与契约一致：%r" % labels


def test_repo_page_has_collapsed_raw_markdown_at_end(paths, tmp_path):
    """report_md 附在页面末尾的折叠区，且默认收起。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    assert "<details" in html and "QTEXT" in html, "report_md 应在折叠区里"
    assert "<summary>" in html
    det = html[html.index("<details") : html.index("<details") + 80]
    assert "open" not in det, det
    assert html.index("RISKDETAIL") < html.index("QTEXT"), "折叠区应在报告正文之后"


def test_raw_markdown_is_escaped_not_injected(paths, tmp_path):
    """报告里的标签/脚本要转义，不能真的注入到页面里。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(
        paths,
        "github.com__o__a",
        aid="an-a",
        report={
            "schema_id": "memex/report/1",
            "one_liner": "ONELINER",
            "features": [
                {
                    "key": "k",
                    "title": "<b>T</b>",
                    "summary": "s",
                    "intent": "i",
                    "principles": {},
                    "evidence": [],
                    "cards": [],
                },
            ],
        },
        md="# 标题\n\n<img src=x onerror=alert(1)>\n",
    )
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    assert "<img src=x" not in html, "report_md 里的标签必须转义"
    assert "&lt;img src=x" in html, "应该转义成实体"
    assert "<b>T</b>" not in html, "标题里的标签必须转义"


def test_multiple_analyses_each_render_full_body(paths, tmp_path):
    """一个仓有两次分析，两次都要各自渲染完整正文。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(paths, "github.com__o__a", aid="an-a", md="报告一正文 MDA。")
    _seed_full(
        paths,
        "github.com__o__a",
        aid="an-b",
        commit_sha="1" * 40,
        created_at="2026-02-01T00:00:00Z",
        md="报告二正文 MDB。",
    )
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    assert html.count("RISKDETAIL") == 2, "两次分析各自一份风险块"
    assert "MDA" in html and "MDB" in html, "两次 report_md 都在"


# ---------- 跨仓模式页 ----------


def _seed_pattern(conn_rows, *, pattern_id="pat-1", title="重试收敛到同一循环", card_count=2, repo_count=2, members=()):
    conn_rows.execute(
        "INSERT INTO patterns(pattern_id, key, title, tags_json, card_count, repo_count) "
        "VALUES(?,?,?,?,?,?)",
        (pattern_id, "k-" + pattern_id, title, json.dumps(["retry", "backoff"]), card_count, repo_count),
    )
    for card_id, score in members:
        conn_rows.execute(
            "INSERT INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
            (pattern_id, card_id, score),
        )


def _seed_card_for_pattern(paths, repo_id, analysis_id, feature_id, card_id, *, title, kind="mechanism", summary="摘要"):
    conn = sqlite3.connect(paths.db)
    try:
        conn.execute("PRAGMA foreign_keys=1")
        conn.execute(
            "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) "
            "VALUES(?,?,?,?,?,1)",
            (feature_id, analysis_id, "slug-" + feature_id, "功能 " + feature_id, "功能摘要"),
        )
        conn.execute(
            "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, "
            "language, symbol) VALUES(?,?,?,1,?,?,?,?,?)",
            (
                card_id,
                feature_id,
                kind,
                title,
                summary,
                "English mechanism description for clustering purposes.",
                "go",
                "sym",
            ),
        )
        # 必须显式提交：sqlite3 默认开事务，close() 不提交等于回滚，
        # 于是种子卡片凭空消失，模式页渲染成空页（第一版就是这么翻车的）。
        conn.commit()
    finally:
        conn.close()


def test_patterns_page_lists_patterns_with_members(paths, tmp_path):
    """patterns.html 独立成页，列出模式与成员卡片，成员能点回单仓页。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_repo(paths, "github.com__o__b", full_name="o/b")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    _seed_full(paths, "github.com__o__b", aid="an-b")
    _seed_card_for_pattern(paths, "github.com__o__a", "an-a", "fe-a", "ca-a", title="退避集中在重试引擎")
    _seed_card_for_pattern(paths, "github.com__o__b", "an-b", "fe-b", "ca-b", title="退避集中在调用方")

    conn = sqlite3.connect(paths.db)
    try:
        _seed_pattern(
            conn,
            members=(("ca-a", 0.81), ("ca-b", 0.74)),
        )
        conn.commit()
    finally:
        conn.close()

    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["patterns"] == 1, res
    assert (out / "patterns.html").exists()
    html = _read(out / "patterns.html")
    assert "重试收敛到同一循环" in html
    assert "退避集中在重试引擎" in html and "退避集中在调用方" in html
    assert "o/a" in html and "o/b" in html, "成员要标出各自来源仓"
    assert "0.81" in html and "0.74" in html, "相似度要显示"
    # 成员点回单仓页
    assert 'href="github.com__o__a.html"' in html
    assert 'href="github.com__o__b.html"' in html


def test_patterns_page_empty_state_is_explicit(paths, tmp_path):
    """没有模式时给明确说明，不留空白页。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["patterns"] == 0
    html = _read(out / "patterns.html")
    assert "至少需要两个仓库" in html, html[:400]


def test_pattern_member_from_skipped_repo_falls_back_to_index(paths, tmp_path):
    """成员所在仓被跳过（脏 repo_id）时，链接退到目录页而不是指向不存在的文件。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_repo(paths, "evil/../x", full_name="o/x")
    _seed_full(paths, "github.com__o__a", aid="an-a")
    _seed_full(paths, "evil/../x", aid="an-x")
    _seed_card_for_pattern(paths, "github.com__o__a", "an-a", "fe-a", "ca-a", title="好仓的卡")
    _seed_card_for_pattern(paths, "evil/../x", "an-x", "fe-x", "ca-x", title="脏仓的卡")

    conn = sqlite3.connect(paths.db)
    try:
        _seed_pattern(conn, members=(("ca-a", 0.8), ("ca-x", 0.7)))
        conn.commit()
    finally:
        conn.close()

    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["skipped_repo_ids"] == ["evil/../x"], res
    html = _read(out / "patterns.html")
    assert 'href="github.com__o__a.html"' in html
    assert 'href="evil/../x.html"' not in html, "不能指向被跳过的仓"
    assert 'href="index.html"' in html, "点不到时应退到目录页"


def test_index_links_to_patterns_page(paths, tmp_path):
    """首页的「模式」计数块要能点进 patterns.html——之前只是个死数字。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "index.html")
    assert 'href="patterns.html"' in html, html[:1200]


def test_repo_pages_link_to_patterns_page(paths, tmp_path):
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    html = _read(out / "github.com__o__a.html")
    assert 'href="patterns.html"' in html
    assert 'href="index.html"' in html, "要能回目录页"


class Test相似度渲染:
    """相似度是页面上唯一的数值列，格式不对会直接毁掉可读性。"""

    def test_全精度浮点被压到三位小数(self) -> None:
        # REAL 列直接 str() 会印出 0.6734730638210827 这种全精度
        assert _score_line({"score": 0.6734730638210827, "symbol": "X"}) == "相似度 0.673 · X"

    def test_符号为空时不留悬空间隔点(self) -> None:
        # 模板里写死 `相似度 $score · $symbol` 会在无符号时留下一个光秃秃的「·」
        line = _score_line({"score": 0.6, "symbol": None})
        assert line == "相似度 0.600", line
        assert "·" not in line

    def test_非数值退化成破折号而不是抛异常(self) -> None:
        assert _score_line({"score": None, "symbol": ""}) == "相似度 —"
