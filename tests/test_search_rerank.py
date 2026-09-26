"""T8 rerank 死代码的修复（G25）。

G25 十五探针真模型实测已定案：ms-marco-MiniLM-L-6-v2 **有害**（top-1 10/15 → 4/15），
BAAI/bge-reranker-base **有效**（top-1 10/15 → 13/15，MRR 0.776 → 0.910）。
但 src/memex/store/search.py:164 的 _rerank_enabled **零调用点**，
search() 函数体除参数签名外从未引用 rerank —— 旋钮接到了，什么都没接。

本文件不测「我自己的复刻函数」，只测**产品代码 search() 的真实行为**：
  1. 注入假 cross-encoder 后，rerank 必须真的改变**顺序**（不是只改个分数字段）；
  2. rerank 只重排**不增删**条目（top-10 命中率按构造不变，reports/g25_B_rerank.json 已证）；
  3. rerank 失败必须**显式报错**（零假成功），不得静默退回 RRF；
  4. 未启用（默认 off）时结果与不开 rerank 完全一致，且**不加载任何模型**。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from memex.core import Config, MemexError  # noqa: E402
from memex.embeddings import hash_embedder, pack_vector  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store import search as S  # noqa: E402
from memex.store.search import search  # noqa: E402

# 两条候选：RRF 融合分让 A 在前，cross-encoder 让 B 在前，用来验证「顺序真被改写」。
A_TEXT = (
    "Retries are bounded by a retry budget: the budget counts attempts across the whole "
    "request, and when it is exhausted the failure is surfaced instead of sleeping again. "
    "src/urllib3/util/retry.py increment"
)
B_TEXT = (
    "The retry decision is delegated to a separate classifier that strips the error type "
    "from the exception first, so transport faults and protocol faults are judged apart. "
    "src/axum/extract/rejection.rs classify"
)
QUERY = "重试失败之后由谁决定还能不能再试一次"


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
        ("an1", "github.com__demo__demo", "deadbeef", "memex/report/1", "standard", "test",
         "test", "committed", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) VALUES(?,?,?,?,?,?)",
        ("fe1", "an1", "retry", "重试判定", "重试预算与失败分类", 0),
    )
    for cid, card, text in (("ca1", "ch_a", A_TEXT), ("ca2", "ch_b", B_TEXT)):
        conn.execute(
            "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, language, symbol, code_spans_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, "fe1", "mechanism", 1, "card-" + cid, "s", text, "python", "sym", "[]"),
        )
        conn.execute(
            "INSERT INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("ch_" + cid, "card", "fe1", cid, text, "github.com__demo__demo", "python", "h"),
        )
        conn.execute("INSERT INTO chunk_fts(chunk_id, text) VALUES(?,?)", ("ch_" + cid, text))
        # 必须写 chunk_vectors：不写的话向量通道是空的，中文 query 匹配英文块只能
        # 靠 trigram/LIKE，召回 0 条 —— 那是 fixture 的锅，不是 rerank 的锅。
        vec = hash_embedder(64).embed_one(text)
        conn.execute("INSERT INTO chunk_vectors(chunk_id, embedder, dim, vec) VALUES(?,?,?,?)",
                     ("ch_" + cid, "hash:64", 64, pack_vector(vec)))
    conn.commit()
    return conn


class FakeCrossEncoder:
    """假 cross-encoder：只看 pair 里 passage 侧内容，**不含任何 query 词**就判 B 高。

    这样只要顺序变了，就一定是真重排的结果，不是向量通道又跑了一遍。
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self.fail:
            raise RuntimeError("模拟 cross-encoder 加载后崩溃")
        out: list[float] = []
        for q, passage in pairs:
            self.calls.append((q, passage))
            out.append(1.0 if "axum/extract/rejection.rs" in passage else 0.0)
        return out


def test_rerank_真的重排而非只改分数(tmp_path):
    """核心断言：注入的 cross-encoder 判 B 更高，search() 就必须把 B 排到 A 前面。"""
    conn = _db(tmp_path)
    emb = hash_embedder(64)

    base = search(conn, emb, QUERY, limit=10)
    assert len(base["items"]) == 2, "前提：两条候选都应被召回"
    before = [it["chunk_id"] for it in base["items"]]

    enc = FakeCrossEncoder()
    out = search(conn, emb, QUERY, limit=10, rerank=enc)

    after = [it["chunk_id"] for it in out["items"]]
    assert enc.calls, "cross-encoder 必须真的被调用（当前是死代码）"
    assert len(out["items"]) == 2, "rerank 只能重排，不得增删条目"
    assert set(after) == set(before), "rerank 改变了候选集合，这与设计不符"
    assert after == ["ch_ca2", "ch_ca1"], f"cross-encoder 判 B 更高，顺序却没变：{after}"


def test_rerank_不增删条目(tmp_path):
    """top-10 命中率按构造不变——G25 实测已证，这里锁住这个不变量。"""
    conn = _db(tmp_path)
    emb = hash_embedder(64)
    base = search(conn, emb, QUERY, limit=10)
    out = search(conn, emb, QUERY, limit=10, rerank=FakeCrossEncoder())
    assert len(out["items"]) == len(base["items"])


def test_rerank_失败显式报错不静默退化(tmp_path):
    """零假成功：开了 rerank 却跑不通必须报错，不能悄悄退回 RRF 假装成功。"""
    conn = _db(tmp_path)
    with pytest.raises(MemexError) as ei:
        search(conn, hash_embedder(64), QUERY, limit=10, rerank=FakeCrossEncoder(fail=True))
    assert ei.value.code in ("internal", "unsupported"), ei.value.code
    assert ei.value.details, "报错必须带 details 说明怎么处置"


def test_默认关闭时不加载任何模型(tmp_path):
    """默认 off（G25 实测结论）：不开就不该碰 cross-encoder，零额外开销。"""
    conn = _db(tmp_path)
    emb = hash_embedder(64)
    plain = search(conn, emb, QUERY, limit=10)
    off = search(conn, emb, QUERY, limit=10, cfg_rerank="off")
    assert [i["chunk_id"] for i in plain["items"]] == [i["chunk_id"] for i in off["items"]]
    assert S._CROSS_ENC_CACHE == {} and S._CROSS_ENC_ERRORS == {}, (
        "默认关闭时不得构造 cross-encoder（当前实现连缓存都不该建）"
    )


def test_cfg_rerank_on走默认模型名(tmp_path, monkeypatch):
    """cfg_rerank='on' 必须解析成 G25 定案的默认模型名，而不是空操作。"""
    seen: list[str] = []

    def fake_load(name: str) -> FakeCrossEncoder:
        seen.append(name)
        return FakeCrossEncoder()

    monkeypatch.setattr(S, "_load_cross_encoder", fake_load)
    conn = _db(tmp_path)
    out = search(conn, hash_embedder(64), QUERY, limit=10, cfg_rerank="on")
    assert seen, "cfg_rerank=on 必须真的去加载默认 cross-encoder"
    assert seen[0] == S.DEFAULT_RERANK_MODEL, f"默认模型名应来自 G25 定案：{seen[0]}"
    assert [it["chunk_id"] for it in out["items"]] == ["ch_ca2", "ch_ca1"]
