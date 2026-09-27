# -*- coding: utf-8 -*-
"""site-data.json 契约的回归测试（decisions.md C11 改写二 / tech-design 2.7）。

C11 改写二把排版那一半从 Python 挪给了 web/ 的 React 预渲染，于是
Python 侧站点链路只剩一个职责：**查库 → 算锚点 → 写一份带 schema_id 的 JSON**。
这份 JSON 就是两端唯一的接口，所以它必须被当成公开契约来测——
否则前端拿到一个少字段、静默变 None 的数据，页面上只会「少一块内容」，
不会报错，正是当初 members_json 被当 dict 读成 {} 那类静默失败。

这里盯五件事：

1. 产物是**一份 JSON**而不是 .html：文件名、schema_id、返回值形状；
2. 报告正文**原样内联**（report_json 不做前端专用整形）——再多造一层
   前端形状就多一个漂移点；
3. 深链锚点 f{fi}-c{ci} 能从 card_id 反解，解不出给 None 而不是假链接；
4. 脏 repo_id 跳过并记账，且**不进** repos 计数（首页数仓数必须等于真写出的页数）；
5. 零假成功：库不存在显式报错，不产出一份空 JSON 骗人。

从 test_site_export.py（已随 site/render.py 一起删除）继承的部分：
paths fixture 的建库配方、_seed_repo / _seed_analysis 的种子、以及
「脏 repo_id 要跳过不能崩」这条教训——那些测试盯的是同一件事在 JSON 上的表现。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config, Paths  # noqa: E402
from memex.site.dump import (  # noqa: E402
    SCHEMA_ID,
    _anchor_for_card,
    _page_filename,
    dump_site_data,
)
from memex.store import db as store_db  # noqa: E402


@pytest.fixture()
def paths(tmp_path) -> Paths:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg.paths


def _seed_repo(paths, repo_id, *, full_name=None, **cols):
    """种一个仓。默认值刻意留空，好让「空值」那条断言真的被测到。"""
    conn = store_db.connect(paths.db)
    try:
        row: dict[str, Any] = {
            "full_name": full_name or repo_id,
            "url": "https://example.invalid/" + repo_id,
            "host": "example.invalid",
            "description": None,
            "language": None,
            "stars": None,
            "license": None,
            "is_stale": 0,
            "is_fork": 0,
            "default_branch": "main",
            "source": "upload",
        }
        row.update(cols)
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, description, language, "
            "stars, license, is_stale, is_fork, default_branch, source, identity_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                repo_id,
                row["full_name"],
                row["url"],
                row["host"],
                row["description"],
                row["language"],
                row["stars"],
                row["license"],
                row["is_stale"],
                row["is_fork"],
                row["default_branch"],
                row["source"],
                "id#" + repo_id,
            ),
        )
    finally:
        conn.close()


def _seed_analysis(paths, repo_id, *, aid=None, **cols):
    conn = store_db.connect(paths.db)
    try:
        row: dict[str, Any] = {
            "commit_sha": "0" * 40,
            "analyst": "agent-x",
            "producer": "agent",
            "status": "committed",
            "created_at": "2026-01-01T00:00:00Z",
            "counts": '{"features": 1, "cards": 2, "evidence": 3}',
            "quality": '{"code_mismatch": 0}',
            "reindex_state": "indexed",
            "report": "{}",
            "md": "",
        }
        row.update(cols)
        conn.execute(
            "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
            "analyst, producer, status, created_at, counts_json, quality_json, "
            "finished_at, reindex_state, report_json, report_md) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                aid or ("an-" + repo_id.replace("/", "")),
                repo_id,
                row["commit_sha"],
                "memex/report/1",
                "standard",
                row["analyst"],
                row["producer"],
                row["status"],
                row["created_at"],
                row["counts"],
                row["quality"],
                row["created_at"],
                row["reindex_state"],
                row["report"],
                row["md"],
            ),
        )
    finally:
        conn.close()


def _dump(paths, tmp_path, **kw) -> dict[str, Any]:
    """导出并读回 JSON——断言都落在盘上那份文件上，而不是返回值上。

    返回值只是 CLI 的回显，真正喂给 React 的是这个文件。
    """
    out = tmp_path / "site"
    dump_site_data(paths, out=str(out), **kw)
    return json.loads((out / "site-data.json").read_text(encoding="utf-8"))


# ---------- 产物形状 ----------


def test_dump_writes_one_json_with_schema_id(paths, tmp_path):
    """产物是一份带 schema_id 的 JSON，不是 .html——两端唯一的接口。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    res = dump_site_data(paths, out=str(out))

    assert res["schema_id"] == SCHEMA_ID == "memex/site/1"
    assert res["repos"] == 1
    assert not list(out.glob("*.html")), "排版归 React，Python 侧不该再写 HTML"
    data = json.loads(Path(res["data_file"]).read_text(encoding="utf-8"))
    assert data["schema_id"] == SCHEMA_ID
    assert data["stats"]["repos"] == 1
    assert data["repos"][0]["file"] == "github.com__o__a.html", "文件名要提前定好给前端用"


