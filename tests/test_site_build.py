# -*- coding: utf-8 -*-
"""React 预渲染产物的回归测试（decisions.md C11 改写二 / tech-design 2.7）。

这组测试盯的是**产物 HTML**，不是 JSON。JSON 那半在 test_site_dump.py。
从旧的 test_site_content.py 继承下来的判断，一个都不该在改渲染器时丢掉：

1. 报告六个块真的出现在单仓页——用户截图里「只有仓库信息」缺的就是这一段；
2. 报告里的标签/脚本被转义，没有被当成标记注入；
3. 五轴顺序与契约一致；
4. report_md 以折叠区附在末尾，默认收起；
5. 模式页独立成页，成员能点回单仓页；
6. 产物自足：内联 CSS、无外链、无 JS 依赖（file:// 能直接开）。

**为什么跑真构建而不是 mock**：这些断言全部是「HTML 里有没有这段字」。
mock 掉 React 只能证明调用发生过，证明不了页面真的长这样——当初
members_json 被当 dict 读成 {} 那种 bug，mock 一个都不会发现。
所以这里真调 npm run build + prerender.mjs。

Node 不可用时**显式 skip**（不是静默通过）：没有 Node 就等于没装前端工具链，
这在该跑的机器上会被 CI 看见。构建一次约 2-5s，用 session 级 fixture 复用。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"


def _ev(path: str, start: int, end: int, symbol: str, note: str) -> dict[str, Any]:
    return {"path": path, "start_line": start, "end_line": end, "symbol": symbol, "note": note}


RAW_MD = "# Markdown 报告\n\n> 引用行 QTEXT。\n\n```go\nfunc f() {}\n```\n"


def _report() -> dict[str, Any]:
    """一棵六键齐全的 report_json，标记串与断言一一对应。"""
    ev = _ev("src/a.py", 1, 9, "f", "特征证据说明")
    return {
        "schema_id": "memex/report/1",
        "one_liner": "ONELINER 标记。",
        "characteristics": [
            {"title": "招牌实现", "detail": "CHARSDETAIL。", "evidence": [ev]},
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
                        "mechanism_desc": "Backoff grows multiplicatively and is clamped by a cap.",
                        "reusable": True,
                        "tags": ["backoff", "retry"],
                        "evidence": [
                            _ev("src/retry.py", 20, 28, "compute_backoff", "卡片证据说明")
                        ],
                    },
                    {
                        "kind": "snippet",
                        "title": "退避计算片段",
                        "summary": "第二张卡 CARD2。",
                        "mechanism_desc": "Compute delay as base times factor, then clamp.",
                        "reusable": True,
                        "code_spans": [
                            {"path": "src/retry.py", "start_line": 20, "end_line": 22}
                        ],
                        "tags": ["backoff"],
                        "evidence": [
                            _ev("src/retry.py", 20, 22, "compute_backoff", "片段证据")
                        ],
                    },
                ],
            },
        ],
        "cross_feature_risks": [
            {"title": "跨功能耦合", "detail": "风险正文 RISKDETAIL。", "evidence": [
                _ev("src/a.py", 3, 5, "g", "风险证据说明")
            ]},
        ],
    }


def _analysis(aid: str, *, report=None, md: str | None = None, sha: str = "0" * 40) -> dict[str, Any]:
    return {
        "analysis_id": aid,
        "commit_sha": sha,
        "contract_version": "memex/report/1",
        "depth": "standard",
        "analyst": "dsh-mcp-client",
        "producer": "agent",
        "status": "committed",
        "created_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:10:00Z",
        "reindex_state": "indexed",
        "counts": {"features": 1, "cards": 2, "evidence": 3},
        "quality": {"code_mismatch": 0, "axis_completeness": 1.0},
        "report": _report() if report is None else report,
        "report_md": RAW_MD if md is None else md,
    }


def _repo(rid: str, full: str, analyses: list[dict[str, Any]], **cols) -> dict[str, Any]:
    repo: dict[str, Any] = {
        "repo_id": rid,
        "file": rid + ".html",
        "full_name": full,
        "url": "https://example.invalid/" + full,
        "host": "example.invalid",
        "description": None,
        "language": "python",
        "stars": None,
        "license": None,
        "is_stale": False,
        "is_fork": False,
        "fork_of": None,
        "default_branch": "main",
        "source": "clone",
        "analysis_count": len(analyses),
        "latest": analyses[0] if analyses else None,
        "analyses": analyses,
    }
    repo.update(cols)
    return repo


def _site_data(repos, patterns=None) -> dict[str, Any]:
    n_cards = 0
    n_analyses = 0
    for r in repos:
        n_analyses += len(r["analyses"])
        for a in r["analyses"]:
            feats = a["report"].get("features") or []
            n_cards += sum(len(f.get("cards") or []) for f in feats)
    pats = patterns or []
    return {
        "schema_id": "memex/site/1",
        "generated_at": "2026-01-01T00:00:00Z",
        "embedder": "hash:64",
        "cluster_threshold": 0.60,
        "stats": {
            "repos": len(repos),
            "analyses": n_analyses,
            "cards": n_cards,
            "patterns": len(pats),
        },
        "repos": repos,
        "patterns": pats,
        "skipped_repo_ids": [],
    }


@pytest.fixture(scope="session")
def built() -> None:
    """只跑 vite 编译那一步（build:renderer），产物由各测试自己喂数据渲染。

    拆两步是有意的：npm run build 把「编译」和「预渲染」捆在一起，而预渲染
    必须先有 site-data.json。测试要的是渲染器本身，不是某一份数据。

    Node 缺失就显式 skip 而不是静默 pass：没装 Node 时这组断言等于没跑，
    藏在 pass 里就是骗人（和当初 members_json 静默变 {} 一个性质的问题）。
    """
    if not (WEB / "package.json").exists():
        pytest.skip("源码树里没有 web/，跳过前端产物测试")
    if subprocess.run(["node", "--version"], capture_output=True).returncode != 0:
        pytest.skip("没装 node，无法构建前端产物")
    if not (WEB / "node_modules").exists():
        pytest.skip("web/node_modules 缺失，先在 web/ 跑 npm install")
    proc = subprocess.run(
        ["npm", "run", "build:renderer", "--silent"],
        cwd=str(WEB), capture_output=True, text=True, timeout=600, check=False,
    )
    if proc.returncode != 0:
        pytest.fail("vite build 失败：\n" + (proc.stderr or proc.stdout)[-3000:])


def _render(tmp_path: Path, data: dict[str, Any]) -> Path:
    """用真 prerender.mjs 把数据渲染成 HTML，返回产物目录。"""
    out = tmp_path / "dist"
    out.mkdir(parents=True, exist_ok=True)
    data_file = tmp_path / "site-data.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "MEMEX_SITE_DATA": str(data_file), "MEMEX_SITE_OUT": str(out)}
    proc = subprocess.run(
        ["node", str(WEB / "build" / "prerender.mjs")],
        cwd=str(WEB), capture_output=True, text=True, timeout=300, check=False, env=env,
    )
    if proc.returncode != 0:
        pytest.fail("预渲染失败：\n" + (proc.stderr or proc.stdout)[-3000:])
    return out


# ---------- 单仓页：报告正文 ----------


def test_repo_page_renders_full_report_body(built, tmp_path):
    """六个块都要渲染出来——用户截图里缺的就是这一段。"""
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    for marker in (
        "ONELINER", "招牌实现", "CHARSDETAIL", "src/main.py", "ENTRYROLE",
        "重试循环", "FEATSUMMARY", "AXIS1", "AXIS2", "AXIS3", "AXIS4", "AXIS5",
        "意图", "Retry a flaky call",
        "上限截断的指数退避", "CARDSUMMARY", "CARD2", "Backoff grows multiplicatively",
        "跨功能耦合", "RISKDETAIL", "src/retry.py", "compute_backoff", "卡片证据说明",
    ):
        assert marker in html, "单仓页缺少：%s" % marker


def test_repo_page_shows_five_axes_in_contract_order(built, tmp_path):
    """五轴中文标签，顺序以 src/memex/constants.py 为准。"""
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    labels = ["运行 / 控制流", "数据流", "状态与生命周期", "失败恢复", "并发与时序"]
    pos = [html.index(x) for x in labels]
    assert pos == sorted(pos), "五轴顺序应与契约一致：%r" % labels


def test_cards_get_anchors_one_per_card(built, tmp_path):
    """每张卡片一个 f{fi}-c{ci} 锚点——模式页深链就指到这里。"""
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert 'id="f0-c0"' in html
    assert 'id="f0-c1"' in html
    assert len(re.findall(r'id="f\d+-c\d+"', html)) == 2, "两张卡就该两个锚点"


def test_analyst_shown_not_model(built, tmp_path):
    """展示「分析者」（analyst），不展示「模型」——analyses 表根本没有模型列。

    decisions.md 已定：meta.embedder 是全库唯一索引模型，不是每次分析各记一个。
    页面凭空造一个「模型」字段会让人去找一个不存在的字段。
    """
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "分析者" in html
    assert "dsh-mcp-client" in html
    assert "模型" not in html, "页面出现了 analyses 里根本没有的「模型」字段"


def test_repo_without_analysis_says_so(built, tmp_path):
    """没分析过的仓要显示「尚未分析」，不是显示成 0 让人以为分析过但没产出。"""
    data = _site_data([_repo("github.com__o__fresh", "o/fresh", [])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__fresh.html").read_text(encoding="utf-8")
    assert "尚未分析" in html


def test_multiple_analyses_each_render_full_body(built, tmp_path):
    """一个仓两次分析，两次都要各自渲染完整正文。"""
    a1 = _analysis("an-a", md="报告一正文 MDA。")
    a2 = _analysis("an-b", md="报告二正文 MDB。", sha="1" * 40)
    data = _site_data([_repo("github.com__o__a", "o/a", [a2, a1])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert html.count("RISKDETAIL") == 2, "两次分析各自一份风险块"
    assert "MDA" in html and "MDB" in html, "两次 report_md 都在"


# ---------- 转义：报告是外部输入 ----------


def test_report_html_is_escaped_not_injected(built, tmp_path):
    """报告里的标签/脚本要转义，不能真的注入到页面里。"""
    evil = {
        "schema_id": "memex/report/1",
        "one_liner": "ONELINER",
        "features": [
            {
                "key": "k", "title": "<b>T</b>", "summary": "s", "intent": "i",
                "principles": {}, "evidence": [], "cards": [],
            }
        ],
    }
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a", report=evil)])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "<b>T</b>" not in html, "标题里的标签必须转义"
    assert "&lt;b&gt;" in html


def test_raw_markdown_is_escaped_not_injected(built, tmp_path):
    """report_md 只当快照展示，里面的标签同样要转义。"""
    md = "# 标题\n\n<img src=x onerror=alert(1)>\n"
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a", md=md)])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "<img src=x" not in html, "report_md 里的标签必须转义"
    assert "&lt;img src=x" in html, "应该转义成实体"


def test_repo_description_is_escaped(built, tmp_path):
    """仓库描述也是外部输入，同样要转义。"""
    desc = '<script>alert("x")</script> & more'
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")], description=desc)])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_empty_values_render_as_dash_not_banned_placeholder(built, tmp_path):
    """空值渲染成破折号，不用被禁的占位符（N/A / 无 / 未知 / 待补充 / TODO）。"""
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "—" in html, "空值应渲染成破折号"
    for banned in ("N/A", "未知", "待补充", "TODO"):
        assert banned not in html, "产物里出现了被禁占位符：" + banned


# ---------- report_md 折叠区 ----------


def test_raw_markdown_is_collapsed_at_end(built, tmp_path):
    """report_md 附在页面末尾的折叠区，且默认收起。"""
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")

    assert "<details" in html and "QTEXT" in html, "report_md 应在折叠区里"
    assert "<summary>" in html
    det = html[html.index("<details"): html.index("<details") + 80]
    assert "open" not in det, det
    assert html.index("RISKDETAIL") < html.index("QTEXT"), "折叠区应在报告正文之后"


# ---------- 模式页 ----------


def _pattern(members: list[dict[str, Any]], *, title="重试收敛到同一循环") -> dict[str, Any]:
    return {
        "pattern_id": "pat-1",
        "key": "k-1",
        "title": title,
        "tags": ["retry", "backoff"],
        "card_count": len(members),
        "repo_count": 2,
        "members": members,
    }


def test_patterns_page_lists_patterns_with_members(built, tmp_path):
    """patterns.html 独立成页，列出模式与成员，成员能点回单仓页的卡片锚点。"""
    repos = [
        _repo("github.com__o__a", "o/a", [_analysis("an-a")]),
        _repo("github.com__o__b", "o/b", [_analysis("an-b")]),
    ]
    members = [
        {"card_id": "c-a", "score": 0.81, "kind": "mechanism", "title": "退避集中在重试引擎",
         "summary": "摘要 A", "symbol": "sym", "repo_id": "github.com__o__a",
         "repo_full_name": "o/a", "file": "github.com__o__a.html", "anchor": "f0-c0"},
        {"card_id": "c-b", "score": 0.74, "kind": "mechanism", "title": "退避集中在调用方",
         "summary": "摘要 B", "symbol": "sym", "repo_id": "github.com__o__b",
         "repo_full_name": "o/b", "file": "github.com__o__b.html", "anchor": "f0-c0"},
    ]
    out = _render(tmp_path, _site_data(repos, [_pattern(members)]))

    assert (out / "patterns.html").exists()
    html = (out / "patterns.html").read_text(encoding="utf-8")
    assert "重试收敛到同一循环" in html
    assert "退避集中在重试引擎" in html and "退避集中在调用方" in html
    assert "o/a" in html and "o/b" in html
    assert "0.810" in html and "0.740" in html, "相似度固定三位小数，全精度浮点会毁掉可读性"
    # 深链：文件 + 锚点
    assert 'href="github.com__o__a.html#f0-c0"' in html
    assert 'href="github.com__o__b.html#f0-c0"' in html


def test_patterns_page_empty_state_is_explicit(built, tmp_path):
    """没有模式时给明确说明，不留一张空白的页。"""
    out = _render(tmp_path, _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])]))
    html = (out / "patterns.html").read_text(encoding="utf-8")
    assert "至少需要两个仓库" in html, html[:400]


def test_pattern_member_without_link_is_explained(built, tmp_path):
    """成员所在仓被跳过时不给链接，但要说明为什么没有链接，而不是静默少一块。"""
    members = [
        {"card_id": "c-x", "score": 0.7, "kind": "mechanism", "title": "脏仓的卡",
         "summary": "摘要", "symbol": "sym", "repo_id": "evil/../x",
         "repo_full_name": "o/x", "file": None, "anchor": "f0-c0"},
    ]
    out = _render(tmp_path, _site_data(
        [_repo("github.com__o__a", "o/a", [_analysis("an-a")])], [_pattern(members)]
    ))
    html = (out / "patterns.html").read_text(encoding="utf-8")
    assert "evil/../x.html" not in html, "不能指向被跳过的仓"
    assert "没被导出" in html, "不给链接时要说明原因"


# ---------- 目录页与站内链接 ----------


def test_index_links_to_patterns_page(built, tmp_path):
    """首页的模式计数要能点进 patterns.html，不能只是个死数字。"""
    out = _render(tmp_path, _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])]))
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="patterns.html"' in html


def test_exactly_one_footer_per_page(built, tmp_path):
    """每页只能有一个页脚。

    外壳 Layout 已经渲染页脚，IndexPage 又自己写了一个，于是首页出现两条
    「由 memex export-site 生成」，还把 embedder 说了两遍——同一条元数据
    两处渲染，迟早两处会漂移。页脚归外壳管，页面组件不重复画。
    """
    data = _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])])
    out = _render(tmp_path, data)
    for name in ("index.html", "patterns.html", "github.com__o__a.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert html.count("<footer") == 1, "%s 有多个页脚" % name
        assert html.count("由 memex export-site 生成") == 1, "%s 重复渲染了生成信息" % name


def test_repo_pages_link_back(built, tmp_path):
    out = _render(tmp_path, _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])]))
    html = (out / "github.com__o__a.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in html, "要能回目录页"
    assert 'href="patterns.html"' in html


def test_no_dead_links(built, tmp_path):
    """全站相对链接不许有死链——模式页深链最容易悄悄失效。"""
    repos = [
        _repo("github.com__o__a", "o/a", [_analysis("an-a")]),
        _repo("github.com__o__b", "o/b", [_analysis("an-b")]),
    ]
    members = [
        {"card_id": "c-a", "score": 0.81, "kind": "mechanism", "title": "卡 A",
         "summary": "摘要", "symbol": "sym", "repo_id": "github.com__o__a",
         "repo_full_name": "o/a", "file": "github.com__o__a.html", "anchor": "f0-c0"},
        {"card_id": "c-b", "score": 0.74, "kind": "mechanism", "title": "卡 B",
         "summary": "摘要", "symbol": "sym", "repo_id": "github.com__o__b",
         "repo_full_name": "o/b", "file": "github.com__o__b.html", "anchor": "f0-c1"},
    ]
    out = _render(tmp_path, _site_data(repos, [_pattern(members)]))

    dead: list[str] = []
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target, _, frag = href.partition("#")
            if not target:
                continue
            tp = out / target
            if not tp.exists():
                dead.append("%s -> %s（文件不存在）" % (page.name, href))
            elif frag and ('id="%s"' % frag) not in tp.read_text(encoding="utf-8"):
                dead.append("%s -> %s（锚点不存在）" % (page.name, href))
    assert not dead, "站内死链：" + NL + NL.join(dead)


# ---------- 产物自足（tech-design 2.7.2） ----------


def test_output_is_self_contained(built, tmp_path):
    """C11 的承诺：内联 CSS、无外链资源、无 JS 依赖，file:// 直接能开。"""
    out = _render(tmp_path, _site_data([_repo("github.com__o__a", "o/a", [_analysis("an-a")])]))
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        assert "<style>" in html, page.name + " 缺内联样式"
        assert "<script" not in html.lower(), page.name + " 不该有 JS"
        assert 'rel="stylesheet"' not in html, page.name + " 引用了外链样式"
        assert "http://" not in html, page.name + " 引用了明文外链"


