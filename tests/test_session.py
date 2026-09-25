"""会话层测试：状态机 + TTL 三步回收 + 统计归档。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config, Paths  # noqa: E402
from memex.store.db import connect, init_db  # noqa: E402
from memex.session import (  # noqa: E402
    begin_session,
    can_transition,
    find_active_session,
    is_terminal,
    session_stats_summary,
    set_state,
    sweep,
    touch,
)


def _seed_repo(conn):
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, identity_key, source) "
        "VALUES('github.com__o__r','o/r','https://github.com/o/r','github.com','github.com#1','clone')"
    )


def test_state_machine():
    assert can_transition("begun", "evidence_taken")
    assert can_transition("drafting", "validated")
    assert can_transition("validated", "committed")
    assert not can_transition("begun", "committed")
    assert not can_transition("committed", "drafting")
    assert is_terminal("committed") and is_terminal("abandoned")


def test_lifecycle_and_recycle(tmp_path: Path):
    cfg = Config(home=tmp_path, session_ttl_seconds=0, session_retention_seconds=100_000)
    paths = Paths(tmp_path)
    init_db(paths)
    conn = connect(paths.db)
    try:
        _seed_repo(conn)
        s = begin_session(conn, cfg, "github.com__o__r", "sha1", depth="standard", analyst="tester")
        assert s["state"] == "evidence_taken", s["state"]
        sid = s["session_id"]
        # 状态机推进
        set_state(conn, s, "drafting")
        s2 = touch(conn, cfg, sid, turns=2, tool_calls=3, tokens_est=100)
        assert s2["meta"]["turns"] == 2 and s2["meta"]["tokens_est"] == 100
        set_state(conn, s2, "validated")
        assert find_active_session(conn, "github.com__o__r", "sha1")["session_id"] == sid
        # 把 expires_at 钉死在过去，避免同秒边界抖动；第一步归档，第二步留痕
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00Z", sid),
        )
        res = sweep(conn, cfg)
        assert res["archived"] == 1, res
        row = conn.execute("SELECT state, abandoned_at FROM sessions WHERE session_id=?", (sid,)).fetchone()
        assert row["state"] == "abandoned" and row["abandoned_at"]
        stats = session_stats_summary(conn)
        assert stats["sessions"] == 1 and stats["turns"] == 2 and stats["tool_calls"] == 3, stats
        assert stats["by_outcome"].get("validated") == 1
        # retention=0 -> 第三步物理删除 sessions 行，但统计仍在
        res2 = sweep(conn, Config(home=tmp_path, session_ttl_seconds=0, session_retention_seconds=0))
        assert res2["deleted"] == 1, res2
        assert conn.execute("SELECT COUNT(*) n FROM sessions").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM session_stats").fetchone()["n"] == 1
    finally:
        conn.close()


def test_illegal_transition(tmp_path: Path):
    cfg = Config(home=tmp_path)
    paths = Paths(tmp_path)
    init_db(paths)
    conn = connect(paths.db)
    try:
        _seed_repo(conn)
        s = begin_session(conn, cfg, "github.com__o__r", "sha1", depth="standard", analyst="t", include_pack=False)
        assert s["state"] == "begun"
        try:
            set_state(conn, s, "committed")
            raise AssertionError("should have raised")
        except ValueError:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    import inspect

    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "tmp_path" in inspect.signature(fn).parameters:
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print("PASS", name)
            except AssertionError as e:
                print("FAIL", name, e)