def test_dump_is_utf8_without_escapes(paths, tmp_path):
    """中文必须原样落盘。ensure_ascii=True 会把中文变成 \\uXXXX，
    前端拿到的仍是乱码，页面上「没内容」还查不出原因。"""
    _seed_repo(paths, "github.com__o__a", full_name="仓甲")
    out = tmp_path / "site"
    dump_site_data(paths, out=str(out))
    raw = (out / "site-data.json").read_text(encoding="utf-8")
    assert "仓甲" in raw
    assert "\\u" not in raw


def test_dump_reports_embedder_and_threshold(paths, tmp_path):
    """embedder 与聚类阈值写进 JSON，是为了让页面显示的数值有据可查，
    而不是让前端编一个（decisions.md C11 改写二）。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    data = _dump(paths, tmp_path)
    assert "embedder" in data, "索引模型要如实带出（G11：meta.embedder 是全库唯一的）"
    assert data["cluster_threshold"] == 0.60, "与 patterns/cluster.py DEFAULT_SIM_THRESHOLD 同值"


# ---------- 报告原样内联 ----------


def _full_report() -> dict[str, Any]:
    ev = [{"path": "src/a.py", "start_line": 1, "end_line": 9, "symbol": "f", "note": "说明"}]
    return {
        "schema_id": "memex/report/1",
        "one_liner": "ONELINER 标记。",
        "characteristics": [{"title": "招牌实现", "detail": "CHARSDETAIL。", "evidence": ev}],
        "entry_points": [{"kind": "main", "path": "src/main.py", "role": "ENTRYROLE"}],
        "features": [
            {
                "key": "retry-loop",
                "title": "重试循环",
                "summary": "FEATSUMMARY。",
                "intent": "Retry a flaky call with bounded attempts.",
                "principles": {
                    "runtime_control_flow": "AXIS1。",
                    "data_flow": "AXIS2。",
                    "state_lifecycle": "AXIS3。",
                    "failure_recovery": "AXIS4。",
                    "concurrency_timing": "AXIS5。",
                },
                "evidence": ev,
                "cards": [
                    {
                        "kind": "mechanism",
                        "title": "上限截断的指数退避",
                        "summary": "CARDSUMMARY。",
                        "mechanism_desc": "Backoff grows multiplicatively and is clamped.",
                        "reusable": True,
                        "tags": ["backoff"],
                        "evidence": ev,
                    }
                ],
            }
        ],
        "cross_feature_risks": [{"title": "跨功能耦合", "detail": "RISKDETAIL。", "evidence": ev}],
    }


def test_report_json_is_inlined_verbatim(paths, tmp_path):
    """report_json 原样内联，不做前端专用整形（tech-design 2.7.1）。

    这条最容易被「顺手优化」破坏：一旦在 dump 侧改名/裁字段，
    前端 types.ts 就与库里的真实形状分家，而两边都不报错。
    """
    report = _full_report()
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_analysis(
        paths,
        "github.com__o__a",
        aid="an-a",
        report=json.dumps(report, ensure_ascii=False),
        md="RAWMD 标记。",
    )
    data = _dump(paths, tmp_path)

    got = data["repos"][0]["analyses"][0]["report"]
    assert got == report, "report 必须与库里的 report_json 完全一致"
    assert data["repos"][0]["analyses"][0]["report_md"] == "RAWMD 标记。"
    # 六个块都在，前端才有东西可渲染
    for key in (
        "one_liner",
        "characteristics",
        "entry_points",
        "features",
        "cross_feature_risks",
    ):
        assert key in got, "报告少了块：" + key


def test_counts_and_quality_are_decoded_objects(paths, tmp_path):
    """counts_json / quality_json 是 JSON 字符串，必须解成对象。

    不解的话前端拿到的是字符串，页面上会显示成 [object Object]——
    同样是「有数据但页面不对」，查起来很难受。
    """
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_analysis(
        paths,
        "github.com__o__a",
        aid="an-a",
        counts='{"features": 2, "cards": 5, "evidence": 3}',
        quality='{"code_mismatch": 0}',
    )
    ana = _dump(paths, tmp_path)["repos"][0]["analyses"][0]
    assert ana["counts"] == {"features": 2, "cards": 5, "evidence": 3}
    assert ana["quality"] == {"code_mismatch": 0}
    assert ana["analyst"] == "agent-x", "分析者要如实带出（analyses 没有模型列）"


def test_repo_without_analysis_is_explicit(paths, tmp_path):
    """没分析过的仓 latest 为 None，交给前端显示「尚未分析」，
    不是把 0 当成「分析了但没产出」。"""
    _seed_repo(paths, "github.com__o__fresh", full_name="o/fresh")
    data = _dump(paths, tmp_path)
    repo = data["repos"][0]
    assert repo["analysis_count"] == 0
    assert repo["latest"] is None
    assert repo["analyses"] == []


def test_every_analysis_is_kept_newest_first(paths, tmp_path):
    """一个仓的历次分析都要在，按时间倒序；幂等键不同才不会撞 UNIQUE。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_analysis(paths, "github.com__o__a", aid="an-old",
                   commit_sha="a" * 40, created_at="2026-01-01T00:00:00Z")
    _seed_analysis(paths, "github.com__o__a", aid="an-new",
                   commit_sha="b" * 40, created_at="2026-02-01T00:00:00Z")
    repo = _dump(paths, tmp_path)["repos"][0]
    assert repo["analysis_count"] == 2
    assert [a["analysis_id"] for a in repo["analyses"]] == ["an-new", "an-old"]


