"""三通道 RRF 的投票权重（G29）。

V3 十五探针实测推翻了「三通道等权」：在 trigram 降级路径上 keyword 与 substr
是**同一个测量**（13/15 探针返回完全相同的集合，平均 Jaccard 0.932），
两者各占 1/3 等于把字面信号加权两次。实测把 substr 改成「仅 keyword 返空时兜底投票」后，
指标从 top-1 9/15、top-10 14/15、MRR 0.722 变为 10/15、15/15、0.776。

本文件不测「我自己的复刻函数」，只测**产品代码 search() 的真实行为**：
  1. 正常路径（keyword 非空）—— substr **不得**参与投票；
  2. 兜底路径（keyword 返空，模拟 FTS5 失效）—— substr 必须能独立把候选捞回来。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from memex.constants import KIND_PRIOR  # noqa: E402
from memex.core import Config  # noqa: E402
from memex.embeddings import hash_embedder  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store import search as S  # noqa: E402
from memex.store.search import search  # noqa: E402

CARD_TEXT = (
    "Verification codes are delivered by mail before they are persisted into Redis with EX 120s, "
    "then compared as plain strings during sign up to reject expired or replayed codes. "
    "src/services/mail.py deliver_code"
)
FEATURE_TEXT = (
    "邮箱验证码注册（Redis TTL 门禁 + 雪花ID落库）。注册流程先投递邮件再写入缓存，"
    "缓存条目带两分钟过期时间，校验阶段做字符串比对。邮箱验证码注册"
)
NOISE_TEXT = "Totally unrelated chunk about disk caching and LRU eviction journals rebuilt atomically."
SENTENCE = "用户注册时先发验证码到邮箱，验证码带有效期，注册时比对"


def _db(tmp_path: Path) -> sqlite3.Connection:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, identity_key, source, head_sha, repo_path, default_branch) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("github.com__demo__demo", "demo/demo", "https://github.com/demo/demo", "github.com",
         "github.com#demo/demo", "fetch", "deadbeef", str(tmp_path), "main"),
    )
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, analyst, producer, status, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("an1", "github.com__demo__demo", "deadbeef", "memex/report/1", "standard", "test", "test", "ok", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) VALUES(?,?,?,?,?,?)",
        ("fe1", "an1", "mail-code", "邮箱验证码注册", "先发信后落缓存", 0),
    )
    conn.execute(
        "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) VALUES(?,?,?,?,?,?)",
        ("fe2", "an1", "disk-cache", "Cache eviction", "LRU journal", 1),
    )
    conn.execute(
        "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, language, symbol, code_spans_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ca1", "fe1", "mechanism", 1, "验证码先发送后落 Redis", "先发信后写缓存", CARD_TEXT, "python", "deliver_code", "[]"),
    )
    conn.execute(
        "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, language, symbol, code_spans_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ca2", "fe2", "mechanism", 1, "Cache eviction", "LRU", NOISE_TEXT, "python", "evict", "[]"),
    )
    rows = [
        ("ch_card", "card", "fe1", "ca1", CARD_TEXT, "验证码先发送后落 Redis"),
        ("ch_feat", "feature", "fe1", None, FEATURE_TEXT, "邮箱验证码注册"),
        ("ch_noise", "card", "fe2", "ca2", NOISE_TEXT, "Cache eviction"),
    ]
    for cid, kind, ref, card, text, head in rows:
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading) VALUES(?,?,?,?,?,?,?,?)",
            (cid, kind, ref, card, text, "github.com__demo__demo", "python", head),
        )
        conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)", (cid, text))
    conn.commit()
    return conn


def test_正常路径下substr不参与投票(tmp_path, monkeypatch):
    """keyword 非空时 substr 必须零票——否则字面信号被加权两次（G29 修正的正是这条）。"""
    conn = _db(tmp_path)
    votes: list[list[str]] = []

    kw = S._keyword_rank(conn, SENTENCE, [])
    votes.append(list(kw))
    votes.append(list(S._substr_rank(conn, SENTENCE, [])))
    assert kw, "前提：整句查询的 keyword 通道必须非空"
    assert votes[1], "前提：整句查询的 substr 通道必须非空"

    real_rrf = S._rrf
    seen: list[list[list[str]]] = []

    def spy(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
        seen.append([list(x) for x in rank_lists])
        return real_rrf(rank_lists, k)

    monkeypatch.setattr(S, "_rrf", spy)
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    assert seen, "search() 必须调用 _rrf"
    # keyword 非空 → 融合只喂两路（vector / keyword），substr 不进融合
    assert len(seen[0]) == 2, f"keyword 非空时只该融合 2 路，实际 {len(seen[0])}：{seen[0]}"

    # matched_by 是**出处**不是**票数**：substr 确实命中了，所以照实上报；
    # 但它的贡献分不在 fused 里。这两条必须分开看。
    items = {it["chunk_id"]: it for it in out["items"]}
    assert "ch_feat" in items
    assert "substr" in items["ch_feat"]["matched_by"], "matched_by 应如实上报 substr 命中过"
    ch_feat = items["ch_feat"]
    fused = real_rrf(seen[0], 60)
    # 注：score 落库时被舍入到 6 位小数，故用 rel 容差比较
    assert ch_feat["score"] == pytest.approx(
        fused["ch_feat"] * KIND_PRIOR["feature"], rel=1e-3
    ), "融合分必须只来自 vector/keyword 两路"


def test_keyword返空时substr兜底投票(tmp_path, monkeypatch):
    """模拟 FTS5 失效：keyword 返空，substr 必须能独立把候选捞回来（V3 探针 11 的依据）。"""
    conn = _db(tmp_path)
    monkeypatch.setattr(S, "_keyword_rank", lambda conn, q, notes=None: [])

    real_rrf = S._rrf
    seen: list[list[list[str]]] = []

    def spy(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
        seen.append([list(x) for x in rank_lists])
        return real_rrf(rank_lists, k)

    monkeypatch.setattr(S, "_rrf", spy)
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    assert seen
    assert len(seen[0]) == 3, f"keyword 返空时 substr 必须补第三路，实际 {len(seen[0])}"
    items = {it["chunk_id"]: it for it in out["items"]}
    assert "ch_feat" in items, out["items"]
    assert "substr" in items["ch_feat"]["matched_by"], items["ch_feat"]["matched_by"]


def test_channels仍如实上报三通道条数(tmp_path, monkeypatch):
    """兜底是投票规则，不是上报规则：channels 必须仍报三通道真实条数。"""
    conn = _db(tmp_path)
    monkeypatch.setattr(S, "_keyword_rank", lambda conn, q, notes=None: [])
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    assert out["channels"]["keyword"] == 0
    assert out["channels"]["substr"] > 0


def test_正常路径目标块仍能召回(tmp_path):
    """兜底规则不该把原本能召回的块踢出结果集。"""
    conn = _db(tmp_path)
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    assert out["channels"]["keyword"] > 0
    items = {it["chunk_id"]: it for it in out["items"]}
    # 中文查询在字面通道只沾得到中文块 ch_feat（跨语言块靠向量通道，本 fixture 的
    # hash 嵌入器无跨语言能力），所以这里只断言目标块在结果里。
    assert "ch_feat" in items, sorted(items)
