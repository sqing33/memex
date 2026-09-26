"""SQLite 存储层：建库、迁移、重索引、统计、硬删除。

设计要点（tech-design §2.2 / §2.3）：
- 单文件 memex.db；开 WAL、外键、busy_timeout；
- 物理表共 14 张（文档正文写作「13 张」是历史口径，meta..session_stats 全部落库）；
- 重建性分层：source（repos/analyses/features/cards/evidence，需备份）、
  derived（patterns/pattern_members/pattern_intents，可重算）、
  index（chunks/chunk_vectors/chunk_fts，可重索引）；session_stats 永不删除。

json_loads/json_dumps 是全仓共用的 JSON 边界：容错解析 + 稳定序列化。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..constants import SCHEMA_VERSION
from ..core import Config, MemexError, Paths

# 放开 check_same_thread=False 的前提：SQLite 必须以 serialized 模式编译（连接可跨线程共用）。
# 低于 3 说明只能跨线程、不能共用同一个连接，远程形态就会随机崩 —— 故在 connect() 里显式拒绝。
_MIN_THREADSAFETY = 3

# reindex 要重建索引的 analyses.status 取值（见 reindex docstring）。
# 口径：status 只表达「这份分析能不能被当成结论用」，来源由 producer/analyst 区分，
# 所以 agent 提交与 VibeCraft 回填**都写 committed**（docs/mcp-tools.md T11 枚举里没有 ready）。
# 派生表清空后若漏筛某条，它的索引就再也回不来——所以这里是全集，且有守卫函数兜底。
_REINDEXABLE_STATUSES: tuple[str, ...] = ("committed", "ready")

# ———————————————————————————————— JSON 边界 ————————————————————————————————

def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def json_loads(text: Any, default: Any = None) -> Any:
    if text is None:
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


# ———————————————————————————————— 连接 ————————————————————————————————

def connect(path: str | Path) -> sqlite3.Connection:
    """开一个连接（WAL + autocommit）。

    check_same_thread=False：远程形态用 ThreadingHTTPServer，**每请求一个线程**，
    但共用同一个 Runtime（因而共用这个连接）。默认的线程亲和性检查会让第二个
    线程一碰就 ProgrammingError（实测并发 n=4 起大量 internal，详见
    tech-design.md §4.6.1）。放开的前提是 SQLite 以 serialized 模式编译，
    即 sqlite3.threadsafety >= 3；不满足则**显式报错**，而不是等线上随机崩。
    """
    if sqlite3.threadsafety < _MIN_THREADSAFETY:
        raise MemexError(
            "internal",
            "当前 Python 的 SQLite 不是 serialized 编译（threadsafety="
            + str(sqlite3.threadsafety)
            + " < 3），连接无法跨线程共用，远程形态会随机崩溃；"
            "请换用 serialized 构建的 Python。",
            {"threadsafety": sqlite3.threadsafety, "required": _MIN_THREADSAFETY},
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(p),
        isolation_level=None,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ———————————————————————————————— DDL ————————————————————————————————

DDL_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS repos (
        repo_id        TEXT PRIMARY KEY,
        full_name      TEXT NOT NULL,
        url            TEXT NOT NULL,
        host           TEXT NOT NULL,
        default_branch TEXT,
        language       TEXT,
        stars          INTEGER,
        license        TEXT,
        description    TEXT,
        subpath        TEXT,
        identity_key   TEXT NOT NULL UNIQUE,
        aliases_json   TEXT NOT NULL DEFAULT '[]',
        fork_of        TEXT,
        is_fork        INTEGER NOT NULL DEFAULT 0,
        source         TEXT NOT NULL DEFAULT 'fetch',
        is_stale       INTEGER NOT NULL DEFAULT 0,
        head_sha       TEXT,
        cloned_at      TEXT,
        repo_path      TEXT,
        is_local       INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS analyses (
        analysis_id      TEXT PRIMARY KEY,
        repo_id          TEXT NOT NULL REFERENCES repos(repo_id) ON DELETE CASCADE,
        commit_sha       TEXT NOT NULL,
        contract_version TEXT NOT NULL,
        depth            TEXT NOT NULL,
        analyst          TEXT NOT NULL,
        producer         TEXT NOT NULL,
        status           TEXT NOT NULL,
        report_md        TEXT,
        report_json      TEXT,
        counts_json      TEXT,
        quality_json     TEXT,
        created_at       TEXT NOT NULL,
        finished_at      TEXT,
        reindex_state    TEXT NOT NULL DEFAULT 'indexed',
        UNIQUE (repo_id, commit_sha, contract_version)
    )""",
    """CREATE TABLE IF NOT EXISTS features (
        feature_id  TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
        slug        TEXT NOT NULL,
        title       TEXT NOT NULL,
        summary     TEXT,
        position    INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS cards (
        card_id         TEXT PRIMARY KEY,
        feature_id      TEXT NOT NULL REFERENCES features(feature_id) ON DELETE CASCADE,
        kind            TEXT NOT NULL,
        reusable        INTEGER NOT NULL DEFAULT 1,
        title           TEXT NOT NULL,
        summary         TEXT,
        mechanism_desc  TEXT NOT NULL,
        language        TEXT,
        symbol          TEXT,
        code_spans_json TEXT NOT NULL DEFAULT '[]',
        quality_json    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        card_id     TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
        path        TEXT NOT NULL,
        start_line  INTEGER NOT NULL,
        end_line    INTEGER NOT NULL,
        symbol      TEXT,
        file_sha    TEXT,
        excerpt     TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS patterns (
        pattern_id TEXT PRIMARY KEY,
        key        TEXT NOT NULL UNIQUE,
        title      TEXT NOT NULL,
        tags_json  TEXT NOT NULL DEFAULT '[]',
        card_count INTEGER NOT NULL DEFAULT 0,
        repo_count INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS pattern_members (
        pattern_id TEXT NOT NULL REFERENCES patterns(pattern_id) ON DELETE CASCADE,
        card_id    TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
        score      REAL NOT NULL DEFAULT 0.0,
        PRIMARY KEY (pattern_id, card_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pattern_intents (
        pattern_id TEXT NOT NULL REFERENCES patterns(pattern_id) ON DELETE CASCADE,
        text       TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        kind     TEXT NOT NULL,
        ref_id   TEXT,
        card_id  TEXT,
        text     TEXT NOT NULL,
        repo_id  TEXT,
        language TEXT,
        heading  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS chunk_vectors (
        chunk_id TEXT PRIMARY KEY,
        embedder TEXT NOT NULL,
        dim      INTEGER NOT NULL,
        vec      BLOB NOT NULL
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5 (
        chunk_id UNINDEXED,
        text,
        tokenize = 'trigram'
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id   TEXT PRIMARY KEY,
        repo_id      TEXT NOT NULL REFERENCES repos(repo_id) ON DELETE CASCADE,
        state        TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        expires_at   TEXT,
        abandoned_at TEXT,
        meta_json    TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS session_stats (
        session_id   TEXT PRIMARY KEY,
        repo_id      TEXT,
        turns        INTEGER NOT NULL DEFAULT 0,
        tool_calls   INTEGER NOT NULL DEFAULT 0,
        tokens_est   INTEGER NOT NULL DEFAULT 0,
        wall_seconds REAL NOT NULL DEFAULT 0.0,
        outcome      TEXT,
        created_at   TEXT NOT NULL
    )""",
)

INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_analyses_repo ON analyses(repo_id)",
    "CREATE INDEX IF NOT EXISTS idx_features_analysis ON features(analysis_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_feature ON cards(feature_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_reusable ON cards(reusable)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_card ON evidence(card_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_ref ON chunks(ref_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id)",
    "CREATE INDEX IF NOT EXISTS idx_pattern_members_card ON pattern_members(card_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_repo ON sessions(repo_id)",
)


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def create_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    for stmt in INDEX_STATEMENTS:
        conn.execute(stmt)
    _ensure_columns(conn)


# 列级增量迁移：为既有库补上后加的列（幂等）。值 = (表, 列, 列定义)。
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("analyses", "reindex_state", "TEXT NOT NULL DEFAULT 'indexed'"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _COLUMN_MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(paths: Paths, *, embedder_spec: str | None = None) -> dict[str, Any]:
    """初始化 memex 主库。幂等。返回 {"created":bool,"db":path,"schema_version":...}。"""
    existed = paths.db.exists()
    conn = connect(paths.db)
    try:
        create_schema(conn)
        if _meta_get(conn, "schema_version") is None:
            _meta_set(conn, "schema_version", SCHEMA_VERSION)
            _meta_set(conn, "created_at", _now())
        if _meta_get(conn, "embedder") is None and embedder_spec:
            _meta_set(conn, "embedder", embedder_spec)
        conn.execute("COMMIT") if False else None
        return {
            "created": not existed,
            "db": str(paths.db),
            "schema_version": _meta_get(conn, "schema_version"),
        }
    finally:
        conn.close()


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if not row:
        return None
    return _meta_get(conn, "schema_version")


def check_schema(conn: sqlite3.Connection) -> tuple[str, str]:
    """启动检查：返回 (db_version, code_version) 并执行 G9 规则。

    - db 更高 -> unsupported（拒绝启动，绝不静默降级）；
    - db 更低 -> 提示运行 memex migrate（此处只报告，不动库）。
    """
    dbv = schema_version(conn) or "0"
    codev = SCHEMA_VERSION
    try:
        dbn = int(dbv.split(".")[0])
        cn = int(codev.split(".")[0])
    except ValueError:
        raise MemexError("internal", "schema_version 非数字", {"db": dbv, "code": codev})
    if dbn > cn:
        raise MemexError(
            "unsupported",
            "数据库 schema 版本高于本程序，请升级 memex",
            {"db_schema_version": dbv, "code_schema_version": codev},
        )
    if dbn < cn:
        raise MemexError(
            "conflict",
            "数据库 schema 版本较低，请运行 memex migrate",
            {"db_schema_version": dbv, "code_schema_version": codev, "action": "memex migrate"},
        )
    return dbv, codev


def migrate(paths: Paths, *, to: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """迁移主库。V1 只有 schema 1，迁移是「建表 + 写版本」的幂等动作。"""
    target = to or SCHEMA_VERSION
    conn = connect(paths.db)
    try:
        dbv = schema_version(conn) or "0"
        plan = {"from": dbv, "to": target, "steps": [] if dbv == target else [f"{dbv} -> {target}"]}
        if dry_run:
            return {"dry_run": True, **plan}
        create_schema(conn)
        _meta_set(conn, "schema_version", target)
        return {"dry_run": False, **plan, "schema_version": target}
    finally:
        conn.close()


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """recall_stats 用的基础计数。"""
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "repos": count("SELECT COUNT(*) FROM repos"),
        "analyses": count("SELECT COUNT(*) FROM analyses"),
        "features": count("SELECT COUNT(*) FROM features"),
        "cards": count("SELECT COUNT(*) FROM cards"),
        "reusable_cards": count("SELECT COUNT(*) FROM cards WHERE reusable = 1"),
        "evidence": count("SELECT COUNT(*) FROM evidence"),
        "patterns": count("SELECT COUNT(*) FROM patterns"),
        "chunks": count("SELECT COUNT(*) FROM chunks"),
        "sessions": count("SELECT COUNT(*) FROM sessions"),
        "schema_version": schema_version(conn),
        "embedder": _meta_get(conn, "embedder"),
    }


def forget_analysis(conn: sqlite3.Connection, analysis_id: str, *, confirm: bool) -> dict[str, Any]:
    """硬删除一次分析（级联 features/cards/evidence）并清索引 + 重建聚类（G8）。"""
    if not confirm:
        raise MemexError("invalid_argument", "forget_analysis 需要 confirm:true", {"analysis_id": analysis_id})
    row = conn.execute("SELECT analysis_id, repo_id FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    if not row:
        raise MemexError("not_found", "分析不存在", {"analysis_id": analysis_id})
    fid_rows = conn.execute("SELECT feature_id FROM features WHERE analysis_id = ?", (analysis_id,)).fetchall()
    from .index import drop_chunks_by_ref  # 局部导入避免循环

    drop_chunks_by_ref(conn, "feature", [r["feature_id"] for r in fid_rows])
    drop_chunks_by_ref(conn, "report_section", [analysis_id])
    card_rows = conn.execute(
        "SELECT card_id FROM cards WHERE feature_id IN (SELECT feature_id FROM features WHERE analysis_id = ?)",
        (analysis_id,),
    ).fetchall()
    drop_chunks_by_ref(conn, "card", [r["card_id"] for r in card_rows])
    conn.execute("DELETE FROM analyses WHERE analysis_id = ?", (analysis_id,))
    return {"analysis_id": analysis_id, "deleted": True, "repo_id": row["repo_id"]}


def forget_repo(conn: sqlite3.Connection, repo_id: str, *, confirm: bool) -> dict[str, Any]:
    """硬删除整仓（级联 analyses/features/cards/evidence/sessions）并清索引（G8）。"""
    if not confirm:
        raise MemexError("invalid_argument", "forget_repo 需要 confirm:true", {"repo_id": repo_id})
    row = conn.execute("SELECT repo_id FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    if not row:
        raise MemexError("not_found", "仓库不存在", {"repo_id": repo_id})
    from .index import drop_chunks_by_ref

    a_rows = conn.execute("SELECT analysis_id FROM analyses WHERE repo_id = ?", (repo_id,)).fetchall()
    for a in a_rows:
        aid = a["analysis_id"]
        f_rows = conn.execute("SELECT feature_id FROM features WHERE analysis_id = ?", (aid,)).fetchall()
        for f in f_rows:
            drop_chunks_by_ref(conn, "feature", [f["feature_id"]])
            c_rows = conn.execute("SELECT card_id FROM cards WHERE feature_id = ?", (f["feature_id"],)).fetchall()
            drop_chunks_by_ref(conn, "card", [c["card_id"] for c in c_rows])
        drop_chunks_by_ref(conn, "report_section", [aid])
    conn.execute("DELETE FROM repos WHERE repo_id = ?", (repo_id,))
    return {"repo_id": repo_id, "deleted": True, "analyses_deleted": len(a_rows)}


def _assert_reindex_covers_all(conn: sqlite3.Connection) -> None:
    """守卫：库里有本常量没覆盖的 status 值时**显式报错**，而不是照常清空派生表。

    没有这层守卫的话，将来再加一条落库路径写了新 status，reindex 仍会
    静默把它的索引删光且永不重建（这正是 BUG-1 的形状）。
    """
    seen = {r["status"] for r in conn.execute("SELECT DISTINCT status FROM analyses").fetchall()}
    missing = sorted(seen - set(_REINDEXABLE_STATUSES))
    if missing:
        raise MemexError(
            "internal",
            "analyses.status 出现了 reindex 不会重建的取值 "
            + str(missing)
            + "；直接清空派生表会让这些分析的索引永久丢失。"
            "请把它们加入 _REINDEXABLE_STATUSES 或改用既有取值。",
            {"unknown_statuses": missing, "reindexable": list(_REINDEXABLE_STATUSES)},
        )


def reindex(paths: Paths, *, embedder_spec: str | None = None) -> dict[str, Any]:
    """整层重建索引：清空 chunks/vectors/fts，再按 analyses 逐条重算（R层）。

    覆盖范围是 `_REINDEXABLE_STATUSES` 的全集：agent 提交与 VibeCraft 回填
    **都写 `committed`**（来源由 producer 区分，不用 status 区分）。派生表被清空后若漏筛某条，
    它的索引就再也回不来（V5 审计 BUG-1），故选不中任何已落库分析时显式报错，
    不静默产出空索引。
    """
    from ..embeddings import get_embedder
    from .index import index_analysis

    conn = connect(paths.db)
    try:
        # 守卫必须**先于**清空：连接是 autocommit（isolation_level=None），
        # DELETE 落盘后即使后面 raise，派生表也已经空了。放错顺序等于
        # 「报了错但索引已经没了」——正是零假成功要避免的形状。
        _assert_reindex_covers_all(conn)
        emb = get_embedder(embedder_spec or _meta_get(conn, "embedder"))
        for t in ("chunk_vectors", "chunk_fts", "chunks"):
            conn.execute(f"DELETE FROM {t}")
        n_chunks = 0
        placeholders = ",".join("?" * len(_REINDEXABLE_STATUSES))
        rows = conn.execute(
            "SELECT analysis_id, report_md FROM analyses WHERE status IN (" + placeholders + ")",
            _REINDEXABLE_STATUSES,
        ).fetchall()
        for r in rows:
            res = index_analysis(conn, emb, r["analysis_id"], report_md=r["report_md"])
            set_repo = conn.execute(
                "SELECT repo_id FROM analyses WHERE analysis_id = ?", (r["analysis_id"],)
            ).fetchone()
            if set_repo:
                from .index import set_chunk_repo

                set_chunk_repo(conn, set_repo["repo_id"])
            n_chunks += res["chunks"]
        _meta_set(conn, "embedder", emb.model)
        _meta_set(conn, "dim", str(emb.dim))
        return {"analyses": len(rows), "chunks": n_chunks, "embedder": emb.model, "degraded": emb.degraded}
    finally:
        conn.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ———————————————————————— 兼容别名（供 store/__init__.py 与其它模块引用）————————————————————————
ALL_DDL = DDL_STATEMENTS
apply_schema = create_schema
utcnow = _now


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    val = _meta_get(conn, key)
    return default if val is None else val


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    _meta_set(conn, key, value)


def all_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta").fetchall()}

