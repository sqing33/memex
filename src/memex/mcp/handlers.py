"""memex MCP 工具处理器（17 个）。

每个 handler 的输入是已解析的 JSON 参数 dict 与一个运行时句柄 rt（见 Runtime）。
handler 只负责三件事：

  1. 调用方输入校验（caller misuse -> MemexError，最终走 invalid_argument 等业务错误）；
  2. 组织业务逻辑（业务失败一律返回 {ok:false,error:{code,message,details}}，不抛协议错误）；
  3. 把底层模块的返回值整理成 docs/mcp-tools.md 规定的 payload。

约定（docs/mcp-tools.md §1）：

  - 列表统一 {count, items, total?, next_cursor?}（用 mcp.envelope.page 生成）；
  - next_step 在流程结束后省略（不是 null）；
  - 业务失败走 {ok:false,error}，MCP 层 isError 保持 false；
  - 协议层错误（未知工具 / 参数结构非法）才用 ProtocolError。
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
from typing import Any, Callable, TypeVar, cast

from .. import constants, core, store
from ..analyze import commit_report
from ..contract import validate_report
from ..contract import contract as contract_mod
from ..core import Config, MemexError, count_units, is_repo_id
from ..embeddings import Embedder, get_embedder
from ..evidence import build_pack, read_slice
from ..fetch import (
    GitError,
    fetch_repo,
    get_repo,
    repo_summary,
    request_repo_bundle,
    upload_repo_bundle,
)
from ..limits import DEFAULT_DEPTH, depth_profile
from ..patterns import recluster
from ..session import manager as session_manager
from ..session import state as session_state
from ..store import analysis as analysis_store
from ..store import db as store_db
from ..store import search as search_store
from . import envelope


# --------------------------------------------------------------------------- #
# 协议层错误（仅用于调用方误用）
# --------------------------------------------------------------------------- #
class ProtocolError(Exception):
    """协议层错误：未知工具 / 结构非法。MCP 层映射为 JSON-RPC -32602。"""

    def __init__(self, message: str, code: int = -32602):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# 运行时句柄
# --------------------------------------------------------------------------- #
class Runtime:
    """一次进程/连接共享的运行时句柄。

    延迟打开数据库连接与向量模型：只有真正用到时才建立，避免 help 之类的只读调用
    也被迫加载 470MB 模型。
    """

    def __init__(self, cfg: Config, *, client_name: str | None = None):
        self.cfg = cfg
        self.client_name = client_name
        self._conn: sqlite3.Connection | None = None
        self._embedder: Embedder | None = None
        self._embedder_error: MemexError | None = None
        self._warm_started = False

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = store.connect(self.cfg.paths.db)
        return self._conn

    def _resolve_embedder_spec(self) -> str | None:
        """解析当前应使用的嵌入器规格（配置优先，其次库 meta）。

        用**独立短连接**读 meta：绝不碰 self._conn，避免把共享连接绑定到
        预热线程（SQLite 连接有线程亲和性）。
        """
        if self.cfg.embedder:
            return self.cfg.embedder
        conn = store.connect(self.cfg.paths.db)
        try:
            return store.get_meta(conn, "embedder")
        finally:
            conn.close()

    def embedder(self) -> Embedder:
        """取嵌入器句柄；真模型首次使用时加载（get_embedder 进程内缓存）。

        若后台预热已失败，直接抛出预热时记录的错误：既不每次调用都重试加载，
        也不静默降级（守 D1/G22）。
        """
        if self._embedder is not None:
            return self._embedder
        if self._embedder_error is not None:
            raise self._embedder_error
        spec = self._resolve_embedder_spec()
        try:
            self._embedder = get_embedder(spec)
        except MemexError as exc:
            self._embedder_error = exc
            raise
        return self._embedder

    def warm(self) -> None:
        """后台线程预热真模型（不阻塞 initialize / tools/list）。

        失败时把错误记录到 _embedder_error，由首次 embedder() 显式抛出。
        """
        if self._warm_started:
            return
        self._warm_started = True

        def _run() -> None:
            try:
                self.embedder()
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, MemexError):
                    self._embedder_error = MemexError(
                        "internal", "加载嵌入模型失败", {"reason": str(exc)}
                    )

        threading.Thread(target=_run, name="memex-embedder-warm", daemon=True).start()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - 连接可能绑定在其他线程
                pass
            self._conn = None


# --------------------------------------------------------------------------- #
# 注册表 + 派发
# --------------------------------------------------------------------------- #
_HANDLERS: dict[str, Callable[[Runtime, dict[str, Any]], dict[str, Any]]] = {}


F = TypeVar("F", bound=Callable[..., Any])


def handler(name: str) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _HANDLERS[name] = cast("Callable[[Runtime, dict[str, Any]], dict[str, Any]]", fn)
        return fn

    return deco


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    """把业务 payload 包成 MCP tool result。

    §1.13：structuredContent 给机器；content 只放一行人类摘要。
    业务失败也是正常返回（isError 保持 false）。
    """
    if payload.get("ok") is False:
        err = payload.get("error") or {}
        text = "错误 " + str(err.get("code")) + "：" + str(err.get("message"))
    else:
        text = "ok"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }


def dispatch(rt: Runtime, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """执行一次工具调用，返回 MCP tool result。"""
    from .schemas import tool_category

    category = tool_category(name)
    if category is None:
        raise ProtocolError("未知工具：" + name)
    if not rt.cfg.tool_enabled(category):
        return _result(
            envelope.error(
                "disabled",
                "工具 " + name + " 所属类别 " + category + " 未启用",
                {"tool": name, "category": category, "enabled": list(rt.cfg.tools)},
            )
        )
    fn = _HANDLERS.get(name)
    if fn is None:
        raise ProtocolError("工具未实现：" + name)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError("arguments 必须是对象")
    try:
        payload = fn(rt, args)
    except MemexError as exc:
        payload = envelope.error(exc.code, exc.message, exc.details or None)
    except ProtocolError:
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底为 internal，绝不泄漏栈
        payload = envelope.from_exception(exc)
    return _result(payload)


# --------------------------------------------------------------------------- #
# 参数校验小工具
# --------------------------------------------------------------------------- #
def _req_str(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise MemexError("invalid_argument", "缺少必填参数 " + key, {"param": key})
    return v


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    v = args.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise MemexError("invalid_argument", "参数 " + key + " 必须是字符串", {"param": key})
    return v


def _opt_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    v = args.get(key, default)
    if not isinstance(v, bool):
        raise MemexError("invalid_argument", "参数 " + key + " 必须是布尔值", {"param": key})
    return v


def _opt_int(
    args: dict[str, Any],
    key: str,
    default: int | None = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    v = args.get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise MemexError("invalid_argument", "参数 " + key + " 必须是整数", {"param": key})
    if minimum is not None and v < minimum:
        raise MemexError("invalid_argument", "参数 " + key + " 不能小于 " + str(minimum), {"param": key})
    if maximum is not None and v > maximum:
        raise MemexError("invalid_argument", "参数 " + key + " 不能大于 " + str(maximum), {"param": key})
    return v


def _repo_id_arg(args: dict[str, Any], key: str = "repo_id") -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise MemexError("invalid_argument", "缺少必填参数 " + key, {"param": key})
    if not is_repo_id(v):
        raise MemexError("invalid_argument", key + " 格式非法：" + v, {"param": key})
    return v


def _check_depth(depth: str) -> None:
    try:
        depth_profile(depth)
    except KeyError:
        raise MemexError(
            "invalid_argument", "depth 必须是 fast|standard|deep", {"param": "depth"}
        ) from None


def _next(action: str, args: dict[str, Any] | None = None, hint: str = "") -> dict[str, Any]:
    step: dict[str, Any] = {"action": action, "hint": hint or action}
    if args:
        step["args"] = args
    return step


def _excerpt(text: str | None, n: int = 240) -> str | None:
    if not text:
        return text
    t = text.strip()
    return t if len(t) <= n else t[:n] + "…"


def _count(rt: Runtime, sql: str, *params: Any) -> int:
    row = rt.conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def _json_loads(v: Any) -> Any:
    return store_db.json_loads(v) if isinstance(v, str) else v


# --------------------------------------------------------------------------- #
# 跨表读取小工具（source 层，只读）
# --------------------------------------------------------------------------- #
def _analyzed_sha(rt: Runtime, repo_id: str) -> str | None:
    row = analysis_store.latest_committed(rt.conn, repo_id)
    return row["commit_sha"] if row else None


def _card_repo(rt: Runtime, card_id: str) -> str | None:
    row = rt.conn.execute(
        "SELECT a.repo_id AS repo_id FROM cards c "
        "JOIN features f ON c.feature_id = f.feature_id "
        "JOIN analyses a ON f.analysis_id = a.analysis_id "
        "WHERE c.card_id = ?",
        (card_id,),
    ).fetchone()
    return row["repo_id"] if row else None


def _card_feature(rt: Runtime, card_id: str) -> dict[str, Any] | None:
    row = rt.conn.execute(
        "SELECT f.slug AS key, f.title AS title FROM cards c "
        "JOIN features f ON c.feature_id = f.feature_id WHERE c.card_id = ?",
        (card_id,),
    ).fetchone()
    return {"key": row["key"], "title": row["title"]} if row else None


def _card_evidence(rt: Runtime, card_id: str) -> list[dict[str, Any]]:
    rows = rt.conn.execute(
        "SELECT path, start_line, end_line, symbol, file_sha FROM evidence "
        "WHERE card_id = ? ORDER BY evidence_id",
        (card_id,),
    ).fetchall()
    return [
        {
            "path": r["path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "symbol": r["symbol"],
            "file_sha": r["file_sha"],
        }
        for r in rows
    ]


def _card_tags(rt: Runtime, card_id: str) -> list[str]:
    row = rt.conn.execute(
        "SELECT f.slug AS slug, c.title AS title, a.report_json AS report_json FROM cards c "
        "JOIN features f ON c.feature_id = f.feature_id "
        "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE c.card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        return []
    report = _json_loads(row["report_json"]) or {}
    for feat in report.get("features", []):
        if feat.get("key") == row["slug"]:
            for card in feat.get("cards", []):
                if card.get("title") == row["title"]:
                    return list(card.get("tags") or [])
    return []


def _card_patterns(rt: Runtime, card_id: str) -> list[dict[str, Any]]:
    rows = rt.conn.execute(
        "SELECT p.key AS key, p.title AS title FROM pattern_members m "
        "JOIN patterns p ON m.pattern_id = p.pattern_id WHERE m.card_id = ?",
        (card_id,),
    ).fetchall()
    return [{"key": r["key"], "title": r["title"]} for r in rows]


def _pattern_row(rt: Runtime, key: str) -> Any:
    return rt.conn.execute("SELECT * FROM patterns WHERE key = ?", (key,)).fetchone()


def _pattern_member_ids(rt: Runtime, pattern_id: str) -> list[str]:
    return [
        r["card_id"]
        for r in rt.conn.execute(
            "SELECT card_id FROM pattern_members WHERE pattern_id = ?", (pattern_id,)
        ).fetchall()
    ]


def _pattern_intents(rt: Runtime, pattern_id: str) -> list[str]:
    return [
        r["text"]
        for r in rt.conn.execute(
            "SELECT text FROM pattern_intents WHERE pattern_id = ?", (pattern_id,)
        ).fetchall()
    ]


def _pattern_repos(rt: Runtime, pattern_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cid in _pattern_member_ids(rt, pattern_id):
        rid = _card_repo(rt, cid)
        if rid and rid not in seen:
            seen.add(rid)
            repo = get_repo(rt.conn, rid)
            if repo:
                out.append(repo_summary(repo, analyzed_sha=_analyzed_sha(rt, rid)))
    return out


def _slice_text(rt: Runtime, repo_id: str | None, span: dict[str, Any]) -> str | None:
    if not repo_id:
        return None
    try:
        res = read_slice(
            rt.cfg,
            repo_id,
            span.get("path", ""),
            start_line=int(span.get("start_line", 1) or 1),
            end_line=span.get("end_line"),
            conn=rt.conn,
        )
        return cast(str, res["text"])
    except MemexError:
        return None


def _groups_from_tree(tree: dict[str, Any], entry_points: list[dict[str, Any]], *, max_groups: int = 100) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    entry_tops = {ep.get("path", "").split("/", 1)[0] for ep in entry_points}

    def walk(node: dict[str, Any]) -> None:
        for ch in node.get("children", []) or []:
            p = ch.get("path", "")
            top = p.split("/", 1)[0] if p else p
            g = groups.setdefault(top, {"path": top, "files": 0, "entry_like": top in entry_tops})
            if ch.get("type") == "file":
                g["files"] += 1
            walk(ch)

    walk(tree)
    out = sorted(groups.values(), key=lambda g: (-g["files"], g["path"]))
    return out[:max_groups]


# --------------------------------------------------------------------------- #
# T1 fetch_repo
# --------------------------------------------------------------------------- #
@handler("fetch_repo")
def _t1_fetch_repo(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_url = _req_str(args, "repo_url")
    ref = _opt_str(args, "ref")
    subpath = _opt_str(args, "subpath")
    refresh = _opt_bool(args, "refresh", False)
    try:
        res = fetch_repo(
            rt.cfg, repo_url, ref=ref, subpath=subpath, refresh=refresh, conn=rt.conn
        )
    except GitError as exc:
        raise MemexError(
            "fetch_failed", str(exc), {"retryable": bool(getattr(exc, "retryable", False))}
        ) from None
    repo = res["repo"]
    payload: dict[str, Any] = {"ok": True, "repo": repo, "is_new": res.get("is_new", False)}
    if not rt.cfg.is_http and res.get("repo_path"):
        payload["repo_path"] = res["repo_path"]
    warns = res.get("warnings") or []
    if warns:
        payload["warnings"] = warns
    payload["next_step"] = _next(
        "get_evidence_pack",
        {"repo_id": repo["repo_id"]},
        "取证据包（目录树 + 入口点 + 符号），再 begin_analysis 开会话",
    )
    return payload


# --------------------------------------------------------------------------- #
# T2 get_evidence_pack
# --------------------------------------------------------------------------- #
def _pack_core(rt: Runtime, repo_id: str, *, depth: str, subpath: str | None = None) -> dict[str, Any]:
    pack = build_pack(rt.cfg, repo_id, depth=depth, subpath=subpath, conn=rt.conn)
    repo = repo_summary(pack["repo"], analyzed_sha=_analyzed_sha(rt, repo_id))
    payload: dict[str, Any] = {
        "ok": True,
        "repo": repo,
        "commit_sha": pack["commit_sha"],
        "tree": pack["tree"],
        "entry_points": pack["entry_points"],
        "symbols": pack["symbols"],
        "stats": pack["stats"],
    }
    groups = _groups_from_tree(pack["tree"], pack["entry_points"])
    if groups:
        payload["groups"] = groups
    return payload


@handler("get_evidence_pack")
def _t2_get_evidence_pack(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    depth = _opt_str(args, "depth") or DEFAULT_DEPTH
    _check_depth(depth)
    subpath = _opt_str(args, "subpath")
    payload = _pack_core(rt, repo_id, depth=depth, subpath=subpath)
    payload["next_step"] = _next(
        "begin_analysis",
        {"repo_id": repo_id, "depth": depth},
        "开会话拿契约 + 清单，然后读本地克隆挑功能",
    )
    return payload


# --------------------------------------------------------------------------- #
# T3 read_file_slice
# --------------------------------------------------------------------------- #
@handler("read_file_slice")
def _t3_read_file_slice(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    path = _req_str(args, "path")
    start_line = _opt_int(args, "start_line", 1, minimum=1)
    end_line = _opt_int(args, "end_line", None, minimum=1)
    max_lines = _opt_int(args, "max_lines", 400, minimum=1)
    assert start_line is not None and max_lines is not None
    res = read_slice(
        rt.cfg,
        repo_id,
        path,
        start_line=start_line,
        end_line=end_line,
        max_lines=max_lines,
        conn=rt.conn,
    )
    return {
        "ok": True,
        "path": res["path"],
        "start_line": res["start_line"],
        "end_line": res["end_line"],
        "total_lines": res.get("total_lines"),
        "text": res["text"],
        "file_sha": res["file_sha"],
        "truncated": res.get("truncated", False),
    }


# --------------------------------------------------------------------------- #
# T4 request_repo_bundle
# --------------------------------------------------------------------------- #
@handler("request_repo_bundle")
def _t4_request_repo_bundle(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    ref = _opt_str(args, "ref")
    res = request_repo_bundle(rt.cfg, repo_id, ref=ref)
    payload: dict[str, Any] = {
        "ok": True,
        "url": res["url"],
        "sha256": res["sha256"],
        "commit_sha": res["commit_sha"],
    }
    for k in ("bytes", "expires_at", "usage"):
        if res.get(k) is not None:
            payload[k] = res[k]
    return payload


# --------------------------------------------------------------------------- #
# T5 begin_analysis
# --------------------------------------------------------------------------- #
@handler("begin_analysis")
def _t5_begin_analysis(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    ref = _opt_str(args, "ref")
    depth = _opt_str(args, "depth") or DEFAULT_DEPTH
    _check_depth(depth)
    analyst = _opt_str(args, "analyst") or rt.client_name or "agent"
    include_pack = _opt_bool(args, "include_pack", True)

    repo = get_repo(rt.conn, repo_id)
    if repo is None:
        raise MemexError("not_found", "仓库未登记：" + repo_id, {"repo_id": repo_id})
    commit_sha = repo.get("head_sha")
    if not commit_sha:
        raise MemexError(
            "not_found", "仓库缺少 head_sha（可能尚未克隆）：" + repo_id, {"repo_id": repo_id}
        )

    existing_analysis = analysis_store.find_analysis(
        rt.conn, repo_id, commit_sha, constants.CONTRACT_VERSION
    )
    resumed = False
    session = session_manager.find_active_session(rt.conn, repo_id, commit_sha)
    if session is not None:
        resumed = True
    else:
        session = session_manager.begin_session(
            rt.conn,
            rt.cfg,
            repo_id,
            commit_sha,
            depth=depth,
            analyst=analyst,
            include_pack=include_pack,
        )

    payload: dict[str, Any] = {
        "ok": True,
        "session_id": session["session_id"],
        "state": session["state"],
        "repo": repo_summary(
            repo, analyzed_sha=(commit_sha if existing_analysis is not None else None)
        ),
        "commit_sha": commit_sha,
        "contract": contract_mod.analysis_contract(),
        "checklist": list(contract_mod.ANALYSIS_CHECKLIST),
        "expires_at": session["expires_at"],
        "analyst": analyst,
    }
    if include_pack:
        payload["evidence_pack"] = _pack_core(rt, repo_id, depth=depth)
    if resumed:
        payload["resumed"] = True
    if existing_analysis is not None:
        payload["already_analyzed"] = True
    payload["next_step"] = _next(
        "validate_report",
        {},
        "读完克隆、挑出 >=3 个功能后，先 validate_report 自查，再 commit_report 落库",
    )
    return payload


# --------------------------------------------------------------------------- #
# T6 validate_report
# --------------------------------------------------------------------------- #
@handler("validate_report")
def _t6_validate_report(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    report = args.get("report")
    if not isinstance(report, dict):
        raise MemexError("invalid_argument", "report 必须是对象", {"param": "report"})
    repo_id = _opt_str(args, "repo_id")
    repo_root: str | None = None
    subpath: str | None = None
    if repo_id:
        if not is_repo_id(repo_id):
            raise MemexError("invalid_argument", "repo_id 格式非法：" + repo_id, {"param": "repo_id"})
        repo = get_repo(rt.conn, repo_id)
        if repo is None:
            raise MemexError("not_found", "仓库未登记：" + repo_id, {"repo_id": repo_id})
        repo_root = repo.get("repo_path")
        subpath = repo.get("subpath")
    res = validate_report(report, repo_root=repo_root, subpath=subpath)
    session_id = _opt_str(args, "session_id")
    if session_id:
        session = session_manager.get_session(rt.conn, session_id)
        if session is None:
            raise MemexError("not_found", "会话不存在", {"session_id": session_id})
        if res["is_valid"] and not session_state.is_terminal(session["state"]):
            if session["state"] in ("begun", "evidence_taken"):
                session_manager.set_state(rt.conn, session, "drafting")
            if session["state"] == "drafting":
                session_manager.set_state(rt.conn, session, "validated")
    return {
        "ok": True,
        "is_valid": res["is_valid"],
        "problems": res.get("problems", []),
        "warnings": res.get("warnings", []),
        "counts": res.get("counts", {}),
    }


# --------------------------------------------------------------------------- #
# T7 commit_report
# --------------------------------------------------------------------------- #
@handler("commit_report")
def _t7_commit_report(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    session_id = _req_str(args, "session_id")
    report = args.get("report")
    if not isinstance(report, dict):
        raise MemexError("invalid_argument", "report 必须是对象", {"param": "report"})
    analyst = _opt_str(args, "analyst")
    force = _opt_bool(args, "force", False)

    session = session_manager.get_session(rt.conn, session_id)
    res = commit_report(
        rt.conn,
        rt.cfg,
        rt.embedder(),
        session_id=session_id,
        report=report,
        analyst=analyst or "",
        force=force,
    )

    counts = dict(res.get("counts") or {})
    payload: dict[str, Any] = {
        "ok": True,
        "is_committed": not bool(res.get("already_analyzed")),
        "analysis_id": res.get("analysis_id"),
        "contract_id": constants.CONTRACT_ID,
        "producer": "agent",
        "analyst": analyst or (session["meta"].get("analyst") if session else None) or "agent",
    }
    if session is not None:
        commit_sha = session["meta"].get("commit_sha")
        repo = get_repo(rt.conn, session["repo_id"])
        if repo:
            payload["repo"] = repo_summary(repo, analyzed_sha=commit_sha)
        payload["commit_sha"] = commit_sha
    payload["counts"] = {
        "features": counts.get("features", res.get("features", 0)),
        "cards": counts.get("cards", res.get("cards", 0)),
        "reusable_cards": counts.get("reusable_cards", 0),
        "evidence": counts.get("evidence", res.get("evidence", 0)),
        "chunks_indexed": res.get("chunks", 0),
    }
    payload["quality"] = {
        "code_mismatch": res.get("code_mismatch", 0),
        "axis_completeness": 1.0,
        "evidence_coverage": 1.0,
    }
    if not res.get("already_analyzed"):
        payload["next_step"] = _next(
            "search_implementations",
            {"query": "<关键机制词>"},
            "落库完成；可用 search_implementations 验证召回",
        )
    return payload


# --------------------------------------------------------------------------- #
# T8 search_implementations
# --------------------------------------------------------------------------- #
def _search_item(rt: Runtime, it: dict[str, Any], detail: str) -> dict[str, Any]:
    kind = it.get("kind")
    card = it.get("card") or {}
    item: dict[str, Any] = {
        "score": it.get("score"),
        "matched_by": it.get("matched_by", []),
        "chunk_kind": kind,
        "title": it.get("heading") or card.get("title"),
        "heading": it.get("heading"),
        "language": it.get("language"),
        "excerpt": _excerpt(it.get("text")),
        "card_id": it.get("card_id"),
        "ref_id": it.get("ref_id"),
    }
    repo_id = it.get("repo_id")
    if repo_id:
        repo = get_repo(rt.conn, repo_id)
        if repo:
            item["repo"] = repo_summary(repo, analyzed_sha=_analyzed_sha(rt, repo_id))
    if kind == "pattern":
        row = _pattern_row(rt, str(it.get("ref_id")))
        if row is not None:
            item["pattern_key"] = row["key"]
            item["repos"] = _pattern_repos(rt, row["pattern_id"])
            intents = _pattern_intents(rt, row["pattern_id"])
            if intents:
                item["intents"] = intents
    if detail in ("normal", "full"):
        if card:
            item["mechanism_desc"] = card.get("mechanism_desc")
            tags = _card_tags(rt, str(card.get("card_id")))
            if tags:
                item["tags"] = tags
            ev = _card_evidence(rt, str(card.get("card_id")))
            if ev:
                item["source"] = ev[0]
    if detail == "full":
        if card:
            item["evidence"] = _card_evidence(rt, str(card.get("card_id")))
            spans = card.get("code_spans") or []
            if spans:
                item["code"] = [{"span": sp, "text": _slice_text(rt, repo_id, sp)} for sp in spans]
    return item


@handler("search_implementations")
def _t8_search_implementations(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    query = _req_str(args, "query")
    limit = _opt_int(args, "limit", 8, minimum=1, maximum=100)
    assert limit is not None
    assert limit is not None
    repo_id = _opt_str(args, "repo_id")
    language = _opt_str(args, "language")
    kind = _opt_str(args, "kind")
    detail = _opt_str(args, "detail") or "normal"
    rerank = _opt_bool(args, "rerank", False)
    if detail not in constants.DETAIL_LEVELS:
        raise MemexError(
            "invalid_argument", "detail 必须是 brief|normal|full", {"param": "detail"}
        )
    res = search_store.search(
        rt.conn,
        rt.embedder(),
        query,
        limit=limit,
        repo_id=repo_id,
        language=language,
        kind=kind,
        cfg_rerank=("on" if rerank else rt.cfg.rerank),
    )
    results = [_search_item(rt, it, detail) for it in res.get("items", [])]
    payload: dict[str, Any] = {
        "ok": True,
        "query": res.get("query", query),
        "detail": detail,
        "channels": res.get("channels", {}),
        "results": results,
    }
    notes = res.get("notes")
    if notes:
        payload["notes"] = notes
    return payload


# --------------------------------------------------------------------------- #
# T9 get_card
# --------------------------------------------------------------------------- #
def _card_detail(rt: Runtime, row: dict[str, Any], detail: str) -> dict[str, Any]:
    card_id = row["card_id"]
    repo_id = _card_repo(rt, card_id)
    spans = _json_loads(row.get("code_spans_json")) or []
    card: dict[str, Any] = {
        "card_id": card_id,
        "kind": row["kind"],
        "reusable": bool(row["reusable"]),
        "title": row["title"],
        "summary": row.get("summary"),
        "mechanism_desc": row.get("mechanism_desc"),
        "language": row.get("language"),
        "symbol": row.get("symbol"),
    }
    tags = _card_tags(rt, card_id)
    if tags:
        card["tags"] = tags
    if repo_id:
        repo = get_repo(rt.conn, repo_id)
        if repo:
            card["repo"] = repo_summary(repo, analyzed_sha=_analyzed_sha(rt, repo_id))
    feature = _card_feature(rt, card_id)
    if feature:
        card["feature"] = feature
    card["evidence"] = _card_evidence(rt, card_id)
    patterns = _card_patterns(rt, card_id)
    if patterns:
        card["patterns"] = patterns
    if detail == "full":
        card["code"] = [{"span": sp, "text": _slice_text(rt, repo_id, sp)} for sp in spans]
        card["quality"] = _json_loads(row.get("quality_json"))
    return card


@handler("get_card")
def _t9_get_card(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    card_id = _req_str(args, "card_id")
    detail = _opt_str(args, "detail") or "normal"
    if detail not in constants.DETAIL_LEVELS:
        raise MemexError(
            "invalid_argument", "detail 必须是 brief|normal|full", {"param": "detail"}
        )
    row = rt.conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
    if row is None:
        raise MemexError("not_found", "卡片不存在：" + card_id, {"card_id": card_id})
    return {"ok": True, "card": _card_detail(rt, dict(row), detail)}


# --------------------------------------------------------------------------- #
# T10 list_patterns
# --------------------------------------------------------------------------- #
def _pattern_item(rt: Runtime, p: dict[str, Any]) -> dict[str, Any]:
    tags = _json_loads(p.get("tags_json")) or []
    repos = _pattern_repos(rt, p["pattern_id"])
    langs = sorted({str(r.get("language")) for r in repos if r.get("language")})
    intents = _pattern_intents(rt, p["pattern_id"])
    item: dict[str, Any] = {
        "pattern_id": p["pattern_id"],
        "key": p["key"],
        "title": p["title"],
        "repo_count": p["repo_count"],
        "card_count": p["card_count"],
    }
    if tags:
        item["tags"] = list(tags)
    if langs:
        item["languages"] = langs
    if repos:
        item["repos"] = repos
    if intents:
        item["intents"] = intents
    return item


def _pattern_matches(rt: Runtime, p: dict[str, Any], query: str) -> bool:
    q = query.lower()
    tags = _json_loads(p.get("tags_json")) or []
    if any(q in str(t).lower() for t in tags):
        return True
    return any(q in t.lower() for t in _pattern_intents(rt, p["pattern_id"]))


@handler("list_patterns")
def _t10_list_patterns(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    query = _opt_str(args, "query")
    limit = _opt_int(args, "limit", 20, minimum=1, maximum=100)
    assert limit is not None
    assert limit is not None
    cursor = _opt_str(args, "cursor")
    rows = rt.conn.execute(
        "SELECT * FROM patterns ORDER BY card_count DESC, repo_count DESC, pattern_id"
    ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        p = dict(r)
        if query:
            hay = (str(p.get("title") or "") + " " + str(p.get("key") or "")).lower()
            if query.lower() not in hay and not _pattern_matches(rt, p, query):
                continue
        items.append(_pattern_item(rt, p))
    page = envelope.page(items, limit=limit, cursor=cursor, total=len(items))
    return {"ok": True, **page}


# --------------------------------------------------------------------------- #
# T11 get_report
# --------------------------------------------------------------------------- #
def _analysis_meta(ana: dict[str, Any]) -> dict[str, Any]:
    return {
        "analysis_id": ana["analysis_id"],
        "commit_sha": ana.get("commit_sha"),
        "contract_id": constants.CONTRACT_ID,
        "status": ana.get("status"),
        "contract_version": ana.get("contract_version"),
        "analyst": ana.get("analyst"),
        "producer": ana.get("producer"),
        "depth": ana.get("depth"),
        "created_at": ana.get("created_at"),
        "finished_at": ana.get("finished_at"),
        "counts": ana.get("counts"),
        "quality": ana.get("quality"),
    }


@handler("get_report")
def _t11_get_report(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    commit_sha = _opt_str(args, "commit_sha")
    fmt = _opt_str(args, "format") or "markdown"
    if fmt not in ("markdown", "json", "both"):
        raise MemexError(
            "invalid_argument", "format 必须是 markdown|json|both", {"param": "format"}
        )
    ana = analysis_store.get_report(rt.conn, repo_id=repo_id, commit_sha=commit_sha)
    if ana is None:
        raise MemexError(
            "not_found",
            "未找到报告：" + repo_id,
            {"repo_id": repo_id, "commit_sha": commit_sha},
        )
    repo = get_repo(rt.conn, repo_id)
    payload: dict[str, Any] = {
        "ok": True,
        "repo": repo_summary(repo, analyzed_sha=ana.get("commit_sha")) if repo else None,
        "analysis": _analysis_meta(ana),
        "report": ana.get("report_json"),
    }
    if fmt in ("markdown", "both"):
        payload["report_md"] = ana.get("report_md")
    return payload


# --------------------------------------------------------------------------- #
# T12 list_repos
# --------------------------------------------------------------------------- #
def _repo_item(rt: Runtime, repo: dict[str, Any]) -> dict[str, Any]:
    rid = repo["repo_id"]
    ana = analysis_store.latest_committed(rt.conn, rid)
    analyzed_sha = ana["commit_sha"] if ana else None
    item: dict[str, Any] = {"repo": repo_summary(repo, analyzed_sha=analyzed_sha)}
    item["features"] = _count(
        rt,
        "SELECT COUNT(*) FROM features f JOIN analyses a ON f.analysis_id = a.analysis_id "
        "WHERE a.repo_id = ?",
        rid,
    )
    item["cards"] = _count(
        rt,
        "SELECT COUNT(*) FROM cards c JOIN features f ON c.feature_id = f.feature_id "
        "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE a.repo_id = ?",
        rid,
    )
    if ana is not None:
        item["last_analysis_at"] = ana.get("finished_at") or ana.get("created_at")
        item["quality"] = ana.get("quality")
    if repo.get("subpath"):
        item["subpath"] = repo["subpath"]
    return item


@handler("list_repos")
def _t12_list_repos(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    query = _opt_str(args, "query")
    language = _opt_str(args, "language")
    is_stale_only = _opt_bool(args, "is_stale_only", False)
    limit = _opt_int(args, "limit", 20, minimum=1, maximum=200)
    assert limit is not None
    assert limit is not None
    cursor = _opt_str(args, "cursor")
    where: list[str] = []
    params: list[Any] = []
    if query:
        where.append("(repo_id LIKE ? OR full_name LIKE ?)")
        params += ["%" + query + "%", "%" + query + "%"]
    if language:
        where.append("language = ?")
        params.append(language)
    if is_stale_only:
        where.append("is_stale = 1")
    sql = "SELECT * FROM repos"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY repo_id"
    rows = rt.conn.execute(sql, params).fetchall()
    items = [_repo_item(rt, dict(r)) for r in rows]
    page = envelope.page(items, limit=limit, cursor=cursor, total=len(items))
    return {"ok": True, **page}


# --------------------------------------------------------------------------- #
# T13 recall_stats
# --------------------------------------------------------------------------- #
def _quality_stats(rt: Runtime) -> dict[str, Any]:
    by_producer = {
        (r["producer"] or "unknown"): r["n"]
        for r in rt.conn.execute(
            "SELECT producer, COUNT(*) AS n FROM analyses GROUP BY producer"
        ).fetchall()
    }
    mism = 0
    for r in rt.conn.execute(
        "SELECT quality_json FROM analyses WHERE quality_json IS NOT NULL"
    ).fetchall():
        q = _json_loads(r["quality_json"]) or {}
        mism += int(q.get("code_mismatch", 0) or 0)
    return {"by_producer": by_producer, "code_mismatch": mism}


def _db_bytes(rt: Runtime) -> int:
    total = 0
    for p in (rt.cfg.paths.db, rt.cfg.paths.index_db):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


@handler("recall_stats")
def _t13_recall_stats(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    st = store.stats(rt.conn)
    counts = {
        "repos": st.get("repos", 0),
        "analyses": st.get("analyses", 0),
        "features": st.get("features", 0),
        "cards": st.get("cards", 0),
        "patterns": st.get("patterns", 0),
        "chunks": st.get("chunks", 0),
    }
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": st.get("schema_version"),
        "contract_id": constants.CONTRACT_ID,
        "counts": counts,
    }
    spec = store.get_meta(rt.conn, "embedder")
    if spec:
        payload["embedder"] = {"model": spec, "dim": store.get_meta(rt.conn, "dim")}
    payload["quality"] = _quality_stats(rt)
    payload["stale"] = {"repos": _count(rt, "SELECT COUNT(*) FROM repos WHERE is_stale = 1")}
    payload["disk"] = {"home": str(rt.cfg.paths.home), "db_bytes": _db_bytes(rt)}
    return payload


# --------------------------------------------------------------------------- #
# T14 help
# --------------------------------------------------------------------------- #
@handler("help")
def _t14_help(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    from .help import HELP_TOPICS

    topic = _req_str(args, "topic")
    entry = HELP_TOPICS.get(topic)
    if entry is None:
        raise MemexError(
            "invalid_argument",
            "未知 topic：" + topic,
            {"topics": sorted(HELP_TOPICS.keys())},
        )
    return {
        "ok": True,
        "topic": topic,
        "title": entry["title"],
        "markdown": entry["markdown"],
        "related_topics": list(entry.get("related", [])),
    }


# --------------------------------------------------------------------------- #
# T15 forget_analysis
# --------------------------------------------------------------------------- #
def _safe_recluster(rt: Runtime) -> bool:
    try:
        recluster(rt.conn, rt.cfg)
        return True
    except Exception:  # noqa: BLE001 - 重聚类失败绝不回滚删除
        return False


def _analysis_counts(rt: Runtime, analysis_id: str) -> dict[str, Any]:
    return {
        "features": _count(rt, "SELECT COUNT(*) FROM features WHERE analysis_id = ?", analysis_id),
        "cards": _count(
            rt,
            "SELECT COUNT(*) FROM cards c JOIN features f ON c.feature_id = f.feature_id "
            "WHERE f.analysis_id = ?",
            analysis_id,
        ),
        "evidence": _count(
            rt,
            "SELECT COUNT(*) FROM evidence e JOIN cards c ON e.card_id = c.card_id "
            "JOIN features f ON c.feature_id = f.feature_id WHERE f.analysis_id = ?",
            analysis_id,
        ),
    }


@handler("forget_analysis")
def _t15_forget_analysis(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    analysis_id = _req_str(args, "analysis_id")
    if args.get("confirm") is not True:
        raise MemexError("invalid_argument", "confirm 必须显式为 true", {"param": "confirm"})
    counts = _analysis_counts(rt, analysis_id)
    store.forget_analysis(rt.conn, analysis_id, confirm=True)
    return {
        "ok": True,
        "deleted": "analysis",
        "counts": counts,
        "reclustered": _safe_recluster(rt),
    }


# --------------------------------------------------------------------------- #
# T16 forget_repo
# --------------------------------------------------------------------------- #
def _repo_delete_summary(rt: Runtime, repo: dict[str, Any]) -> dict[str, Any]:
    rid = repo["repo_id"]
    return {
        "repo_id": rid,
        "repo_full_name": repo.get("full_name"),
        "analyses": _count(rt, "SELECT COUNT(*) FROM analyses WHERE repo_id = ?", rid),
        "cards": _count(
            rt,
            "SELECT COUNT(*) FROM cards c JOIN features f ON c.feature_id = f.feature_id "
            "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE a.repo_id = ?",
            rid,
        ),
        "evidence": _count(
            rt,
            "SELECT COUNT(*) FROM evidence e JOIN cards c ON e.card_id = c.card_id "
            "JOIN features f ON c.feature_id = f.feature_id "
            "JOIN analyses a ON f.analysis_id = a.analysis_id WHERE a.repo_id = ?",
            rid,
        ),
    }


def _remove_clone(rt: Runtime, repo_id: str) -> None:
    path = rt.cfg.paths.repo_dir(repo_id)
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


@handler("forget_repo")
def _t16_forget_repo(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    repo_id = _repo_id_arg(args)
    if args.get("confirm") is not True:
        raise MemexError("invalid_argument", "confirm 必须显式为 true", {"param": "confirm"})
    repo = get_repo(rt.conn, repo_id)
    if repo is None:
        raise MemexError("not_found", "仓库未登记：" + repo_id, {"repo_id": repo_id})
    summary = _repo_delete_summary(rt, repo)
    store.forget_repo(rt.conn, repo_id, confirm=True)
    _remove_clone(rt, repo_id)
    return {
        "ok": True,
        "deleted": "repo",
        "summary": summary,
        "reclustered": _safe_recluster(rt),
    }


# --------------------------------------------------------------------------- #
# T17 upload_repo_bundle
# --------------------------------------------------------------------------- #
@handler("upload_repo_bundle")
def _t17_upload_repo_bundle(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    bundle_path = _req_str(args, "bundle_path")
    repo_url = _req_str(args, "repo_url")
    ref = _opt_str(args, "ref")
    subpath = _opt_str(args, "subpath")
    sha256 = _opt_str(args, "sha256")
    res = upload_repo_bundle(
        rt.cfg, bundle_path, repo_url, ref=ref, subpath=subpath, sha256=sha256, conn=rt.conn
    )
    repo = res["repo"]
    payload: dict[str, Any] = {
        "ok": True,
        "repo": repo,
        "commit_sha": res["commit_sha"],
        "bytes": res.get("bytes"),
        "is_new": res.get("is_new", True),
    }
    if not rt.cfg.is_http and res.get("repo_path"):
        payload["repo_path"] = res["repo_path"]
    warns = res.get("warnings") or []
    if warns:
        payload["warnings"] = warns
    payload["next_step"] = _next(
        "get_evidence_pack", {"repo_id": repo["repo_id"]}, "取证据包，然后 begin_analysis 开会话"
    )
    return payload


__all__ = ["Runtime", "ProtocolError", "dispatch", "handler"]