# ---------- 锚点：深链的地基 ----------


class Test锚点反解:
    """模式页要能点回「这张卡在那个仓的第几屏」。"""

    def test_正常卡片能反解出锚点(self) -> None:
        # analyze/rows.py 的 id 形如 card_feat_{analysis_id}_{fi}_{ci}
        assert _anchor_for_card("card_feat_ana_abc_0_2") == "f0-c2"
        assert _anchor_for_card("card_feat_ana_abc_3_0") == "f3-c0"

    def test_解不出时给None而不是编一个链接(self) -> None:
        # 宁可不给深链，也不要给一个点了没反应的假链接
        assert _anchor_for_card("bad") is None
        assert _anchor_for_card("card_feat_ana_abc_x_y") is None
        assert _anchor_for_card("card_x_1") is None


def test_pattern_members_carry_file_and_anchor(paths, tmp_path):
    """模式成员要带来源仓的页面文件名与卡片锚点，深链才成立。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_repo(paths, "github.com__o__b", full_name="o/b")
    _seed_analysis(paths, "github.com__o__a", aid="an-a")
    _seed_analysis(paths, "github.com__o__b", aid="an-b")
    _seed_card(paths, "an-a", "fe-a", "card_feat_an-a_0_1", "好仓的卡")
    _seed_card(paths, "an-b", "fe-b", "card_feat_an-b_1_0", "另一仓的卡")

    conn = sqlite3.connect(paths.db)
    try:
        conn.execute(
            "INSERT INTO patterns(pattern_id, key, title, tags_json, card_count, repo_count) "
            "VALUES(?,?,?,?,?,?)",
            ("pat-1", "k-1", "重试收敛到同一循环", json.dumps(["retry"]), 2, 2),
        )
        conn.execute(
            "INSERT INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
            ("pat-1", "card_feat_an-a_0_1", 0.81),
        )
        conn.execute(
            "INSERT INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
            ("pat-1", "card_feat_an-b_1_0", 0.74),
        )
        conn.commit()
    finally:
        conn.close()

    pats = _dump(paths, tmp_path)["patterns"]
    assert len(pats) == 1
    members = pats[0]["members"]
    assert len(members) == 2, "成员卡片不能被静默丢掉（members_json 曾被当 dict 读成 {}）"
    # 按 score 降序
    assert [m["score"] for m in members] == [0.81, 0.74]
    by_card = {m["card_id"]: m for m in members}
    a = by_card["card_feat_an-a_0_1"]
    assert a["file"] == "github.com__o__a.html"
    assert a["anchor"] == "f0-c1"
    assert a["repo_full_name"] == "o/a"
    assert by_card["card_feat_an-b_1_0"]["anchor"] == "f1-c0"


def test_pattern_member_from_skipped_repo_has_no_link(paths, tmp_path):
    """成员所在仓被跳过（脏 repo_id）时 file 为 None，不给指向不存在页面的链接。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    _seed_repo(paths, "evil/../x", full_name="o/x")
    _seed_analysis(paths, "github.com__o__a", aid="an-a")
    _seed_analysis(paths, "evil/../x", aid="an-x")
    _seed_card(paths, "an-a", "fe-a", "card_feat_an-a_0_0", "好仓的卡")
    _seed_card(paths, "an-x", "fe-x", "card_feat_an-x_0_0", "脏仓的卡")

    conn = sqlite3.connect(paths.db)
    try:
        conn.execute(
            "INSERT INTO patterns(pattern_id, key, title, tags_json, card_count, repo_count) "
            "VALUES(?,?,?,?,?,?)",
            ("pat-1", "k-1", "标题", json.dumps([]), 2, 2),
        )
        conn.execute("INSERT INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
                     ("pat-1", "card_feat_an-a_0_0", 0.8))
        conn.execute("INSERT INTO pattern_members(pattern_id, card_id, score) VALUES(?,?,?)",
                     ("pat-1", "card_feat_an-x_0_0", 0.7))
        conn.commit()
    finally:
        conn.close()

    data = _dump(paths, tmp_path)
    assert data["skipped_repo_ids"] == ["evil/../x"]
    by_card = {m["card_id"]: m for m in data["patterns"][0]["members"]}
    assert by_card["card_feat_an-a_0_0"]["file"] == "github.com__o__a.html"
    assert by_card["card_feat_an-x_0_0"]["file"] is None, "被跳过的仓不能给链接"


