"""VibeCraft 回填路径的回归测试（G12 / V5 P0-1b / P0-2）。

这个模块此前**零测试**，而它恰恰是 analyses.status 口径与索引闭环两处缺陷的所在地。
测试用最小真源库（三个表：repo_sources / repo_analysis_results / repo_knowledge_cards）
+ 一个真实文件根目录，让证据能从真文件重切。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memex.core import Config, Paths
from memex.import_ import vibecraft
from memex.store import db as store_db


def _make_source_db(path: Path, repo_key: str, repo_root: Path) -> None:
    """造一个最小 VibeCraft 源库：1 仓 / 1 次分析 / 1 张卡 + 1 条证据。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE repo_sources (
            repo_key TEXT PRIMARY KEY, full_name TEXT, url TEXT,
            clone_path TEXT, language TEXT
        );
        CREATE TABLE repo_analysis_results (
            repo_key TEXT, analysis_id TEXT, status TEXT, commit_sha TEXT
        );
        CREATE TABLE repo_knowledge_cards (
            id TEXT PRIMARY KEY, repo_key TEXT, analysis_id TEXT,
            card_type TEXT, title TEXT, summary TEXT, mechanism_desc TEXT,
            intent TEXT, feature_key TEXT
        );
        CREATE TABLE repo_knowledge_evidence (
            card_id TEXT, path TEXT, start_line INTEGER, end_line INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO repo_sources VALUES(?,?,?,?,?)",
        (repo_key, "demo/widget", "https://github.com/demo/widget",
         str(repo_root), "go"),
    )
    conn.execute(
        "INSERT INTO repo_analysis_results VALUES(?,?,?,?)",
        (repo_key, "an-1", "ok", "deadbeef"),
    )
    for cid, fkey, title, summary, mech in (
        (
            "c-1", "retry-loop", "带重试的调用循环",
            "调用失败后按退避重试，最多三次。",
            "The call site retries a failed remote request with bounded "
            "exponential backoff, and only gives up after the final attempt "
            "has also failed on the server side.",
        ),
        (
            "c-2", "conn-pool", "连接池的复用与回收",
            "连接归还前先确认没有未完成的读取。",
            "The pool returns an idle connection to the reuse list only "
            "after the pending read has finished, so a half drained socket "
            "is never handed to the next caller.",
        ),
        (
            "c-3", "disk-cache", "磁盘缓存先记账后落文件",
            "先写日志再落数据，崩溃后可重放。",
            "The disk cache writes a journal entry before the payload is "
            "flushed, so a crash between the two steps can be replayed on "
            "the next open instead of leaving a torn entry behind.",
        ),
    ):
        conn.execute(
            "INSERT INTO repo_knowledge_cards VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, repo_key, "an-1", "snippet", title, summary, mech,
             "I want a reusable mechanism from another repository.", fkey),
        )
    for cid, path, s0, e0 in (
        ("c-1", "client.go", 1, 2),
        ("c-2", "pool.go", 1, 2),
        ("c-3", "cache.go", 1, 2),
    ):
        conn.execute(
            "INSERT INTO repo_knowledge_evidence VALUES(?,?,?,?)", (cid, path, s0, e0)
        )
    conn.commit()
    conn.close()


def _setup(tmp_path: Path) -> tuple[Paths, Path]:
    repo_root = tmp_path / "widget"
    repo_root.mkdir()
    for fn in ("client.go", "pool.go", "cache.go"):
        (repo_root / fn).write_text(
            "func Call() error {\n\treturn nil\n}\n", encoding="utf-8"
        )
    src_db = tmp_path / "vibecraft.db"
    _make_source_db(src_db, "demo/widget", repo_root)
    home = tmp_path / "home"
    home.mkdir()
    paths = Paths(home=home)
    return paths, src_db


