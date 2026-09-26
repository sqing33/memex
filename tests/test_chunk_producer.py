# -*- coding: utf-8 -*-
"""G21 写侧：每条检索结果都要能回答「这条是 agent 分析出来的还是 batch 回填的」。

此前 docs/decisions.md 的 G21 承诺「search_implementations 结果里带 producer」，
但 chunks 表根本没有 producer 列 —— 读路径是空的，只有 recall_stats 的
by_producer 分组统计在回答，而单个结果回答不了。

本文件测的是**产品代码的真实链路**：建块落 producer -> 检索透出 -> T8 省略未知键，
以及 pattern 块的多数投票。不测自己复刻的实现。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.embeddings import hash_embedder, pack_vector  # noqa: E402
from memex.patterns.cluster import _vote_producer  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store import index as store_index  # noqa: E402
from memex.store.search import search  # noqa: E402

MECH = (
    "The retry decision is delegated to a separate classifier that strips the error "
    "type from the exception first, so transport faults and protocol faults are "
    "judged apart. src/axum/extract/rejection.rs classify"
)
QUERY = "失败之后由谁决定还能不能再重试一次"


def _setup(tmp_path: Path) -> Config:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg


def _seed(conn, *, suffix: str, producer: str) -> tuple[str, str]:
    """一条分析 + 一个功能 + 一张可复用卡，返回 (analysis_id, card_id)。

    每条分析落在自己的仓上：INSERT OR REPLACE 经 repo_id 外键 CASCADE 会挤掉同仓上一条。
    """
    repo_id = "gh__o__g21" + suffix
    aid = "an-g21-" + suffix
    fid = "f1-g21-" + suffix
    cid = "c1-g21-" + suffix
    conn.execute(
        "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
        "VALUES(?,?,?,?,?,?)",
        (repo_id, "o/g21" + suffix, "https://github.com/o/g21" + suffix, "github.com",
         "github.com#o/g21" + suffix, "upload"),
    )
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
        "analyst, producer, status, created_at, report_md, report_json, counts_json, "
        "quality_json, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, repo_id, "sha-" + suffix, "memex/report/1", "standard", "someone",
         producer, "committed", "2026-01-01T00:00:00Z", "## 功能\n\n正文", "{}", "{}", "{}",
         "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO features(feature_id, analysis_id, slug, title, summary, position) "
        "VALUES(?,?,?,?,?,0)",
        (fid, aid, "slug-" + suffix, "重试判定", "功能摘要"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cards(card_id, feature_id, kind, reusable, title, summary, "
        "mechanism_desc, language) VALUES(?,?,?,?,?,?,?,?)",
        (cid, fid, "mechanism", 1, "重试分类-" + suffix, "摘要", MECH, "rust"),
    )
    return aid, cid


def _chunk(conn, chunk_id: str):
    return conn.execute("SELECT producer FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()


def test_vote_producer_多数值() -> None:
    assert _vote_producer([{"producer": "agent"}, {"producer": "agent"}, {"producer": "batch"}]) == "agent"
    assert _vote_producer([{"producer": "batch"}, {"producer": "batch"}]) == "batch"


def test_vote_producer_平票与全空都给_不知道() -> None:
    # 平票：多数不存在，猜一个就是假信号
    assert _vote_producer([{"producer": "agent"}, {"producer": "batch"}]) is None
    assert _vote_producer([{"producer": None}, {"producer": None}]) is None
    assert _vote_producer([]) is None
    # 历史块 producer 是 NULL（不是字符串 "None"），同样要当「不知道」
    assert _vote_producer([{"producer": None}, {"producer": "agent"}]) == "agent"


def test_index_analysis_把_producer_写到块上(tmp_path: Path) -> None:
    """核心回归：块上必须有 producer，来源可追溯到建块那一刻。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="a", producer="batch")
        aid, cid = "an-g21-a", "c1-g21-a"
        out = store_index.index_analysis(conn, hash_embedder(64), aid, report_md="## 功能\n\n正文")
        assert out["chunks"] == 3, "feature + card + report_section 各一块"
        for kind, ref in (("feature", "f1-g21-a"), ("card", cid), ("report_section", aid)):
            row = _chunk(conn, store_index.chunk_id_for(kind, ref))
            assert row is not None, kind + " 块没建出来"
            assert row["producer"] == "batch", kind + " 块的 producer 应为 batch，实际 " + str(row["producer"])
    finally:
        conn.close()


