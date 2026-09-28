import os

import pytest

os.environ.setdefault("MEMEX_HOME", "/tmp/memex_filter_test")
os.environ["MEMEX_EMBEDDER"] = "hash:64"

from memex.core import Config
from memex.store import db as store_db
from memex.store import search as search_store

Q = "zebra"


@pytest.fixture()
def conn(tmp_path):
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    # E17 真拆后必须用 store_db.connect（它会 ATTACH index.db），
    # 裸 sqlite3.connect 拿不到派生表 chunks。
    c = store_db.connect(cfg.paths.db)
    yield c
    c.close()


def _put_chunk(c, chunk_id, repo_id, text, *, kind="card", language="python"):
    c.execute(
        "INSERT OR REPLACE INTO chunks(chunk_id, kind, ref_id, card_id, text, repo_id, language, heading, producer) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (chunk_id, kind, "r_" + chunk_id, "c_" + chunk_id, text, repo_id, language, "h", "agent"),
    )


def _fill_noise(c, *, n=60, repo_id="github.com__source", kind="card", language="python"):
    """塞 n 个也命中查询的块，把通道 top-50 的名额占满。

    关键：噪声块**必须也含查询串**，否则它们根本不进候选，截断就不会被触发。
    chunk_id 用 "noiseNN"，字典序小于 "targetNN"，于是 50 个名额全被噪声吃掉。
    """
    for i in range(n):
        _put_chunk(c, "noise%02d" % i, repo_id, "zebra %d" % i, kind=kind, language=language)


def test_repo_filter_not_silently_truncated(conn):
    """按仓过滤必须发生在通道截断之前。

    旧实现：每通道先取全库 top-50 再在内存里按仓过滤。
    目标仓的块排在 50 名之外 -> 静默返回 0，调用方以为该仓没有。
    """
    _fill_noise(conn)
    for i in range(3):
        _put_chunk(conn, "target%d" % i, "github.com__target", "zebra %d" % i)

    got = search_store.search(conn, None, Q, limit=10, repo_id="github.com__target")
    assert got["total"] == 3, "按仓过滤被通道截断吃掉了：%r" % got["total"]
    assert got["count"] == 3
    assert {i["repo_id"] for i in got["items"]} == {"github.com__target"}


def test_kind_filter_not_silently_truncated(conn):
    """kind 过滤同样受通道截断影响。"""
    _fill_noise(conn, kind="feature")
    for i in range(2):
        _put_chunk(conn, "target%d" % i, "github.com__source", "zebra %d" % i, kind="gotcha")

    got = search_store.search(conn, None, Q, limit=10, kind="gotcha")
    assert got["total"] == 2, "kind 过滤被通道截断吃掉了：%r" % got["total"]


def test_language_filter_not_silently_truncated(conn):
    """language 过滤同样受通道截断影响。"""
    _fill_noise(conn, language="python")
    for i in range(2):
        _put_chunk(conn, "target%d" % i, "github.com__source", "zebra %d" % i, language="rust")

    got = search_store.search(conn, None, Q, limit=10, language="rust")
    assert got["total"] == 2, "language 过滤被通道截断吃掉了：%r" % got["total"]


def test_filter_still_excludes(conn):
    """修了截断不等于不过滤：谓词必须真的把不匹配的块挡掉。"""
    _fill_noise(conn, n=5)
    _put_chunk(conn, "target0", "github.com__target", "zebra")

    got = search_store.search(conn, None, Q, limit=10, repo_id="github.com__target")
    assert got["total"] == 1
    assert {i["repo_id"] for i in got["items"]} == {"github.com__target"}


def test_unfiltered_still_returns_everything(conn):
    """不加过滤时行为不变：通道截断照旧（那是 top-K 的本意）。"""
    _fill_noise(conn, n=60)
    for i in range(3):
        _put_chunk(conn, "target%d" % i, "github.com__target", "zebra %d" % i)

    got = search_store.search(conn, None, Q, limit=100)
    assert got["total"] == 50, "无过滤时通道上限应保持 50：%r" % got["total"]


def test_empty_after_filter_says_so(conn):
    """过滤后为空时 notes 要说「过滤后无命中」，不能让调用方读成「库里没有」。"""
    _put_chunk(conn, "a1", "github.com__source", "zebra")
    got = search_store.search(conn, None, Q, limit=10, repo_id="github.com__other")
    assert got["total"] == 0
    joined = " ".join(got["notes"])
    assert "过滤" in joined, "notes 没区分「过滤后无命中」与「库中无匹配」：%r" % (got["notes"],)