def _rows(paths: Paths, sql: str, *params: object) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(paths.db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_vibecraft_import_writes_committed_status(tmp_path: Path) -> None:
    """P0-1b：回填的 status 必须是 committed。

    此前 vibecraft 写 "ready"，而 docs/mcp-tools.md T11 的枚举里根本没有 ready，
    结果回填的分析在 T11 get_report / T12 list_repos 的 status='committed'
    筛选下**完全不可见**——回填等于白回填。
    """
    paths, src_db = _setup(tmp_path)
    vibecraft.import_vibecraft(paths=paths, path=str(src_db))

    rows = _rows(paths, "SELECT status, producer, analyst FROM analyses")
    assert len(rows) == 1, "应导入 1 次分析，实际 " + str(len(rows))
    assert rows[0]["status"] == "committed", (
        "回填 status 期望 committed（docs 枚举里没有 ready），实际 " + repr(rows[0]["status"])
    )
    assert rows[0]["producer"] == "batch"
    assert rows[0]["analyst"] == "vibecraft-import"


def test_vibecraft_import_is_visible_to_get_report_path(tmp_path: Path) -> None:
    """回填的分析必须能被 T11/T12 那条 status='committed' 的读路径看见。"""
    paths, src_db = _setup(tmp_path)
    vibecraft.import_vibecraft(paths=paths, path=str(src_db))

    seen = _rows(paths, "SELECT analysis_id FROM analyses WHERE status = 'committed'")
    assert len(seen) == 1, (
        "status='committed' 的读路径应能看到回填分析，实际看到 " + str(len(seen))
    )


def test_vibecraft_import_survives_reindex(tmp_path: Path) -> None:
    """P0-2 的核心：回填的块必须能在 reindex 后**被重建**，而不是被删光。

    这正是 BUG-1 的形状：reindex 清空 chunks/chunk_vectors/chunk_fts 后只按
    committed 重建；回填写 ready 时索引被物理删除且永不重建。
    """
    from memex.store import db as store_db

    paths, src_db = _setup(tmp_path)
    vibecraft.import_vibecraft(paths=paths, path=str(src_db))

    before = _rows(paths, "SELECT COUNT(*) AS n FROM chunks")
    assert before and before[0]["n"] > 0, "回填应至少产出 1 块"

    res = store_db.reindex(paths, embedder_spec="hash:64")
    assert res["analyses"] == 1, (
        "reindex 应选中 1 条回填分析，实际 " + str(res["analyses"]))
    after = _rows(paths, "SELECT COUNT(*) AS n FROM chunks")
    assert after and after[0]["n"] > 0, "reindex 后回填的块被删光且没重建"


def test_vibecraft_dry_run_writes_nothing(tmp_path: Path) -> None:
    """--dry-run 只出报告不落库。"""
    paths, src_db = _setup(tmp_path)
    res = vibecraft.import_vibecraft(paths=paths, path=str(src_db), dry_run=True)
    assert res["ok"] is True and res["dry_run"] is True
    assert not paths.db.exists() or _rows(paths, "SELECT COUNT(*) AS n FROM analyses")[0]["n"] == 0


def test_vibecraft_import_is_searchable_without_reindex(tmp_path: Path) -> None:
    """P0-2：回填完**不跑 reindex** 也必须能被 search 召回。

    旧行为是写完块就结束，返回体只说「下一步去跑 reindex」；链路上任何一环
    出问题（status 对不上、pending 被跳过、用户忘了跑），回填内容就既不可见
    也召不回。索引必须在回填的同一次调用里建好。
    """
    from memex.embeddings import hash_embedder
    from memex.store import search as store_search
    from memex.store import db as store_db

    paths, src_db = _setup(tmp_path)
    cfg = Config.from_env()
    cfg.embedder = "hash:64"
    vibecraft.import_vibecraft(paths=paths, cfg=cfg, path=str(src_db))

    conn = store_db.connect(str(paths.db))
    try:
        vecs = _rows_count(conn, "SELECT COUNT(*) AS n FROM chunk_vectors")
        fts = _rows_count(conn, "SELECT COUNT(*) AS n FROM chunk_fts")
        assert vecs > 0, "回填后 chunk_vectors 为空——索引没建，search 的向量通道是死的"
        assert fts > 0, "回填后 chunk_fts 为空——keyword 通道是死的"

        emb = hash_embedder(64)
        hits = store_search.search(conn, emb, "指数退避重试失败的远程调用", limit=5)
        assert hits["items"], "回填内容应能被中文查询召回"
    finally:
        conn.close()


def test_vibecraft_import_preserves_fork_and_alias_relations(tmp_path: Path) -> None:
    """P1-2：回填不该抹掉 fetch 阶段写入的身份关系。

    此前用 INSERT OR REPLACE 写 repos，而 REPLACE 语义是「删旧行再插新行」：
    列清单里没写的 fork_of / aliases_json / merged_into 全回到默认值，
    于是回填一次就把 G10 写侧刚查到的 fork 关系、别名轨迹、合并指向抹成空——
    跨仓去重（COALESCE(fork_of, identity_key)）随之失效且无任何报错。
    """
    paths, src_db = _setup(tmp_path)
    # 先建表再种数据：import_vibecraft 自己会建库，但这里要提前往 repos 里塞关系
    store_db.init_db(paths, embedder_spec="hash:64")
    conn = sqlite3.connect(str(paths.db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO repos(repo_id, full_name, url, host, identity_key, source, "
            "is_fork, fork_of, aliases_json, merged_into) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("github.com__demo__widget", "demo/widget", "https://github.com/demo/widget",
             "github.com", "github.com#424242", "clone", 1, "demo/upstream",
             '["demo/oldname"]', "github.com__demo__upstream"),
        )
        conn.commit()
    finally:
        conn.close()

    vibecraft.import_vibecraft(paths=paths, path=str(src_db))

    row = _rows(paths, "SELECT is_fork, fork_of, aliases_json, merged_into "
               "FROM repos WHERE repo_id = ?", "github.com__demo__widget")[0]
    assert bool(row["is_fork"]) is True, "回填把 is_fork 抹成了 0——fork 关系没了"
    assert row["fork_of"] == "demo/upstream", "回填把 fork_of 抹成了空"
    assert row["aliases_json"] == '["demo/oldname"]', "回填把别名轨迹抹成了 []"
    assert row["merged_into"] == "github.com__demo__upstream", "回填把合并指向抹成了空"


def test_vibecraft_import_does_not_duplicate_repo_row(tmp_path: Path) -> None:
    """改 upsert 后，仓已经存在也不该多出一行（INSERT OR REPLACE 时代的既有行为）。"""
    paths, src_db = _setup(tmp_path)
    vibecraft.import_vibecraft(paths=paths, path=str(src_db))
    vibecraft.import_vibecraft(paths=paths, path=str(src_db))
    n = _rows(paths, "SELECT COUNT(*) FROM repos WHERE repo_id = ?",
              "github.com__demo__widget")
    assert n[0][0] == 1, "回填两次后仓行数应仍为 1，实际 " + str(n[0][0])

def _rows_count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])
