# -*- coding: utf-8 -*-
"""把知识库导成 `site-data.json`——Python 与 React 之间唯一的接口。

对照 `decisions.md` C11「改写二」与 `tech-design.md` §2.7：
**换掉的是排版那一半，不是数据来源**。本模块保留原来 `export_site()` 里的
4 条 SQL，产物从 `.html` 变成一份带 `schema_id` 的 JSON；
排版交给 `web/` 里的 React 预渲染。

三条不变量（改动前先读文档）：

- **`report_json` 原样内联**，不做前端专用整形。`report.schema.json` 已经是
  那份数据的契约，再造一层前端形状只会多一个漂移点。
- **产物文件名自守**留在这一侧（`_page_filename` 原样搬过来）：脏 `repo_id`
  跳过并记入 `skipped_repo_ids`，不就地改库也不写可疑文件。
- **Node 工具链不进 Python 包**：`pyproject.toml` 的 `dependencies = []` 保持空。

深链锚点 `f{功能下标}-c{卡片下标}` 在这里生成（`_anchor_for_card`），
与 `analyze/rows.py` 的 `card_id = f"card_{fid}_{ci}"` 是同一套下标，
所以模式成员能从 `card_id` 反解出锚点，两端不会漂。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..core import MemexError, Paths
from ..store import db

SCHEMA_ID = "memex/site/1"

# 模式页显示的聚类阈值。与 patterns/cluster.py 的 DEFAULT_SIM_THRESHOLD 同值，
# 写进 JSON 是为了让页面显示的数值有据可查，而不是让前端编一个。
_CLUSTER_THRESHOLD = 0.60


def _page_filename(repo_id: str) -> str | None:
    """把 repo_id 重写成一个安全的产物文件名。

    库里的 repo_id 没有 DDL 约束（TEXT PRIMARY KEY），本函数是最后一道护栏：
    返回 None 表示这个 repo_id 写不进文件名（调用方应跳过并计数），
    返回的字符串保证不含路径分隔符、不是 . / ..、且不超过 255 字节。

    正常仓的 repo_id 是 host__owner__name，原样返回，行为不变。
    """
    name = repo_id
    if not name or name in (".", ".."):
        return None
    # 只保留文件名里合法的字符；其余换成下划线而不是丢掉（丢掉会把 a/b 与
    # a_b 都压成 ab）。真正的防碰撞在调用方：改写结果与原值不一致的一律跳过，
    # 因此真正被写盘的 id 集合上这个映射是恒等映射，天然不碰撞。
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if cleaned in ("", ".", ".."):
        return None
    # 截断按 UTF-8 字节算（文件系统上限是 255 字节，不是 255 个字符），
    # 且回退到字符边界，别把多字节字符切成乱码。
    if len(cleaned.encode("utf-8")) <= 255:
        return cleaned
    cut = cleaned.encode("utf-8")[:255]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return None


def _anchor_for_card(card_id: str) -> str | None:
    """把 card_id 反解成页面锚点；解不出就返回 None（不编造链接）。

    analyze/rows.py 落库时用的 id 形如 card_feat_ana_xxx_0_2
    （fid = f"feat_{analysis_id}_{fi}"、cid = f"card_{fid}_{ci}"），
    所以最后两段就是「功能下标 - 卡片下标」。反过来 dump 时按位置生成
    f{fi}-c{ci}，两边用同一套下标，锚点才不会失效。

    对不上就返回 None：宁可不给深链，也不要给一个点了没反应的假链接。
    """
    parts = card_id.split("_")
    if len(parts) < 2:
        return None
    fi, ci = parts[-2], parts[-1]
    if not (fi.isdigit() and ci.isdigit()):
        return None
    return "f{}-c{}".format(fi, ci)


def _analysis_block(row: sqlite3.Row, keys: set[str]) -> dict[str, Any]:
    """一次分析 -> JSON 块。report 原样内联，report_md 只当快照。"""
    return {
        "analysis_id": row["analysis_id"] if "analysis_id" in keys else None,
        "commit_sha": row["commit_sha"],
        "contract_version": row["contract_version"],
        "depth": row["depth"] if "depth" in keys else None,
        "analyst": row["analyst"],
        "producer": row["producer"],
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "reindex_state": row["reindex_state"] if "reindex_state" in keys else None,
        "counts": db.json_loads(row["counts_json"], {}),
        "quality": db.json_loads(row["quality_json"], {}),
        "report": db.json_loads(row["report_json"], {}),
        "report_md": row["report_md"],
    }


def _load_repos(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[str]]:
    """仓库 + 它的历次分析（按 created_at 降序，最新的在前）。

    返回 (可导出的仓, 被跳过的脏 repo_id)。跳过的那批不进 repos 计数——
    首页显示的仓库数必须等于真的写出了页面的数量（tech-design §2.7.4）。
    """
    repo_rows = list(conn.execute(
        "SELECT repo_id, full_name, url, host, description, language, stars, license, "
        "is_stale, is_fork, fork_of, default_branch, source FROM repos "
        "ORDER BY full_name COLLATE NOCASE ASC"
    ).fetchall())

    # 脊柱 DDL 是否已含 reindex_state / depth 尚不确定（docs 要求、db.py 视版本而定）：
    # 读侧按实际列动态选择，列缺失时以 NULL 顶替，站点始终可用。
    an_cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(analyses)")}
    reindex_expr = "reindex_state" if "reindex_state" in an_cols else "NULL AS reindex_state"
    depth_expr = "depth" if "depth" in an_cols else "NULL AS depth"
    analysis_rows = list(conn.execute(
        "SELECT analysis_id, repo_id, commit_sha, contract_version, " + depth_expr + ", "
        "analyst, producer, status, counts_json, quality_json, created_at, finished_at, "
        "report_json, report_md, " + reindex_expr + " FROM analyses "
        "ORDER BY created_at DESC"
    ).fetchall())

    by_repo: dict[str, list[dict[str, Any]]] = {}
    for row in analysis_rows:
        by_repo.setdefault(str(row["repo_id"]), []).append(_analysis_block(row, an_cols))

    out: list[dict[str, Any]] = []
    skipped: list[str] = []
    for r in repo_rows:
        rid = str(r["repo_id"])
        fname = _page_filename(rid)
        if fname is None or fname != rid:
            # 脏 repo_id：显式跳过并记账，绝不静默产出一个可疑文件。
            skipped.append(rid)
            continue
        analyses = by_repo.get(rid, [])
        out.append({
            "repo_id": rid,
            "file": fname + ".html",
            "full_name": r["full_name"],
            "url": r["url"],
            "host": r["host"],
            "description": r["description"],
            "language": r["language"],
            "stars": r["stars"],
            "license": r["license"],
            "is_stale": bool(r["is_stale"]),
            "is_fork": bool(r["is_fork"]),
            "fork_of": r["fork_of"],
            "default_branch": r["default_branch"],
            "source": r["source"],
            "analysis_count": len(analyses),
            "latest": analyses[0] if analyses else None,
            "analyses": analyses,
        })
    return out, skipped


def _load_patterns(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """跨仓模式 + 成员卡片（带来源仓与深链锚点）。"""
    pats = list(conn.execute(
        "SELECT pattern_id, key, title, tags_json, card_count, repo_count FROM patterns "
        "ORDER BY repo_count DESC, card_count DESC, title COLLATE NOCASE ASC"
    ).fetchall())
    if not pats:
        return []
    by_id: dict[str, dict[str, Any]] = {str(p["pattern_id"]): {"members": []} for p in pats}
    for m in conn.execute(
        "SELECT pm.pattern_id AS pattern_id, pm.score AS score, c.card_id AS card_id, "
        "c.kind AS kind, c.title AS title, c.summary AS summary, c.symbol AS symbol, "
        "a.repo_id AS repo_id, r.full_name AS repo_full_name "
        "FROM pattern_members pm "
        "JOIN cards c ON c.card_id = pm.card_id "
        "JOIN features f ON f.feature_id = c.feature_id "
        "JOIN analyses a ON a.analysis_id = f.analysis_id "
        "LEFT JOIN repos r ON r.repo_id = a.repo_id "
        "ORDER BY pm.score DESC"
    ):
        target = by_id.get(str(m["pattern_id"]))
        if target is None:
            continue
        repo_id = str(m["repo_id"])
        fname = _page_filename(repo_id)
        # 脏 repo_id 指向的仓没被导出（见 _load_repos 的跳过规则），此时 file 为 None，
        # 页面就只显示仓名不给链接——不给链接比给死链诚实。
        target["members"].append({
            "card_id": m["card_id"],
            "score": m["score"],
            "kind": m["kind"],
            "title": m["title"],
            "summary": m["summary"],
            "symbol": m["symbol"],
            "repo_id": repo_id,
            "repo_full_name": m["repo_full_name"],
            "file": (fname + ".html") if fname is not None and fname == repo_id else None,
            "anchor": _anchor_for_card(str(m["card_id"])),
        })
    out: list[dict[str, Any]] = []
    for p in pats:
        pid = str(p["pattern_id"])
        out.append({
            "pattern_id": pid,
            "key": p["key"],
            "title": p["title"],
            "tags": db.json_loads(p["tags_json"], []),
            "card_count": p["card_count"],
            "repo_count": p["repo_count"],
            "members": by_id[pid]["members"],
        })
    return out


def dump_site_data(
    paths: Paths | None = None,
    *,
    out: str | Path | None = None,
) -> dict[str, Any]:
    """把库导成 site-data.json。

    这是 Python 侧站点链路的**全部**职责：查库、算锚点、写一个 JSON。
    它不碰嵌入模型，也不 import 任何 Node 侧的东西——所以挂在 commit 尾巴上
    也就 50ms 量级，而 React 构建（要 Node）留在部署侧（decisions.md C11 改写二）。

    返回 {out_dir, data_file, schema_id, repos, patterns, skipped_repo_ids}；
    repos 是真正导出的仓数，脏 repo_id 只进 skipped_repo_ids。
    out 缺省为 $MEMEX_HOME/site（Paths.site）。
    """
    if paths is None:
        paths = Paths.default()
    if not paths.db.exists():
        raise MemexError(
            "not_found",
            "知识库不存在，请先运行 memex init 建库",
            {"db": str(paths.db)},
        )
    out_dir = Path(out).expanduser() if out else paths.site
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = db.connect(str(paths.db))
    try:
        repos, skipped = _load_repos(conn)
        patterns = _load_patterns(conn)
        n_analyses = int(conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"])
        n_cards = int(conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"])
        embedder = db.get_meta(conn, "embedder")
    finally:
        conn.close()

    data: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "generated_at": db.utcnow(),
        "embedder": embedder,
        "cluster_threshold": _CLUSTER_THRESHOLD,
        "stats": {
            "repos": len(repos),
            "analyses": n_analyses,
            "cards": n_cards,
            "patterns": len(patterns),
        },
        "repos": repos,
        "patterns": patterns,
        "skipped_repo_ids": skipped,
    }
    data_file = out_dir / "site-data.json"
    data_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + chr(10),
        encoding="utf-8",
    )
    return {
        "out_dir": str(out_dir),
        "data_file": str(data_file),
        "schema_id": SCHEMA_ID,
        "repos": len(repos),
        "patterns": len(patterns),
        "skipped_repo_ids": skipped,
    }
