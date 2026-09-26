"""reindex 的覆盖范围：agent 路径的 committed 与回填路径的 ready 都得重建。

背景（BUG-1）：reindex 先清空 chunks / chunk_vectors / chunk_fts 三张派生表，
再按 analyses.status 逐条重建。原先只筛 committed，而 VibeCraft 回填写的是 ready
——回填的索引会被物理删除且永不重建，而 import-vibecraft 的 next_step 恰恰
让用户去跑 reindex，等于越修越空。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config, MemexError  # noqa: E402
from memex.store import db as store_db  # noqa: E402

STATUS_COMMITTED = "committed"
STATUS_READY = "ready"

MECH = "Retry with bounded backoff budget across remote calls."


def _setup(tmp_path: Path) -> Config:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg


def _seed(conn, analysis_id: str, status: str) -> None:
    """插入一条分析 + 一个功能 + 一张卡 + 一块 chunk（模拟已落库状态）。

    每条分析落在**自己的仓**上：analyses 有 UNIQUE(repo_id, commit_sha, contract_version)，
    且 features 走 INSERT OR REPLACE 会经 repo_id 外键的 ON DELETE CASCADE
    把同仓的上一条分析连带删掉——共仓会让第二条把第一条挤掉（踩过一次）。
    """
    repo_id = "gh__o__" + analysis_id
    feat_id = "f1-" + analysis_id
    card_id = "c1-" + analysis_id
    conn.execute(
        "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
        "VALUES(?,?,?,?,?,?)",
        (
            repo_id,
            "o/" + analysis_id,
            "https://github.com/o/" + analysis_id,
            "github.com",
            "github.com#o/" + analysis_id,
            "upload",
        ),
    )
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
        "analyst, producer, status, created_at, report_md, report_json, counts_json, "
        "quality_json, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            analysis_id,
            repo_id,
            "sha-" + analysis_id,
            "memex/report/1",
            "standard",
            "tester",
            "batch" if status == STATUS_READY else "agent",
            status,
            "2026-01-01T00:00:00Z",
            "## 功能\n\n正文",
            "{}",
            "{}",
            "{}",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO features(feature_id, analysis_id, slug, title, summary, position) "
        "VALUES(?,?,?,?,?,?)",
        (feat_id, analysis_id, feat_id, "重试功能", "功能摘要", 0),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cards(card_id, feature_id, kind, reusable, title, summary, "
        "mechanism_desc) VALUES(?,?,?,?,?,?,?)",
        (card_id, feat_id, "mechanism", 1, "重试", "重试摘要", MECH),
    )
    conn.execute(
        "INSERT OR REPLACE INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id) "
        "VALUES(?,?,?,?,?,?)",
        ("card~" + card_id, "card", card_id, card_id, MECH, repo_id),
    )


def test_reindex_rebuilds_ready_analysis_from_vibecraft(tmp_path: Path) -> None:
    """status=ready（vibecraft 回填）的分析，reindex 后必须重新有块。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, "an-ready", STATUS_READY)
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1

        store_db.reindex(cfg.paths, embedder_spec="hash:64")

        chunks = conn.execute("SELECT chunk_id FROM chunks").fetchall()
        assert chunks, (
            "reindex 把 status=ready 的分析索引清空后没有重建"
            "（VibeCraft 回填的索引会被物理删除且无法找回）"
        )
    finally:
        conn.close()


def test_reindex_covers_both_committed_and_ready(tmp_path: Path) -> None:
    """agent 路径的 committed 与回填路径的 ready 都必须被选中。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, "an-committed", STATUS_COMMITTED)
        _seed(conn, "an-ready", STATUS_READY)
        before = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        assert before == 2, "种子没落对，analyses=" + str(before)

        res = store_db.reindex(cfg.paths, embedder_spec="hash:64")

        assert res["analyses"] == 2, (
            "reindex 只选了 " + str(res["analyses"]) + " 条分析，期望 2（committed + ready）"
        )
    finally:
        conn.close()


def test_reindex_refuses_unknown_status_before_destroying_anything(tmp_path: Path) -> None:
    """未知 status 要在**清空之前**就拒绝：报错时索引必须还在。

    连接是 autocommit，DELETE 一旦落盘再 raise 也救不回来——所以「报错」本身
    不够，必须验证派生表毫发无损，否则等于报了错但数据已经没了。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, "an-weird", "some_future_status")
        _seed(conn, "an-ready", STATUS_READY)
        chunks_before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunks_before == 2

        try:
            store_db.reindex(cfg.paths, embedder_spec="hash:64")
        except MemexError as exc:
            assert "some_future_status" in str(exc), str(exc)
        else:
            raise AssertionError(
                "reindex 遇到未知 status 应该显式报错"
                "（否则会把它删光索引却不吭声，正是 BUG-1 的形状）"
            )

        chunks_after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunks_after == chunks_before, (
            "reindex 报错后 chunks 从 " + str(chunks_before) + " 变成 " + str(chunks_after)
            + "：守卫放在了清空之后，等于「报了错但索引已经没了」"
        )
    finally:
        conn.close()
