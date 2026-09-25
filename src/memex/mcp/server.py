"""MCP 服务端：stdio（本地）与 Streamable HTTP（远程）。

- **stdio**：换行分隔的 JSON-RPC，读 stdin / 写 stdout（本地形态，见 tech-design §4.1）。
- **serve-http**：单个 `/mcp` 端点，POST + GET，**只收 application/json，不出 SSE**
  （E15）。强制校验 `Origin`；认证用静态 Bearer Token（E16）。`Mcp-Session-Id`
  是传输层会话标识，**与业务 session_id 无关**。

协议约定（docs/mcp-tools.md §1.14 / G27）：

- 只实现 `tools/*`。`resources/*` 与 `prompts/*` 一律 -32601。
- 未知工具 / 参数结构非法 -> JSON-RPC 错误 -32602（caller misuse）。
- 业务失败 -> 正常 result，`structuredContent.ok=false`，`isError` 保持 false。
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .. import __version__
from ..core import Config
from . import handlers
from ..startup import run_startup_check, startup_banner
from .handlers import ProtocolError, Runtime

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "memex"

# JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class Server:
    """与传输无关的 JSON-RPC 分派器。"""

    def __init__(self, cfg: Config, *, client_name: str | None = None):
        self.rt = Runtime(cfg, client_name=client_name)
        self._initialized = False
        self._client_name = client_name

    # ---- 生命周期 ---------------------------------------------------- #
    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        """处理一条 JSON-RPC 消息；通知返回 None。"""
        if not isinstance(msg, dict):
            return _rpc_error(None, INVALID_REQUEST, "消息必须是对象")
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        is_notification = req_id is None

        try:
            result = self._dispatch_method(method, params)
        except ProtocolError as exc:
            if is_notification:
                return None
            return _rpc_error(req_id, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            if is_notification:
                return None
            return _rpc_error(req_id, INTERNAL_ERROR, "服务端异常", {"reason": str(exc)})

        if result is _NOTIFICATION:
            return None
        if is_notification:
            return None
        return _rpc_result(req_id, result)

    def _dispatch_method(self, method: Any, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method in ("notifications/initialized", "initialized"):
            self._initialized = True
            return _NOTIFICATION
        if method in ("notifications/cancelled", "notifications/progress"):
            return _NOTIFICATION
        if method == "ping":
            return {}
        if method == "tools/list":
            return self._tools_list()
        if method == "tools/call":
            return self._tools_call(params)
        # G27：不实现 resources / prompts
        if isinstance(method, str) and (
            method.startswith("resources/") or method.startswith("prompts/")
        ):
            raise ProtocolError("不支持的方法：" + method, METHOD_NOT_FOUND)
        raise ProtocolError("未知方法：" + str(method), METHOD_NOT_FOUND)

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        info = params.get("clientInfo") or {}
        if isinstance(info, dict) and info.get("name"):
            self._client_name = str(info["name"])
            self.rt.client_name = self._client_name
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }

    def _tools_list(self) -> dict[str, Any]:
        from .schemas import tool_definitions

        tools = []
        for d in tool_definitions():
            tools.append(
                {
                    "name": d["name"],
                    "title": d.get("title"),
                    "description": d.get("description"),
                    "inputSchema": d["inputSchema"],
                    "outputSchema": d["outputSchema"],
                    "annotations": d["annotations"],
                }
            )
        return {"tools": tools}

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ProtocolError("tools/call 缺少 name", INVALID_PARAMS)
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ProtocolError("arguments 必须是对象", INVALID_PARAMS)
        return handlers.dispatch(self.rt, name, args)

    def close(self) -> None:
        self.rt.close()


class _Notification:
    pass


_NOTIFICATION = _Notification()


# --------------------------------------------------------------------------- #
# stdio 传输
# --------------------------------------------------------------------------- #
def _startup_or_exit(cfg: Config) -> dict[str, Any]:
    """跑启动检查；失败则打印可读提示并以非零码退出（operations.md §4）。"""
    from ..core import MemexError
    try:
        summary = run_startup_check(cfg)
    except MemexError as exc:
        sys.stderr.write("memex 启动失败 [" + exc.code + "]：" + exc.message + "\n")
        for out in (exc.details or {}).get("outs") or []:
            sys.stderr.write("  - " + str(out) + "\n")
        sys.stderr.flush()
        raise SystemExit(2) from exc
    sys.stderr.write(startup_banner(summary) + "\n")
    for w in summary.get("warnings") or []:
        sys.stderr.write("memex 警告：" + str(w) + "\n")
    sys.stderr.flush()
    return summary


def serve_stdio(cfg: Config | None = None) -> None:
    cfg = cfg or Config.from_env()
    cfg = _with_is_http(cfg, False)
    _startup_or_exit(cfg)
    server = Server(cfg)
    stdin = sys.stdin
    stdout = sys.stdout
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp = _rpc_error(None, PARSE_ERROR, "JSON 解析失败")
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()
                continue
            msg_resp = server.handle_message(msg)
            if msg_resp is not None:
                stdout.write(json.dumps(msg_resp, ensure_ascii=False) + "\n")
                stdout.flush()
    finally:
        server.close()


def _with_is_http(cfg: Config, value: bool) -> Config:
    try:
        cfg.is_http = value
    except Exception:  # noqa: BLE001
        pass
    return cfg


# --------------------------------------------------------------------------- #
# Streamable HTTP 传输（单 /mcp 端点，JSON-only，无 SSE）
# --------------------------------------------------------------------------- #
def serve_http(cfg: Config | None = None, *, host: str = "127.0.0.1", port: int = 8931) -> None:
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    resolved: Config = _with_is_http(cfg or Config.from_env(), True)
    _startup_or_exit(resolved)
    server = Server(resolved)
    sessions: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:  # 静音
            pass

        def _send(self, code: int, body: bytes, *, ctype: str = "application/json", extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, code: int, obj: Any, extra: dict[str, str] | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

        def _check_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            if resolved.allow_local_paths:
                return True
            return False

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            return auth == "Bearer " + resolved.token

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/mcp":
                self._json(404, {"error": "not found"})
                return
            if not self._check_auth():
                self._json(401, {"error": "unauthorized"})
                return
            # 无 SSE：GET 仅用于健康探测
            self._json(405, {"error": "GET 不提供事件流（json-only）"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/mcp":
                self._json(404, {"error": "not found"})
                return
            if not self._check_origin():
                self._json(403, {"error": "origin rejected"})
                return
            if not self._check_auth():
                self._json(401, {"error": "unauthorized"})
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._json(415, {"error": "只接受 application/json"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(200, _rpc_error(None, PARSE_ERROR, "JSON 解析失败"))
                return
            resp = server.handle_message(msg)
            extra = {}
            sid = self.headers.get("Mcp-Session-Id")
            if sid:
                sessions.add(sid)
            elif msg.get("method") == "initialize":
                extra["Mcp-Session-Id"] = uuid.uuid4().hex
            if resp is None:
                self._send(202, b"", extra=extra)
                return
            self._json(200, resp, extra=extra)

    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        server.close()


__all__ = ["Server", "serve_stdio", "serve_http", "PROTOCOL_VERSION"]
