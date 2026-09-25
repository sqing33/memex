"""MCP 出参信封（docs/mcp-tools.md §1.2 / §1.4）。

- 成功：{"ok": true, <payload>}
- 失败：{"ok": false, "error": {"code","message","details"?}}
- 列表：{"count","total"?,"items","next_cursor"?}

铁律：业务失败走正常返回（ok:false），不用 JSON-RPC error；
协议错误（未知工具名 -32602 等）由 server 层单独处理。
"""

from __future__ import annotations

import base64
from typing import Any

from ..core import MemexError


def ok(**payload: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    out.update(payload)
    return out


def ok_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    out.update(payload)
    return out


def error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"ok": False, "error": err}


def from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MemexError):
        return error(exc.code, exc.message, exc.details or None)
    return error("internal", "服务端异常", {"reason": str(exc)})


def page(items: list[Any], *, limit: int, cursor: str | None, total: int | None = None) -> dict[str, Any]:
    """游标分页（offset 编码为不透明 base64 串）。"""
    offset = decode_cursor(cursor)
    window = items[offset : offset + limit]
    out: dict[str, Any] = {"count": len(window), "items": window}
    if total is None:
        total = len(items)
    out["total"] = total
    if offset + limit < len(items):
        out["next_cursor"] = encode_cursor(offset + limit)
    return out


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        return max(0, int(raw))
    except Exception:
        raise MemexError("invalid_argument", "cursor 非法", {"cursor": cursor})
