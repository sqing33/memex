"""会话管理：开会话、状态迁移、TTL 惰性清扫、统计归档（G18）。

会话存 SQLite（不是进程内存），保证 server 重启后 agent 能续上
（docs/tech-design.md §2.6）。

回收语义（三步，顺序不可换）：
1. 归档统计 -> session_stats（turns / tool_calls / tokens_est / wall_seconds / outcome）；
2. 留痕     -> 置 state='abandoned' + abandoned_at，行保留；
3. 物理删除 -> 过 MEMEX_SESSION_RETENTION_SECONDS 后删 sessions 行。

session_stats 永不删除——它是 V1 判「agent 驱动可不可行」的唯一依据。
"""

from __future__ import annotations

import calendar
import sqlite3
import time
import uuid
from typing import Any

from ..constants import CONTRACT_ID
from ..core import Config
from ..store.db import json_dumps, json_loads, utcnow
from .state import can_transition, is_terminal, next_state_after_begin

MAX_META_BYTES = 60_000


def new_session_id() -> str:
    """sess_<16 hex> —— 与 MCP 运输层 Mcp-Session-Id 无关（§4.3）。"""
    return "sess_" + uuid.uuid4().hex[:16]


def _utc_in(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def _epoch(rfc3339: str) -> float:
    try:
        return float(calendar.timegm(time.strptime(rfc3339, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    d["meta"] = json_loads(d.pop("meta_json", None), {}) or {}
    return d


def begin_session(
    conn: sqlite3.Connection,
    cfg: Config,
    repo_id: str,
    commit_sha: str,
    *,
    depth: str,
    analyst: str,
    include_pack: bool = True,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """新建一个会话并落库，返回会话字典（含生成的 session_id）。"""
    sid = new_session_id()
    created = utcnow()
    payload: dict[str, Any] = {
        "contract_id": CONTRACT_ID,
        "depth": depth,
        "commit_sha": commit_sha,
        "include_pack": include_pack,
        "analyst": analyst,
        "turns": 0,
        "tool_calls": 0,
        "tokens_est": 0,
    }
    if meta:
        payload.update(meta)
    state = next_state_after_begin(include_pack)
    conn.execute(
        "INSERT INTO sessions(session_id, repo_id, state, created_at, expires_at, meta_json) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (sid, repo_id, state, created, _utc_in(cfg.session_ttl_seconds), json_dumps(payload)),
    )
    return load_session(conn, sid)


def load_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise KeyError(session_id)
    return _row_to_dict(row)


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def find_active_session(conn: sqlite3.Connection, repo_id: str, commit_sha: str) -> dict[str, Any] | None:
    """找同一 (repo_id, commit_sha) 上尚未终结的会话，用于 G18 的 resumed 语义。"""
    row = conn.execute(
        "SELECT * FROM sessions WHERE repo_id = ? AND state NOT IN ('committed','abandoned') "
        "ORDER BY created_at DESC LIMIT 1",
        (repo_id,),
    ).fetchone()
    if row is None:
        return None
    session = _row_to_dict(row)
    if (session.get("meta") or {}).get("commit_sha") != commit_sha:
        return None
    return session


def set_state(conn: sqlite3.Connection, session: dict[str, Any], new_state: str) -> dict[str, Any]:
    """按状态机迁移，非法迁移抛 ValueError（调用方转 conflict）。"""
    old = session["state"]
    if not can_transition(old, new_state):
        raise ValueError(f"非法会话迁移：{old} -> {new_state}")
    conn.execute("UPDATE sessions SET state = ? WHERE session_id = ?", (new_state, session["session_id"]))
    session["state"] = new_state
    return session


def touch(
    conn: sqlite3.Connection,
    cfg: Config,
    session_id: str,
    *,
    turns: int = 0,
    tool_calls: int = 1,
    tokens_est: int = 0,
    refresh_ttl: bool = True,
) -> dict[str, Any] | None:
    """累计会话过程统计；可选滑动续期（跑很久的会话不该在分析中途过期）。"""
    session = get_session(conn, session_id)
    if session is None:
        return None
    if is_terminal(session["state"]):
        return session
    meta = session.get("meta") or {}
    meta["turns"] = int(meta.get("turns", 0)) + turns
    meta["tool_calls"] = int(meta.get("tool_calls", 0)) + tool_calls
    meta["tokens_est"] = int(meta.get("tokens_est", 0)) + tokens_est
    conn.execute(
        "UPDATE sessions SET meta_json = ? WHERE session_id = ?", (json_dumps(meta), session_id)
    )
    if refresh_ttl:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE session_id = ?",
            (_utc_in(cfg.session_ttl_seconds), session_id),
        )
    return get_session(conn, session_id)


def archive_stats(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    outcome: str,
) -> None:
    """把会话过程统计写入 session_stats（永不删除的表）。幂等 upsert。"""
    meta = session.get("meta") or {}
    created = session.get("created_at") or utcnow()
    wall = max(0, int(_epoch(utcnow()) - _epoch(created)))
    conn.execute(
        "INSERT INTO session_stats(session_id, repo_id, turns, tool_calls, tokens_est, wall_seconds, "
        "outcome, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET turns = excluded.turns, tool_calls = excluded.tool_calls, "
        "tokens_est = excluded.tokens_est, wall_seconds = excluded.wall_seconds, "
        "outcome = excluded.outcome",
        (
            session["session_id"],
            session.get("repo_id"),
            int(meta.get("turns", 0)),
            int(meta.get("tool_calls", 0)),
            int(meta.get("tokens_est", 0)),
            wall,
            outcome,
            created,
        ),
    )


def finish(conn: sqlite3.Connection, session: dict[str, Any], *, outcome: str) -> None:
    """终结会话：先归档统计，再置终态由调用方负责。"""
    archive_stats(conn, session, outcome=outcome)


def sweep(conn: sqlite3.Connection, cfg: Config) -> dict[str, int]:
    """惰性清扫：TTL 到期 -> 归档 + 置 abandoned；过保留期 -> 物理删会话行。

    返回 {archived, deleted}。可在任意工具调用前顺带执行；serve-http 另有 60s 后台线程。
    """
    now = utcnow()
    archived = 0
    deleted = 0
    rows = conn.execute(
        "SELECT * FROM sessions WHERE state != 'abandoned' AND expires_at <= ?", (now,)
    ).fetchall()
    for row in rows:
        session = _row_to_dict(row)
        if session["state"] == "committed":
            continue
        archive_stats(conn, session, outcome=session["state"])
        conn.execute(
            "UPDATE sessions SET state = 'abandoned', abandoned_at = ? WHERE session_id = ?",
            (now, session["session_id"]),
        )
        archived += 1
    retention_cutoff = _utc_in(-cfg.session_retention_seconds)
    gone = conn.execute(
        "SELECT session_id FROM sessions WHERE state = 'abandoned' "
        "AND COALESCE(abandoned_at, expires_at) <= ?",
        (retention_cutoff,),
    ).fetchall()
    for row in gone:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (row["session_id"],))
        deleted += 1
    return {"archived": archived, "deleted": deleted}


def session_stats_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """给 recall_stats 用的过程统计摘要（agent 驱动可不可行的观测口）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(turns),0) AS turns, "
        "COALESCE(SUM(tool_calls),0) AS tool_calls, COALESCE(SUM(tokens_est),0) AS tokens_est "
        "FROM session_stats"
    ).fetchone()
    by_outcome = {
        r["outcome"]: r["n"]
        for r in conn.execute("SELECT outcome, COUNT(*) AS n FROM session_stats GROUP BY outcome")
    }
    return {
        "sessions": int(row["n"]),
        "turns": int(row["turns"]),
        "tool_calls": int(row["tool_calls"]),
        "tokens_est": int(row["tokens_est"]),
        "by_outcome": by_outcome,
    }
