"""analyses / features / cards / evidence 的读写。

这四张表属于**源层（source）**——它们需要备份、不可从别处重建。
提交时在同一事务内写入，保证不会留下半条分析。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .db import json_dumps, json_loads


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def analysis_status(conn: sqlite3.Connection, analysis_id: str) -> str | None:
    row = conn.execute("SELECT status FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    return row["status"] if row else None


def find_analysis(conn: sqlite3.Connection, repo_id: str, commit_sha: str, contract_version: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analyses WHERE repo_id = ? AND commit_sha = ? AND contract_version = ?",
        (repo_id, commit_sha, contract_version),
    ).fetchone()
    return _row_to_analysis(row) if row else None


def get_analysis(conn: sqlite3.Connection, analysis_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    return _row_to_analysis(row) if row else None


def latest_committed(conn: sqlite3.Connection, repo_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analyses WHERE repo_id = ? AND status = 'committed' "
        "ORDER BY finished_at DESC, created_at DESC LIMIT 1",
        (repo_id,),
    ).fetchone()
    return _row_to_analysis(row) if row else None


def get_report(conn: sqlite3.Connection, *, repo_id: str | None, commit_sha: str | None) -> dict[str, Any] | None:
    """取已提交报告（get_report 工具）。commit_sha 省略则取该仓最新。"""
    if repo_id is None:
        return None
    if commit_sha:
        row = conn.execute(
            "SELECT * FROM analyses WHERE repo_id = ? AND commit_sha = ? AND status = 'committed' LIMIT 1",
            (repo_id, commit_sha),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM analyses WHERE repo_id = ? AND status = 'committed' "
            "ORDER BY finished_at DESC, created_at DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
    return _row_to_analysis(row) if row else None


def _row_to_analysis(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report_json"] = json_loads(d.get("report_json"), None)
    d["counts"] = json_loads(d.get("counts_json"), {})
    d["quality"] = json_loads(d.get("quality_json"), {})
    return d


def insert_analysis(
    conn: sqlite3.Connection,
    *,
    analysis_id: str,
    repo_id: str,
    commit_sha: str,
    contract_version: str,
    depth: str,
    analyst: str,
    producer: str,
    status: str = "committed",
) -> None:
    conn.execute(
        "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, analyst, producer, status, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (analysis_id, repo_id, commit_sha, contract_version, depth, analyst, producer, status, _now()),
    )


def delete_analysis_rows(conn: sqlite3.Connection, analysis_id: str) -> None:
    """删除一次分析的全部源行（幂等重建/force 覆盖时用）。"""
    card_rows = conn.execute(
        "SELECT card_id FROM cards WHERE feature_id IN (SELECT feature_id FROM features WHERE analysis_id = ?)",
        (analysis_id,),
    ).fetchall()
    for r in card_rows:
        conn.execute("DELETE FROM evidence WHERE card_id = ?", (r["card_id"],))
    conn.execute(
        "DELETE FROM cards WHERE feature_id IN (SELECT feature_id FROM features WHERE analysis_id = ?)",
        (analysis_id,),
    )
    conn.execute("DELETE FROM features WHERE analysis_id = ?", (analysis_id,))
    conn.execute("DELETE FROM analyses WHERE analysis_id = ?", (analysis_id,))


def finish_analysis(
    conn: sqlite3.Connection,
    analysis_id: str,
    *,
    report: dict[str, Any],
    report_md: str,
    counts: dict[str, Any],
    quality: dict[str, Any],
    status: str = "committed",
) -> None:
    conn.execute(
        "UPDATE analyses SET status = ?, report_md = ?, report_json = ?, counts_json = ?, quality_json = ?, finished_at = ? "
        "WHERE analysis_id = ?",
        (status, report_md, json_dumps(report), json_dumps(counts), json_dumps(quality), _now(), analysis_id),
    )


def insert_features_cards_evidence(
    conn: sqlite3.Connection,
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    for f in rows["features"]:
        conn.execute(
            "INSERT INTO features(feature_id, analysis_id, slug, title, summary, position) VALUES(?,?,?,?,?,?)",
            (f["feature_id"], f["analysis_id"], f["slug"], f["title"], f["summary"], f["position"]),
        )
    for c in rows["cards"]:
        conn.execute(
            "INSERT INTO cards(card_id, feature_id, kind, reusable, title, summary, mechanism_desc, language, symbol, code_spans_json, quality_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (c["card_id"], c["feature_id"], c["kind"], c["reusable"], c["title"], c["summary"],
             c["mechanism_desc"], c["language"], c["symbol"], c["code_spans_json"], c["quality_json"]),
        )
    for e in rows["evidence"]:
        conn.execute(
            "INSERT INTO evidence(evidence_id, card_id, path, start_line, end_line, symbol, file_sha, excerpt) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (e["evidence_id"], e["card_id"], e["path"], e["start_line"], e["end_line"],
             e["symbol"], e["file_sha"], e["excerpt"]),
        )
    return {"features": len(rows["features"]), "cards": len(rows["cards"]), "evidence": len(rows["evidence"])}


def count_features(conn: sqlite3.Connection, analysis_id: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM features WHERE analysis_id = ?", (analysis_id,)).fetchone()[0])