def test_检索结果透出_producer(tmp_path: Path) -> None:
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="b", producer="agent")
        store_index.index_analysis(conn, hash_embedder(64), "an-g21-b")
        out = search(conn, hash_embedder(64), QUERY, limit=10)
        assert out["items"], "前提：中文 query 至少要召回一条"
        card_items = [it for it in out["items"] if it["kind"] == "card"]
        assert card_items, "前提：应召回 card 块"
        for it in card_items:
            assert it["producer"] == "agent", "search 必须把 producer 透出，实际 " + str(it.get("producer"))
    finally:
        conn.close()


def test_历史块_producer_为_NULL_而不是_默认_agent(tmp_path: Path) -> None:
    """加列之前建的块 producer 必须是 NULL。

    给个默认 'agent' 会让「不知道」看起来像「知道」—— batch 导入的历史知识
    会被当成 agent 亲手分析的，这是最典型的假信号。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="c", producer="batch")
        aid, cid = "an-g21-c", "c1-g21-c"
        # 模拟历史块：先按加列之前的形状手工建块，producer 不给
        text = MECH
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (store_index.chunk_id_for("card", cid), "card", "f1-g21-c", cid, text,
             "gh__o__g21c", "rust", "重试分类-c"),
        )
        conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)",
                     (store_index.chunk_id_for("card", cid), text))
        vec = hash_embedder(64).embed_one(text)
        conn.execute("INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
                     (store_index.chunk_id_for("card", cid), "hash:64", 64, pack_vector(vec)))
        conn.commit()
        assert aid

        row = _chunk(conn, store_index.chunk_id_for("card", cid))
        assert row is not None and row["producer"] is None, "历史块的 producer 必须是 NULL，不是 'agent'"

        out = search(conn, hash_embedder(64), QUERY, limit=10)
        card_items = [it for it in out["items"] if it["kind"] == "card"]
        assert card_items
        for it in card_items:
            assert it.get("producer") is None, "历史块不该凭空长出 producer，实际 " + str(it.get("producer"))
    finally:
        conn.close()


def test_T8_有来源给键_不知道省略键(tmp_path: Path) -> None:
    """走真实 Server：T8 结果里 producer 有值才出现这个键，不出现 null。

    results[] 是 additionalProperties:false 的契约——键不声明就出不来，
    声明了却恒为 null 只是噪音，还会逼调用方猜「null 是不是等于 agent」。
    """
    from memex.mcp.server import Server
    from memex.store import search as S

    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _seed(conn, suffix="d", producer="agent")
        store_index.index_analysis(conn, hash_embedder(64), "an-g21-d")
        # 混入一条「加列之前建的」历史块：producer 为 NULL
        _seed(conn, suffix="e", producer="batch")
        aid_e, cid_e = "an-g21-e", "c1-g21-e"
        conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?",
                     (store_index.chunk_id_for("card", cid_e),))
        conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?",
                     (store_index.chunk_id_for("card", cid_e),))
        conn.execute("DELETE FROM chunks WHERE chunk_id = ?",
                     (store_index.chunk_id_for("card", cid_e),))
        text = MECH
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (store_index.chunk_id_for("card", cid_e), "card", "f1-g21-e", cid_e, text,
             "gh__o__g21e", "rust", "重试分类-e"),
        )
        conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)",
                     (store_index.chunk_id_for("card", cid_e), text))
        vec = hash_embedder(64).embed_one(text)
        conn.execute("INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
                     (store_index.chunk_id_for("card", cid_e), "hash:64", 64, pack_vector(vec)))
        conn.commit()
    finally:
        conn.close()

    srv = Server(cfg)
    try:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "search_implementations", "arguments": {"query": QUERY, "limit": 10}}}
        resp = srv.handle_message(msg)
        assert resp is not None and "result" in resp, resp
        sc = resp["result"]["structuredContent"]
        assert sc["ok"] is True, sc
        card_items = [it for it in sc["results"] if it["chunk_kind"] == "card"]
        assert len(card_items) == 2, "前提：agent 块与历史块都应被召回，实际 " + str(len(card_items))

        by_card = {it["card_id"]: it for it in card_items}
        known = by_card["c1-g21-d"]
        assert known["producer"] == "agent", "agent 来源的结果必须带 producer 键"
        unknown = by_card["c1-g21-e"]
        assert "producer" not in unknown, (
            "来源不明的结果不该带恒为 null 的 producer 键，实际 " + repr(unknown.get("producer"))
        )
        assert S is not None
    finally:
        srv.close()


def _seed_cluster_card(conn, *, suffix: str, producer: str) -> None:
    """种一张可聚类的卡：正文与其他仓**逐字相同**（hash 嵌入无语义，异文即随机向量）。"""
    _seed(conn, suffix=suffix, producer=producer)
    cid = "c1-g21-" + suffix
    conn.execute(
        "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading, producer) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (store_index.chunk_id_for("card", cid), "card", "f1-g21-" + suffix, cid, MECH,
         "gh__o__g21" + suffix, "rust", "retry", producer),
    )
    conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)",
                 (store_index.chunk_id_for("card", cid), MECH))
    vec = hash_embedder(64).embed_one(MECH)
    conn.execute("INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
                 (store_index.chunk_id_for("card", cid), "hash:64", 64, pack_vector(vec)))


def test_pattern_块_取成员多数(tmp_path: Path) -> None:
    """真实 recluster：2 agent : 1 batch 成簇，pattern 块的 producer 必须是多数值。

    pattern 天然跨仓跨 producer，单值本来就是简写——「多数」是唯一说得过去的
    简写口径；没有多数就得说不知道。
    """
    from memex.patterns.cluster import recluster

    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        for suffix, producer in (("f", "agent"), ("g", "agent"), ("h", "batch")):
            _seed_cluster_card(conn, suffix=suffix, producer=producer)
        conn.commit()

        out = recluster(conn, cfg)
        assert out["patterns"] == 1, "前提：三张同机制卡跨三仓应成 1 个模式，实际 " + str(out["patterns"])

        row = conn.execute(
            "SELECT chunks.producer AS p, patterns.card_count AS n "
            "FROM chunks JOIN patterns ON patterns.pattern_id = chunks.ref_id "
            "WHERE chunks.kind = 'pattern'"
        ).fetchone()
        assert row is not None, "pattern 块没建出来"
        assert row["n"] == 3
        assert row["p"] == "agent", "2 agent : 1 batch，多数应是 agent，实际 " + str(row["p"])
    finally:
        conn.close()


def test_pattern_块_平票时不给_producer(tmp_path: Path) -> None:
    """一 agent 一 batch 成簇：平票没有多数，不得挑一个当事实。"""
    from memex.patterns.cluster import recluster

    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        for suffix, producer in (("i", "agent"), ("j", "batch")):
            _seed_cluster_card(conn, suffix=suffix, producer=producer)
        conn.commit()

        out = recluster(conn, cfg)
        assert out["patterns"] == 1, "前提：两仓同机制应成 1 个模式，实际 " + str(out["patterns"])

        row = conn.execute("SELECT producer FROM chunks WHERE kind = 'pattern'").fetchone()
        assert row is not None
        assert row["producer"] is None, "平票时 pattern 块的 producer 必须是 NULL，实际 " + str(row["producer"])
    finally:
        conn.close()
