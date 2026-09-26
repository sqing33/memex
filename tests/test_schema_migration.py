"""G9 补记：既有库升级后，代码要用到的列必须真的补上——而且要在启动时就说清楚。

背景（真库实测踩到的）：SCHEMA_VERSION 长期停在 "1"，而 _COLUMN_MIGRATIONS
陆续加了 analyses.reindex_state / repos.merged_into / chunks.producer 三列。
建表路径（init_db / memex migrate）才会补列，**既有库直接升级代码没人跑它**，
于是新代码读 chunks.producer 就是裸 sqlite3.OperationalError，被兜成 internal，
agent 只会重试；repos.merged_into 则是恒 None——静默拿到一半成果。

版本号对得上**不代表列齐全**：版本号是人手工维护的，列是代码事实。
所以这里既有"缺列要报错"，也要有"别只靠版本号"这条防线。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.constants import SCHEMA_VERSION  # noqa: E402
from memex.core import Config, MemexError  # noqa: E402
from memex.store import db as store_db  # noqa: E402

NEW_COLUMNS = (
    ("analyses", "reindex_state"),
    ("repos", "merged_into"),
    ("chunks", "producer"),
)


def _setup(tmp_path: Path) -> Config:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _age_to_v1(conn: sqlite3.Connection) -> None:
    """把库退回到"升级前"：删掉后加的列、把版本号按回 1。

    这是本文件的关键夹具——**真库就是这么变成不能用的**。
    """
    for table, column in NEW_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()


def test_列迁移清单里的列在新建库里齐了(tmp_path: Path) -> None:
    """新建库（memex init）必须一次到位：init_db 走的就是 create_schema。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        for table, column in NEW_COLUMNS:
            assert column in _cols(conn, table), f"{table}.{column} 新建库里就该有"
    finally:
        conn.close()


def test_老库升级后补上列而不是崩(tmp_path: Path) -> None:
    """**这就是真库踩到的那个崩**：老库缺列时 migrate 必须把列补上。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _age_to_v1(conn)
    finally:
        conn.close()
    assert "producer" not in _cols(store_db.connect(cfg.paths.db), "chunks")

    res = store_db.migrate(cfg.paths)
    assert res["schema_version"] == SCHEMA_VERSION
    conn = store_db.connect(cfg.paths.db)
    try:
        for table, column in NEW_COLUMNS:
            assert column in _cols(conn, table), f"{table}.{column} 没被补上"
    finally:
        conn.close()


def test_migrate_如实报告改了哪些列(tmp_path: Path) -> None:
    """零假成功：applied_columns 不能谎报空——那次谎报正是我自己补丁的 bug。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _age_to_v1(conn)
    finally:
        conn.close()

    res = store_db.migrate(cfg.paths)
    assert "chunks.producer" in res["applied_columns"], (
        f"迁移实际补了列，却报告成 {res['applied_columns']}"
    )


def test_migrate_幂等_第二次不动任何列(tmp_path: Path) -> None:
    cfg = _setup(tmp_path)
    store_db.migrate(cfg.paths)
    res = store_db.migrate(cfg.paths)
    assert res["applied_columns"] == [], f"第二次迁移不该再加列：{res['applied_columns']}"


def test_dry_run_打印真正会执行的步骤(tmp_path: Path) -> None:
    """旧行为是 steps 恒为空——用户看计划等于没看。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _age_to_v1(conn)
    finally:
        conn.close()

    res = store_db.migrate(cfg.paths, dry_run=True)
    assert res["dry_run"] is True
    assert res["steps"], "老库升级时 dry-run 必须列出迁移步骤"
    conn = store_db.connect(cfg.paths.db)
    try:
        assert "producer" not in _cols(conn, "chunks"), "dry-run 不该动库"
    finally:
        conn.close()


def test_启动检查自动迁移_不需要手动跑_migrate(tmp_path: Path) -> None:
    """G9 原文：「更低 -> 若存在 migrate_<from>_<to>() 则自动迁移」。

    这条在代码里从来没有过（注册表是这次才建的），文档承诺了两年是空的。
    """
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        _age_to_v1(conn)
        dbv, codev = store_db.check_schema(conn)
        assert (dbv, codev) == (SCHEMA_VERSION, SCHEMA_VERSION)
        for table, column in NEW_COLUMNS:
            assert column in _cols(conn, table), f"启动检查没自动补 {table}.{column}"
    finally:
        conn.close()


def test_缺列时启动检查给可操作提示而不是裸异常(tmp_path: Path) -> None:
    """版本号对得上但列缺了——这正是原缺陷的形态，必须报 conflict + 指路。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        conn.execute("ALTER TABLE chunks DROP COLUMN producer")
        conn.commit()
        try:
            store_db.assert_expected_columns(conn)
        except MemexError as exc:
            assert exc.code == "conflict"
            assert "chunks.producer" in str(exc.details)
            assert exc.details["action"] == "memex migrate"
        else:
            raise AssertionError("缺列必须显式报错，不能静默放行")
    finally:
        conn.close()


def test_库比代码新仍然拒绝启动(tmp_path: Path) -> None:
    """G9 的另一半：拒绝静默降级读，不能因为这次加了自动迁移就放掉。"""
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.commit()
        try:
            store_db.check_schema(conn)
        except MemexError as exc:
            assert exc.code == "unsupported"
        else:
            raise AssertionError("库比代码新必须拒绝启动")
    finally:
        conn.close()


def test_新库直接_migrate_不用先_init(tmp_path: Path) -> None:
    """回归：migrate 曾在未初始化的库上报「没有 0->1 的迁移函数」——我自己引入的。"""
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    cfg = Config.from_env()
    res = store_db.migrate(cfg.paths)
    assert res["schema_version"] == SCHEMA_VERSION
    conn = store_db.connect(cfg.paths.db)
    try:
        for table, column in NEW_COLUMNS:
            assert column in _cols(conn, table)
    finally:
        conn.close()


def test_版本号是整数才能比大小(tmp_path: Path) -> None:
    cfg = _setup(tmp_path)
    conn = store_db.connect(cfg.paths.db)
    try:
        conn.execute("UPDATE meta SET value = 'not-a-number' WHERE key = 'schema_version'")
        conn.commit()
        try:
            store_db.check_schema(conn)
        except MemexError as exc:
            assert exc.code == "internal"
        else:
            raise AssertionError("版本号非数字必须报错，不能当成 0 或 1")
    finally:
        conn.close()
