"""P1-4：reindex 分仓重建（--repo）的回归测试。

三条不变量：
1. 只动目标仓的块，另一个仓的块数与向量数完全不变；
2. 不存在的仓必须显式报错，且**在动任何块之前**（autocommit 连接，事后报错等于索引已被删）；
3. 库里混着别的 embedder 标识时必须报出是哪几个仓（G11 混模型可诊断）。

每条分析落自己的仓：features 走 INSERT OR REPLACE 会经 repo_id 外键的
ON DELETE CASCADE 把同仓的上一条分析连带删掉。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from memex.core import Config, MemexError  # noqa: E402
from memex.core import Paths  # noqa: E402
from memex.store import db as store_db  # noqa: E402


def _seed(paths: Paths, repo_id: str) -> str:
    """种一个仓 + 一条已提交分析 + 一张可复用卡，返回 analysis_id。"""
    conn = store_db.connect(paths.db)
    try:
        suffix = repo_id.replace("/", "").replace("-", "")
        aid = "an-p14-" + suffix
        fid = "f-p14-" + suffix
        cid = "c-p14-" + suffix
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
            "VALUES(?,?,?,?,?,?)",
            (repo_id, repo_id, "https://x/" + repo_id, "x", "x#" + repo_id, "upload"),
        )
        conn.execute(
            "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
            "analyst, producer, status, created_at, report_md, report_json, counts_json, "
            "quality_json, finished_at, reindex_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, repo_id, "sha-" + suffix, "memex/report/1", "standard", "t", "agent",
             "committed", "2026-01-01T00:00:00Z", "## retry", "{}", "{}",
             "{}", "2026-01-01T00:00:00Z", "indexed"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO features(feature_id, analysis_id, slug, title, summary, position) "
            "VALUES(?,?,?,?,?,0)",
            (fid, aid, "retry", "重试与退避", "对可重试错误做指数退避重试。"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO cards(card_id, feature_id, kind, reusable, title, summary, "
            "mechanism_desc, quality_json) VALUES(?,?,?,?,?,?,?,?)",
            (cid, fid, "mechanism", 1, "重试退避", "用指数退避加抖动重试可重试错误。",
             "Retries transient failures with exponential backoff and jitter.", "{}"),
        )
        return aid
    finally:
        conn.close()


@pytest.fixture()
def paths(tmp_path) -> Paths:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg.paths


def _shape(conn: sqlite3.Connection) -> tuple[dict[str, int], int]:
    """(按仓分组的块数, 向量总数)。"""
    by_repo = {
        r["repo_id"] or "(null)": r["n"]
        for r in conn.execute("SELECT repo_id, COUNT(*) AS n FROM chunks GROUP BY repo_id").fetchall()
    }
    n_vec = conn.execute("SELECT COUNT(*) AS n FROM chunk_vectors").fetchone()["n"]
    return by_repo, n_vec


def test_reindex_by_repo_only_touches_that_repo(paths: Paths) -> None:
    """分仓重建：目标仓块数不变，另一个仓的块与向量原封不动。"""
    _seed(paths, "o/repo-a")
    _seed(paths, "o/repo-b")
    store_db.reindex(paths, embedder_spec="hash:64")

    conn = store_db.connect(paths.db)
    try:
        before, vec_before = _shape(conn)
    finally:
        conn.close()
    assert set(before) == {"o/repo-a", "o/repo-b"}

    res = store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a"])
    assert res["scope"] == "repos"
    assert res["repos"] == ["o/repo-a"]
    assert res["analyses"] == 1

    conn = store_db.connect(paths.db)
    try:
        after, vec_after = _shape(conn)
    finally:
        conn.close()
    # 漏删时会出现同一 id 的新旧两块并存 → repo-a 块数翻倍
    assert after["o/repo-a"] == before["o/repo-a"], (before, after)
    assert after["o/repo-b"] == before["o/repo-b"], (before, after)
    # pattern 块是跨仓的，recluster 重写时 repo_id 恒为 None（cluster.py:231），
    # 所以 (null) 分组的块数**会**变——这不是「动了别的仓的块」，是聚类结果的固有形状。
    assert set(after) - {"(null)"} == set(before)
    assert vec_after >= vec_before, (vec_before, vec_after)


def test_reindex_by_repo_accepts_multiple_repos(paths: Paths) -> None:
    """--repo 可重复：指定两个仓就重建两个。"""
    _seed(paths, "o/repo-a")
    _seed(paths, "o/repo-b")
    _seed(paths, "o/repo-c")
    res = store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a", "o/repo-c"])
    assert res["repos"] == ["o/repo-a", "o/repo-c"]
    assert res["analyses"] == 2


def test_reindex_by_unknown_repo_errors_and_changes_nothing(paths: Paths) -> None:
    """不存在的仓必须显式报错，且在动任何块之前。"""
    _seed(paths, "o/repo-a")
    store_db.reindex(paths, embedder_spec="hash:64")

    conn = store_db.connect(paths.db)
    try:
        before = _shape(conn)
    finally:
        conn.close()

    with pytest.raises(MemexError) as ei:
        store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a", "o/nope"])
    err = ei.value
    assert err.code == "not_found"
    assert "未做任何重建" in err.message
    assert err.details["missing_repos"] == ["o/nope"]

    conn = store_db.connect(paths.db)
    try:
        after = _shape(conn)
    finally:
        conn.close()
    assert after == before


def test_reindex_by_repo_reports_embedder_mismatch(paths: Paths) -> None:
    """库里混着别的 embedder 标识时必须报出是哪几个仓。"""
    _seed(paths, "o/repo-a")
    _seed(paths, "o/repo-b")
    store_db.reindex(paths, embedder_spec="hash:64")

    conn = store_db.connect(paths.db)
    try:
        conn.execute("UPDATE chunk_vectors SET embedder = ?", ("old-model/32",))
        conn.commit()
    finally:
        conn.close()

    res = store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a"])
    assert res["embedder_mismatch_repos"] == ["o/repo-b"]


def test_reindex_full_scope_reports_repos_none(paths: Paths) -> None:
    """不给 --repo 时仍走整层清空：scope=all、repos=None、mismatch 必为空。"""
    _seed(paths, "o/repo-a")
    res = store_db.reindex(paths, embedder_spec="hash:64")
    assert res["scope"] == "all"
    assert res["repos"] is None
    assert res["embedder_mismatch_repos"] == []
    assert res["recluster"] is None
    assert res["chunks"] > 0


def test_reindex_by_repo_reruns_recluster(paths: Paths) -> None:
    """分仓重建必须重跑聚类：pattern 块是跨仓的，不重聚相似度失真。"""
    _seed(paths, "o/repo-a")
    res = store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a"])
    assert res["recluster"] is not None
    assert "patterns" in res["recluster"]


def test_reindex_by_repo_drops_orphan_chunks(paths: Paths) -> None:
    """孤儿块（ref 已不存在）必须被清掉，不能带着旧向量留在检索集里。"""
    _seed(paths, "o/repo-a")
    store_db.reindex(paths, embedder_spec="hash:64")

    conn = store_db.connect(paths.db)
    try:
        # 模拟：某张卡从报告里删掉了，但它的块还留在 chunks 里
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("card~c-ghost", "card", "c-ghost", "c-ghost", "幽灵卡片的描述",
             "o/repo-a", "go", "幽灵卡片"),
        )
        conn.execute(
            "INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
            ("card~c-ghost", "hash:64", 64, bytes(64 * 4)),
        )
        conn.commit()
        n_before = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE chunk_id = ?", ("card~c-ghost",)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n_before == 1

    store_db.reindex(paths, embedder_spec="hash:64", repo_ids=["o/repo-a"])

    conn = store_db.connect(paths.db)
    try:
        n_after = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE chunk_id = ?", ("card~c-ghost",)
        ).fetchone()["n"]
        v_after = conn.execute(
            "SELECT COUNT(*) AS n FROM chunk_vectors WHERE chunk_id = ?", ("card~c-ghost",)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n_after == 0, "孤儿块没被清掉：召回会命中一条早已不存在的卡片"
    assert v_after == 0, "孤儿块的向量没被清掉"