def test_patterns_empty_is_empty_list_not_error(paths, tmp_path):
    """没有模式时是空列表，前端据此显示「至少需要两个仓库」。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    data = _dump(paths, tmp_path)
    assert data["patterns"] == []
    assert data["stats"]["patterns"] == 0


def _seed_card(paths, analysis_id, feature_id, card_id, title, *, kind="mechanism"):
    """种一张卡。必须显式 commit：sqlite3 默认开事务，close() 不提交等于回滚，
    种子卡片会凭空消失（第一版 patterns 测试就是这么翻车的）。"""
    conn = sqlite3.connect(paths.db)
    try:
        conn.execute("PRAGMA foreign_keys=1")
        conn.execute(
            "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) "
            "VALUES(?,?,?,?,?,1)",
            (feature_id, analysis_id, "slug-" + feature_id, "功能", "功能摘要"),
        )
        conn.execute(
            "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, "
            "mechanism_desc, language, symbol) VALUES(?,?,?,1,?,?,?,?,?)",
            (card_id, feature_id, kind, title, "摘要", "English mechanism desc.", "go", "sym"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- 脏 repo_id：产物文件名自守 ----------


class Test产物文件名自守:
    """_page_filename 原样搬自被删掉的 render.py，行为与理由不变。

    repo_id 在 DDL 里只是 TEXT PRIMARY KEY，没有字符集约束，所以这是最后一道护栏：
    改写结果与原值不一致的**一律跳过**，真正写盘的那批上这个映射是恒等的，
    天然不碰撞（a/b 与 a_b 都会压成 a_b，但 a/b 因为需要改写而被跳过）。
    """

    def test_正常仓原样通过(self) -> None:
        rid = "github.com__sqing33__PTNexus"
        assert _page_filename(rid) == rid

    def test_危险值被改写或拒绝(self) -> None:
        assert _page_filename("evil/../../x") != "evil/../../x"
        assert "/" not in (_page_filename("evil/../../x") or "")
        assert _page_filename("") is None
        assert _page_filename(".") is None
        assert _page_filename("..") is None

    def test_超长名按UTF8字节截断(self) -> None:
        got = _page_filename("a" * 400)
        assert got is not None and len(got.encode("utf-8")) <= 255
        cjk = _page_filename("仓" * 200)
        assert cjk is not None and len(cjk.encode("utf-8")) <= 255
        assert (cjk or "").encode("utf-8").decode("utf-8") == cjk, "不能把多字节切成乱码"

    def test_重写本身不保证唯一_靠跳过防碰撞(self) -> None:
        # 记住这条：防碰撞靠调用方跳过，不靠重写函数本身
        assert _page_filename("a/b") == _page_filename("a_b") == "a_b"


def test_dirty_repo_id_is_skipped_and_not_counted(paths, tmp_path):
    """脏 repo_id 跳过并记账，且不进 repos 计数——
    首页显示的仓库数必须等于真能写出的页数（tech-design 2.7.4）。"""
    _seed_repo(paths, "github.com__o__good", full_name="o/good")
    conn = store_db.connect(paths.db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
            "VALUES(?,?,?,?,?,?)",
            ("evil/../../x", "evil", "https://example.invalid/x",
             "example.invalid", "id#evil", "upload"),
        )
    finally:
        conn.close()

    data = _dump(paths, tmp_path)
    assert data["skipped_repo_ids"] == ["evil/../../x"], "脏 repo_id 要被记账"
    assert data["stats"]["repos"] == 1, "只数真正导出的仓"
    assert [r["repo_id"] for r in data["repos"]] == ["github.com__o__good"]


def test_collision_pair_keeps_only_the_legal_id(paths, tmp_path):
    """a/b 需要改写被跳过，a_b 合法原样保留——落盘那批上恒等，无同名。"""
    _seed_repo(paths, "a/b", full_name="o/ab")
    _seed_repo(paths, "a_b", full_name="o/a_b")
    _seed_repo(paths, "github.com__o__good", full_name="o/good")
    data = _dump(paths, tmp_path)
    assert data["skipped_repo_ids"] == ["a/b"]
    assert data["stats"]["repos"] == 2
    files = [r["file"] for r in data["repos"]]
    assert len(files) == len(set(files)), "落盘的文件名必须唯一"
    assert "a_b.html" in files


# ---------- 零假成功 ----------


def test_dump_errors_when_db_missing(paths, tmp_path):
    """库不存在要显式报错，不是一份空 JSON 骗人说「站点是新的」。"""
    from memex.core import MemexError

    paths.db.unlink()
    with pytest.raises(MemexError) as ei:
        dump_site_data(paths, out=str(tmp_path / "site"))
    assert ei.value.code == "not_found"
    assert not (tmp_path / "site" / "site-data.json").exists()


def test_empty_db_dumps_honestly(paths, tmp_path):
    """库在但没数据：正常导出，内容为空。不报错，也不假装有仓。"""
    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    data = _dump(paths, tmp_path)
    assert data["stats"] == {"repos": 1, "analyses": 0, "cards": 0, "patterns": 0}
