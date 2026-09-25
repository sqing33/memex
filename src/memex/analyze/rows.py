"""从报告对象构造 DB 行（features / cards / evidence）。

分析 id 与子行 id 都是**确定性**的：同一 (repo, commit, contract_version) 永远得到
同一 analysis_id，便于幂等与冲突检测（G19）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..fsutil import read_lines, sha256_file


def analysis_id_for(repo_id: str, commit_sha: str, contract_version: str) -> str:
    h = hashlib.sha1(f"{repo_id}|{commit_sha}|{contract_version}".encode("utf-8")).hexdigest()
    return f"ana_{h[:16]}"


def report_fingerprint(report: dict[str, Any]) -> str:
    """报告的稳定指纹，用于「同内容 no-op / 不同内容 conflict」（B7）。"""
    blob = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _excerpt(repo_root: Path | None, subpath: str | None, path: str, start: int, end: int) -> tuple[str | None, str | None]:
    if repo_root is None:
        return None, None
    base = repo_root / subpath if subpath else repo_root
    target = base / path
    if not target.is_file():
        return None, None
    lines = read_lines(target)
    piece = "\n".join(lines[start - 1 : end])
    return piece, sha256_file(target)


def build_rows(
    report: dict[str, Any],
    *,
    analysis_id: str,
    repo_id: str,
    commit_sha: str,
    contract_version: str,
    repo_root: Path | None,
    subpath: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """把报告摊平成可插入的行集合。调用前必须已通过校验（code_mismatch=0）。"""
    features: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    for fi, feat in enumerate(report.get("features", [])):
        slug = feat.get("key")
        fid = f"feat_{analysis_id}_{fi}"
        features.append({
            "feature_id": fid,
            "analysis_id": analysis_id,
            "slug": slug,
            "title": feat.get("title"),
            "summary": feat.get("summary"),
            "position": fi,
        })
        for ci, card in enumerate(feat.get("cards", [])):
            cid = f"card_{fid}_{ci}"
            cards.append({
                "card_id": cid,
                "feature_id": fid,
                "kind": card.get("kind"),
                "reusable": 1 if card.get("reusable") else 0,
                "title": card.get("title"),
                "summary": card.get("summary"),
                "mechanism_desc": card.get("mechanism_desc"),
                "language": card.get("language"),
                "symbol": card.get("symbol"),
                "code_spans_json": json.dumps(card.get("code_spans") or [], ensure_ascii=False),
                "quality_json": None,
            })
            for ei, ev in enumerate(card.get("evidence", [])):
                excerpt, file_sha = _excerpt(repo_root, subpath, ev["path"], ev["start_line"], ev["end_line"])
                evidence.append({
                    "evidence_id": f"ev_{cid}_{ei}",
                    "card_id": cid,
                    "path": ev["path"],
                    "start_line": ev["start_line"],
                    "end_line": ev["end_line"],
                    "symbol": ev.get("symbol"),
                    "file_sha": file_sha,
                    "excerpt": excerpt,
                })

    return {"features": features, "cards": cards, "evidence": evidence}
