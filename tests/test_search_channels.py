"""文本通道的两级降级（V3 实测修正）。

V3 首轮实测：15 个中文自然语言探针里，keyword / substr 两通道对整句查询恒返回 0 条
（三通道融合退化成单路向量，top-1 命中只有 60%）。根因是 trigram 分词器 + 短语查询，
以及 substr 侧只有 LIKE '%q%' 一条路径——二者都要求整串连续出现。

所以文本通道改为两级降级：整串连续优先，整串无命中时降级为 trigram OR 匹配。
本文件把这条行为钉死，并锁住「降级要在 notes 里显式可见」这条零假成功要求。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.embeddings import hash_embedder  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store.search import _keyword_rank, _substr_rank, search  # noqa: E402

# 块文本必须**不含**任何整串连续出现的片段：这是本测试的全部意义。
# 「用户注册时先发验证码到邮箱，验证码带有效期，注册时比对」在下面任何一段里都不连续。
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


def test_整串无命中时关键词通道降级为trigram_or(tmp_path):
    conn = _db(tmp_path)
    assert SENTENCE not in CARD_TEXT and SENTENCE not in FEATURE_TEXT
    ids = _keyword_rank(conn, SENTENCE)
    assert ids, "整句查询在关键词通道必须降级后有命中（旧代码恒返回空）"
    # 文本通道只做**字面**匹配：中文查询命中含中文片段的块，英文块不该被文本通道捞出来
    # （中文查英文卡是向量通道的职责，那是跨语言召回的来源）。
    assert "ch_feat" in ids, ids
    assert "ch_noise" not in ids, ids


def test_整串无命中时子串通道降级为逐trigram(tmp_path):
    conn = _db(tmp_path)
    ids = _substr_rank(conn, SENTENCE)
    assert ids, "整句查询在子串通道必须降级后有命中（旧代码恒返回空）"
    assert "ch_feat" in ids, ids
    assert "ch_noise" not in ids, ids


def test_整串连续匹配仍然优先且不被降级污染(tmp_path):
    """短查询/符号名的老行为不变：整串命中时结果与非降级路径一致。"""
    conn = _db(tmp_path)
    # deliver_code 整串连续出现在 CARD_TEXT 里
    ids = _keyword_rank(conn, "deliver_code")
    assert ids and ids[0] == "ch_card"


def test_三通道融合在整句查询下两个文本通道都有票(tmp_path):
    conn = _db(tmp_path)
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    assert out["channels"]["keyword"] > 0, out["channels"]
    assert out["channels"]["substr"] > 0, out["channels"]


def test_降级在notes里显式可见_零假成功(tmp_path):
    """降级不得静默：notes 必须说明文本通道用了哪一级。"""
    conn = _db(tmp_path)
    out = search(conn, hash_embedder(64), SENTENCE, limit=10)
    notes = out.get("notes") or []
    assert any("trigram" in n for n in notes), notes
