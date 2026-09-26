"""P1-3 / G12：mechanism_desc 非语言中立的卡片必须被排除出检索与聚类集。

背景：G12 承诺「卡片仍入库，但 reindex_state='pending' 且排除出检索/聚类集」。
此前跳过判断只写在 import-vibecraft 的建块循环里，而 reindex 走的是
index_analysis 同一个函数——跑一次 reindex，中文 mechanism_desc 就被建成块，
进入三通道 RRF 与 patterns/cluster.py（实测复现：块数 0 -> 1）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store import index as store_index  # noqa: E402

# 英文（语言中立）描述：应该建块
MECH_EN = "Poll a background worker and wake the caller once the threshold is crossed."
# 中文描述：G12 说的那类，不该建块
MECH_ZH = "用后台线程做轮询，超过阈值就触发一次回调通知调用方继续处理。"


def _setup(tmp_path: Path) -> Config:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg


def _seed(
    conn,
    *,
    suffix: str,
    pending: bool,
    status: str = "committed",
) -> tuple[str, str]:
    """一条分析 + 一个功能 + 一张卡，返回 (analysis_id, card_id)。

    每条分析落在自己的仓上：features 走 INSERT OR REPLACE 会经 repo_id 外键的
    ON DELETE CASCADE 把同仓的上一条分析连带删掉（踩过一次）。
    """
    repo_id = "gh__o__p13" + suffix
    aid = "an-p13-" + suffix
    fid = "f1-p13-" + suffix
    cid = "c1-p13-" + suffix
    conn.execute(
        "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
        "VALUES(?,?,?,?,?,?)",
        (repo_id, "o/p13" + suffix, "https://github.com/o/p13" + suffix, "github.com",
         "github.com#o/p13" + suffix, "upload"),
    )
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
        "analyst, producer, status, created_at, report_md, report_json, counts_json, "
        "quality_json, finished_at, reindex_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, repo_id, "sha-" + suffix, "memex/report/1", "standard", "vibecraft-import",
         "batch", status, "2026-01-01T00:00:00Z", "## 功能\n\n正文", "{}", "{}", "{}",
         "2026-01-01T00:00:00Z", "pending" if pending else "indexed"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO features(feature_id, analysis_id, slug, title, summary, position) "
        "VALUES(?,?,?,?,?,0)",
        (fid, aid, "slug-" + suffix, "轮询功能", "功能摘要"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cards(card_id, feature_id, kind, reusable, title, summary, "
        "mechanism_desc, quality_json) VALUES(?,?,?,?,?,?,?,?)",
        (cid, fid, "mechanism", 1, "轮询", "轮询摘要",
         MECH_ZH if pending else MECH_EN,
         json.dumps({"source_repo": repo_id, "pending": pending}, ensure_ascii=False)),
    )
    return aid, cid


def _chunk_count(conn, card_id: str) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM chunks WHERE card_id = ?", (card_id,)).fetchone()[0]
    )


def test_reindex_does_not_build_chunks_for_pending_card(tmp_path: Path) -> None:
    """核心回归：reindex 之后 pending 卡片仍然没有块。

    这是本条缺陷的原形状——闸门写在回填调用方、reindex 绕过去。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="a", pending=True)
        store_db.reindex(cfg.paths, embedder_spec="hash:64")
        n = _chunk_count(conn, "c1-p13-a")
        assert n == 0, (
            "reindex 给 reindex_state='pending' 的中文卡片建了 " + str(n) + " 个块，期望 0"
            "（G12 承诺排除出检索/聚类集）"
        )
    finally:
        conn.close()


def test_index_analysis_skips_pending_but_keeps_normal_cards(tmp_path: Path) -> None:
    """同一仓里两张卡：一张 pending 一张正常，只有正常那张建块。

    闸门不能粗到整条分析一起跳过——分析级 reindex_state 是标量，
    表达不了「这张补好了那张没补」的卡片级粒度（P1-3 的核心决定）。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    emb = __import__("memex.embeddings", fromlist=["get_embedder"]).get_embedder("hash:64")
    try:
        _seed(conn, suffix="b", pending=False)
        _seed(conn, suffix="c", pending=True)
        # 把 pending 卡挂到正常的分析下，模拟「一次分析里两张卡」
        conn.execute(
            "UPDATE cards SET feature_id = (SELECT feature_id FROM cards WHERE card_id = ?) "
            "WHERE card_id = ?",
            ("c1-p13-b", "c1-p13-c"),
        )
        res = store_index.index_analysis(conn, emb, "an-p13-b", report_md=None)
        assert res["pending_cards"] == 1, (
            "pending_cards 期望 1，实际 " + str(res["pending_cards"])
        )
        assert _chunk_count(conn, "c1-p13-b") == 1, "正常卡片必须照常建块"
        assert _chunk_count(conn, "c1-p13-c") == 0, "pending 卡片不该建块"
    finally:
        conn.close()


def test_pending_card_stays_readable_via_get_card_path(tmp_path: Path) -> None:
    """pending 卡片不建块，但**仍在 cards 表里**——派生层被拦不等于真源被删。

    get_card 是「这个仓当初分析出了什么」的路径，不是检索路径。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="d", pending=True)
        store_db.reindex(cfg.paths, embedder_spec="hash:64")
        row = conn.execute(
            "SELECT card_id, mechanism_desc FROM cards WHERE card_id = ?", ("c1-p13-d",)
        ).fetchone()
        assert row is not None, "pending 卡片不该从 cards 表消失（真源层，闸门只在派生层）"
        assert row["mechanism_desc"] == MECH_ZH
    finally:
        conn.close()


def test_recall_stats_reports_pending_mechanism(tmp_path: Path) -> None:
    """G12 要求 recall_stats 单列 pending_mechanism 计数，实现此前是零。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="e", pending=True)
        _seed(conn, suffix="f", pending=False)
        st = store_db.stats(conn)
        assert st["pending_mechanism"] == 1, (
            "pending_mechanism 期望 1（只有中文那张），实际 " + str(st["pending_mechanism"])
        )
        assert st["cards"] == 2, "两张卡都在（真源层不删）"
    finally:
        conn.close()


def test_is_pending_defaults_to_false_on_unreadable_quality_json(tmp_path: Path) -> None:
    """读不出 / 不是布尔 / 没有这个键——一律当非 pending。

    闸门只拦「明确标了待补」的卡：宁可放行，也别把正常卡片静默剔出索引，
    那是另一种假信号（比少召回更坏：调用方以为库里没有这张卡）。
    """
    from memex.store.index import _is_pending

    assert _is_pending(json.dumps({"pending": True})) is True
    assert _is_pending(json.dumps({"pending": False})) is False
    assert _is_pending(json.dumps({})) is False
    assert _is_pending(json.dumps({"pending": "yes"})) is True   # 真值即真
    assert _is_pending(None) is False
    assert _is_pending("") is False
    assert _is_pending("not json at all") is False
    assert _is_pending(json.dumps([1, 2, 3])) is False           # 不是 dict
