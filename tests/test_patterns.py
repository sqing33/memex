"""聚类测试：跨 2 个源仓的相似机制 -> 生成模式；单仓不生成。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.patterns import recluster  # noqa: E402
from memex.patterns.cluster import cluster_cards  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.embeddings import hash_embedder, pack_vector  # noqa: E402


def test_cluster_cards_pure():
    cards = [
        {"card_id": "a", "vec": [1.0, 0.0], "title": "A"},
        {"card_id": "b", "vec": [0.99, 0.01], "title": "B"},
        {"card_id": "c", "vec": [0.0, 1.0], "title": "C"},
    ]
    clusters = cluster_cards(cards, threshold=0.9)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def _seed_repo_and_card(conn, emb, *, repo_id, identity, group_fork=None, mech="Bounded retry with exponential backoff across all remote calls."):
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, identity_key, fork_of, source) VALUES(?,?,?,?,?,?,?)",
        (repo_id, repo_id.replace("__", "/"), "https://x/" + repo_id, "github.com", identity, group_fork, "fetch"),
    )
    aid = "ana_" + repo_id
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, analyst, producer, status, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (aid, repo_id, "sha1", "1", "standard", "t", "agent", "committed", "2024-01-01T00:00:00Z"),
    )
    fid = "feat_" + repo_id
    conn.execute("INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) VALUES(?,?,?,?,?,?)",
                 (fid, aid, "f", "T", "S", 0))
    cid = "card_" + repo_id
    conn.execute(
        "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, code_spans_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (cid, fid, "mechanism", 1, "Retry", "S", mech, "[]"),
    )
    vec = emb.embed_one(mech)
    conn.execute("INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text) VALUES(?,?,?,?,?)",
                 ("card~" + cid, "card", cid, cid, mech))
    conn.execute("INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
                 ("card~" + cid, emb.model, emb.dim, pack_vector(vec)))
    conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)", ("card~" + cid, mech))
    return cid


def test_recluster_requires_two_source_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    emb = hash_embedder(64)

    _seed_repo_and_card(conn, emb, repo_id="github.com__a__a", identity="github.com#a/a")
    _seed_repo_and_card(conn, emb, repo_id="github.com__b__b", identity="github.com#b/b")
    res = recluster(conn, cfg, threshold=0.5)
    assert res["patterns"] == 1, res
    assert conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pattern_members").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE kind='pattern'").fetchone()[0] == 1


def test_fork_does_not_count_as_second_source(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    emb = hash_embedder(64)

    _seed_repo_and_card(conn, emb, repo_id="github.com__a__a", identity="github.com#a/a")
    # b 是 a 的 fork：source_group 同为 github.com#a/a -> 不满足 min_repos=2
    _seed_repo_and_card(conn, emb, repo_id="github.com__b__b", identity="github.com#b/b", group_fork="github.com#a/a")
    res = recluster(conn, cfg, threshold=0.5)
    assert res["patterns"] == 0, res


def test_cluster_cards_no_transitive_absorption():
    """G24 回归：A~B 与 B~C 达标但 A~C 不达标时，三者不得并成一簇。

    并查集连通分量按传递闭包合并，A~B、B~C 达标就会把 cos(A,C) 只有 0.5 的
    C 也拖进簇。
    """
    # 单位圆上相隔 60 度：A 与 B 夹角 30 度、B 与 C 夹角 30 度、A 与 C 夹角 60 度
    cards = [
        {"card_id": "a", "vec": [1.0, 0.0], "title": "A"},
        {"card_id": "b", "vec": [0.8660254, 0.5], "title": "B"},
        {"card_id": "c", "vec": [0.5, 0.8660254], "title": "C"},
    ]
    # cos(A,B)=0.866  cos(B,C)=0.866  cos(A,C)=0.5  阈值 0.8
    clusters = cluster_cards(cards, threshold=0.8)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2], sizes


def test_cluster_cards_is_deterministic():
    """同输入必须同输出：greedy 按输入顺序选簇，不依赖哈希或集合顺序。"""
    cards = [
        {"card_id": "a", "vec": [1.0, 0.0], "title": "A"},
        {"card_id": "b", "vec": [0.0, 1.0], "title": "B"},
        {"card_id": "c", "vec": [1.0, 0.0], "title": "C"},
    ]
    one = sorted(sorted(c["card_id"] for c in cl) for cl in cluster_cards(cards, threshold=0.9))
    two = sorted(sorted(c["card_id"] for c in cl) for cl in cluster_cards(list(cards), threshold=0.9))
    assert one == two
    assert one == [["a", "c"], ["b"]]