# ---------- 零假成功 ----------


def test_prerender_rejects_wrong_schema(tmp_path):
    """schema 对不上就停：宁可不产页面，也不要出一张静默错页。"""
    if subprocess.run(["node", "--version"], capture_output=True).returncode != 0:
        pytest.skip("没装 node")
    if not (WEB / "node_modules").exists():
        pytest.skip("web/node_modules 缺失")
    data = _site_data([_repo("github.com__o__a", "o/a", [])])
    data["schema_id"] = "memex/site/999"
    data_file = tmp_path / "bad.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "dist"
    env = {**os.environ, "MEMEX_SITE_DATA": str(data_file), "MEMEX_SITE_OUT": str(out)}
    proc = subprocess.run(
        ["node", str(WEB / "build" / "prerender.mjs")],
        cwd=str(WEB), capture_output=True, text=True, timeout=300, check=False, env=env,
    )
    assert proc.returncode != 0, "schema 不匹配必须失败"
    assert "memex/site/999" in proc.stderr, proc.stderr
    assert not list(out.glob("*.html")) if out.exists() else True, "不该产出页面"


def test_prerender_needs_data_and_out(built):
    """两个路径缺一个都要显式报错，不要默默写出到当前目录。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("MEMEX_SITE_DATA", "MEMEX_SITE_OUT")}
    proc = subprocess.run(
        ["node", str(WEB / "build" / "prerender.mjs")],
        cwd=str(WEB), capture_output=True, text=True, timeout=120, check=False, env=env,
    )
    assert proc.returncode != 0
    assert "MEMEX_SITE_DATA" in proc.stderr
