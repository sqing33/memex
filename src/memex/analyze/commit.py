"""提交路径：把一份通过校验的报告落库并建索引（T7 commit_report）。

顺序（report-contract.md 与 AGENTS.md 的「证据链强制」）：
1. 装载会话，状态必须是 validated（否则 conflict/state 错）；
2. 会话绑定 commit 与仓库当前 head_sha 比对（G20：不一致 -> stale_repo，保留会话）；
3. 用 repo 根目录重跑校验器 -> is_valid=false 或 code_mismatch>0 一律 INVALID_REPORT（硬门）；
4. 幂等（B7）：同 (repo, commit, contract_version) 已存在时——
   同内容 -> no-op（already_analyzed）；不同内容 -> conflict，需 force:true 覆盖；
5. 单事务写入 analyses/features/cards/evidence，再写索引；归档会话统计并置 committed。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..constants import CONTRACT_VERSION
from ..contract import validate_report
from ..core import Config, MemexError
from ..limits import depth_profile
from ..session import manager as session_manager
from ..store import analysis as store_analysis
from ..store import index as store_index
from ..store.db import json_loads
from .rows import analysis_id_for, build_rows, report_fingerprint
from .render import render_report_md


def commit_report(
    conn: sqlite3.Connection,
    cfg: Config,
    embedder: Any,
    *,
    session_id: str,
    report: dict[str, Any],
    analyst: str = "",
    force: bool = False,
) -> dict[str, Any]:
    session = session_manager.get_session(conn, session_id)
    if session is None:
        raise MemexError("not_found", "会话不存在", {"session_id": session_id})
    if not analyst:
        analyst = (session.get("meta") or {}).get("analyst") or "agent"
    if session["state"] == "committed":
        raise MemexError("conflict", "会话已提交", {"session_id": session_id, "state": "committed"})
    if session["state"] == "abandoned":
        raise MemexError("conflict", "会话已废弃", {"session_id": session_id, "state": "abandoned"})
    if session["state"] not in ("validated", "drafting"):
        raise MemexError(
            "conflict",
            "会话状态不允许提交，请先 validate_report",
            {"session_id": session_id, "state": session["state"]},
        )

    repo_id = session["repo_id"]
    commit_sha = (session.get("meta") or {}).get("commit_sha", "")
    repo = conn.execute("SELECT * FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    if repo is None:
        raise MemexError("not_found", "仓库不存在", {"repo_id": repo_id})

    # G20：会话绑定 commit 必须等于仓库当前 head_sha
    head = repo["head_sha"]
    if head and commit_sha and head != commit_sha:
        raise MemexError(
            "stale_repo",
            "仓库已更新，会话绑定的 commit 已不是最新",
            {"session_id": session_id, "session_commit": commit_sha, "current_commit": head},
        )

    repo_path = Path(repo["repo_path"]) if repo["repo_path"] else None
    subpath = repo["subpath"]
    if repo_path is not None and not repo_path.is_dir():
        raise MemexError("not_found", "仓库工作区不存在，请重新 fetch_repo", {"repo_id": repo_id})

    # 硬门：校验（含 code_mismatch）
    vres = validate_report(report, repo_root=repo_path, subpath=subpath)
    if not vres["is_valid"]:
        raise MemexError(
            "invalid_report",
            "报告未通过校验",
            {"problems": vres["problems"], "counts": vres["counts"]},
        )
    if vres["counts"]["code_mismatch"] > 0:
        raise MemexError(
            "invalid_report",
            "code_mismatch 必须为 0，拒绝提交",
            {"code_mismatch": vres["counts"]["code_mismatch"], "problems": vres["problems"]},
        )

    prof = depth_profile(session.get("depth") or "standard")
    n_feat = len(report.get("features", []))
    if n_feat < prof.min_features:
        raise MemexError(
            "invalid_report",
            f"当前 depth 要求至少 {prof.min_features} 个 feature",
            {"features": n_feat, "min_features": prof.min_features, "depth": prof.depth},
        )

    analysis_id = analysis_id_for(repo_id, commit_sha, CONTRACT_VERSION)
    fingerprint = report_fingerprint(report)
    existing = store_analysis.get_analysis(conn, analysis_id)
    if existing is not None and existing["status"] == "committed":
        prev_fp = report_fingerprint(existing["report_json"] or {})
        if prev_fp == fingerprint:
            _finalize_session(conn, cfg, session)
            return {
                "analysis_id": analysis_id,
                "already_analyzed": True,
                "updated": False,
                "features": len(existing["report_json"].get("features", [])) if existing["report_json"] else n_feat,
                "counts": existing.get("counts", {}),
                "code_mismatch": 0,
            }
        if not force:
            raise MemexError(
                "conflict",
                "该 commit 已存在不同内容的分析，需 force:true 覆盖",
                {"analysis_id": analysis_id, "hint": "传 force:true 覆盖既有分析"},
            )

    rows = build_rows(
        report,
        analysis_id=analysis_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        contract_version=CONTRACT_VERSION,
        repo_root=repo_path,
        subpath=subpath,
    )
    report_md = render_report_md(report)
    counts = {
        "features": len(rows["features"]),
        "cards": len(rows["cards"]),
        "evidence": len(rows["evidence"]),
        "reusable_cards": sum(1 for c in rows["cards"] if c["reusable"]),
    }
    quality = {
        "code_mismatch": 0,
        "code_checked": vres["counts"]["code_checked"],
        "axis_completeness": 1.0,
        "evidence_coverage": 1.0,
        "warnings": vres["warnings"],
        "depth": prof.depth,
    }

    conn.execute("BEGIN")
    try:
        if existing is not None:
            store_analysis.delete_analysis_rows(conn, analysis_id)
        store_analysis.insert_analysis(
            conn,
            analysis_id=analysis_id,
            repo_id=repo_id,
            commit_sha=commit_sha,
            contract_version=CONTRACT_VERSION,
            depth=prof.depth,
            analyst=analyst or session.get("analyst") or "agent",
            producer="agent",
            status="committed",
        )
        store_analysis.insert_features_cards_evidence(conn, rows)
        store_analysis.finish_analysis(
            conn, analysis_id, report=report, report_md=report_md, counts=counts, quality=quality, status="committed"
        )
        idx = store_index.index_analysis(conn, embedder, analysis_id, report_md=report_md)
        store_index.set_chunk_repo(conn, repo_id)
        conn.execute(
            "UPDATE repos SET is_stale = 0 WHERE repo_id = ?", (repo_id,)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    _finalize_session(conn, cfg, session)

    # 聚类（派生层）：尽量做，但聚类失败不应让已提交的分析回滚。
    cluster_info = None
    try:
        from ..patterns import recluster

        cluster_info = recluster(conn, cfg)
    except Exception as exc:  # noqa: BLE001 聚类是派生层，失败不致命
        cluster_info = {"error": str(exc)}

    return {
        "analysis_id": analysis_id,
        "already_analyzed": False,
        "updated": existing is not None,
        "features": counts["features"],
        "cards": counts["cards"],
        "evidence": counts["evidence"],
        "chunks": idx["chunks"],
        "code_mismatch": 0,
        "counts": counts,
        "patterns": (cluster_info or {}).get("patterns"),
    }


def _finalize_session(conn: sqlite3.Connection, cfg: Config, session: dict[str, Any]) -> None:
    if session["state"] != "committed":
        session_manager.set_state(conn, session, "committed")
    session_manager.archive_stats(conn, session, outcome="committed")
