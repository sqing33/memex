"""MCP 服务端：stdio（本地）与 Streamable HTTP（远程）。

- **stdio**：换行分隔的 JSON-RPC，读 stdin / 写 stdout（本地形态，见 tech-design §4.1）。
- **serve-http**：**三个**端点——`POST /mcp`（JSON-RPC）、`GET /bundles/{repo_id}`
  （bundle 下载，票据即凭证）、`GET /healthz`（容器探活）。`/mcp` **只收
  application/json，不出 SSE**（E15）。强制校验 `Origin`（`MEMEX_ALLOWED_ORIGINS`
  白名单）；认证用静态 Bearer Token（E16）。**不下发 `Mcp-Session-Id`**：本服务无跨请求
  传输层状态（业务 session 走 `session_id`，在库里），发一个只写不查的头等于假装有状态
  ——旧实现就是 `sessions.add(sid)` 之后再没人读过。

协议约定（docs/mcp-tools.md §1.14 / G27）：

- 只实现 `tools/*`。`resources/*` 与 `prompts/*` 一律 -32601。
- 未知工具 / 参数结构非法 -> JSON-RPC 错误 -32602（caller misuse）。
- 业务失败 -> 正常 result，`structuredContent.ok=false`，`isError` 保持 false。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any

from .. import __version__, session
from ..core import Config, MemexError
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


def _denied(message: str) -> dict[str, Any]:
    """bundle 端点校验失败时的统一响应信封。

    HTTP 状态码按 deployment.md §4.2 取 403（签名错/过期一律 403，不区分
    「不存在」与「签名错」，避免向未持票据的一方泄露 bundle 是否存在）；
    错误码取 invalid_argument —— ERROR_CODES 是全局封闭集（新增要改
    docs/mcp-tools.md），而契约对 bundle 失败只规定 HTTP 码、未指定 error.code，
    故复用既有码，不动契约。
    """
    return {"ok": False, "error": {"code": "invalid_argument", "message": message}}


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
    server.rt.warm()  # 后台预热嵌入模型，不阻塞 initialize（D1/G22）
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
        if value:
            # A3/D5：远程形态**恒不接受本地路径**。
            # 以前这条是「碰巧」成立的：from_env 里 allow_local_paths 的默认值是
            # `not is_http`，但 _with_is_http 只改 is_http 不重算，而 cli 的 _config()
            # 从不设 MEMEX_IS_HTTP —— 于是远程实际拿到 True。
            # 更糟的是旧的 _check_origin 把同一个布尔当成「本机形态放行任意 Origin」的
            # 开关，一个布尔控制了两件不相干的事。现已拆开：本地路径由这里钉死，
            # Origin 由 MEMEX_ALLOWED_ORIGINS 管。
            cfg.allow_local_paths = False
    except Exception:  # noqa: BLE001
        pass
    return cfg


# --------------------------------------------------------------------------- #
# 限流（G7 / A7）
# --------------------------------------------------------------------------- #
class _TokenBucket:
    """按 token 的令牌桶。单进程内共享，线程安全。

    以前 `cfg.http_qps_per_token` 全仓只有「声明赋值」没有消费点，
    `rate_limited` 这个错误码从没有任何一处真的会抛出来——
    operations.md 与 mcp-tools.md 承诺的 `details.retry_after_seconds` 因此是空头支票。
    端口映射到公网之后这就是必需品：没有它，拿到 token 的一方可以无限打。
    """

    def __init__(self, qps: int, *, burst: int | None = None) -> None:
        self.qps = max(1, int(qps))
        self.burst = max(1, int(burst if burst is not None else self.qps))
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (剩余令牌, 上次时间)
        self._lock = threading.Lock()

    def allow(self, key: str) -> float:
        """扣一个令牌。返回 0.0 表示放行；返回 >0 表示还要等多少秒。"""
        now = time.monotonic()
        with self._lock:
            tokens, ts = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - ts) * self.qps)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0
            # 陈旧桶（久未使用）顺手清掉，否则 key 无界增长
            if len(self._buckets) > 64:
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 300.0}
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self.qps


def _health_payload(rt: Any, cfg: Config) -> dict[str, Any]:
    """`/healthz` 响应体。**不泄露路径 / token / 用户名**——它挂在公开端点上。"""
    ready = bool(getattr(rt, "embedder_ready", False))
    return {
        "ok": True,
        "status": "ok" if ready else "warming",
        "version": __version__,
        "embedder_ready": ready,
        "degraded": (cfg.embedder or "").strip().startswith("hash:"),
    }


# --------------------------------------------------------------------------- #
# Streamable HTTP 传输（单 /mcp 端点，JSON-only，无 SSE）
# --------------------------------------------------------------------------- #
def serve_http(cfg: Config | None = None, *, host: str = "127.0.0.1", port: int = 8931) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, unquote

    resolved: Config = _with_is_http(cfg or Config.from_env(), True)
    # B1：远程形态必须给绝对基址。没有它，T4 只能退回 file:// 形态，远程 agent
    # 拿到的是死链——而 bundle 下发正是「远程可用」的第一根杠杆。缺省拒启，
    # 不给「配了但没生效」的中间态。
    if not resolved.public_base_url:
        sys.stderr.write(
            "memex 启动失败 [invalid_argument]：远程形态必须设置 MEMEX_PUBLIC_BASE_URL\n"
            "  原因：T4 下发的 bundle 票据要拼绝对 URL；缺了它 agent 拿到的仍是 file://，\n"
            "        在远程机器上打不开。\n"
            "  示例：MEMEX_PUBLIC_BASE_URL=https://memex.example.com\n"
        )
        sys.stderr.flush()
        raise SystemExit(2)
    _startup_or_exit(resolved)
    server = Server(resolved)
    server.rt.warm()  # 后台预热嵌入模型，不阻塞 initialize（D1/G22）
    limiter = _TokenBucket(resolved.http_qps_per_token)

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

        def _route(self) -> str:
            return self.path.split("?", 1)[0].rstrip("/") or "/"

        def _check_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                # 没有 Origin 头 = 不是浏览器发出来的：MCP 客户端、curl、git 都走
                # 这条路径，远程接入的正常流量本来就全在这里。
                return True
            # 有 Origin 就必须在白名单里（tech-design §4.4 的防 DNS rebinding）。
            # 旧实现把它绑在 allow_local_paths 上，已于 _with_is_http 注释里说明。
            return origin.strip().rstrip("/") in resolved.allowed_origins

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            return auth == "Bearer " + resolved.token

        def _serve_bundle(self, rest: str) -> None:
            """GET /bundles/{repo_id}?commit=&exp=&sig=

            票据即凭证，不校验 Bearer——agent 的 git clone/curl 不便带头。
            校验失败一律 403，不区分「不存在」与「签名错」：区分开等于向未持票据的
            一方泄露 bundle 是否存在。
            """
            from ..fetch import bundle as bundle_mod

            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            repo_id = unquote(rest)
            commit = (query.get("commit") or [""])[0]
            exp = (query.get("exp") or [""])[0]
            sig = (query.get("sig") or [""])[0]
            if not bundle_mod.repo_id_is_safe(repo_id):
                self._json(403, _denied("bundle 票据无效或已过期"))
                return
            if not bundle_mod.verify_ticket(resolved, repo_id, commit, exp, sig):
                self._json(403, _denied("bundle 票据无效或已过期"))
                return
            try:
                path = bundle_mod.bundle_path_for(resolved, repo_id, commit)
            except MemexError:
                self._json(403, _denied("bundle 票据无效或已过期"))
                return
            if not path.is_file():
                # 票据有效但产物被 LRU 清掉了：让 agent 重新调一次 T4 即可。
                self._json(
                    404,
                    {
                        "ok": False,
                        "error": {
                            "code": "not_found",
                            "message": "bundle 已被清理，请重新调用 request_repo_bundle",
                        },
                    },
                )
                return
            self._send(
                200,
                path.read_bytes(),
                ctype="application/octet-stream",
                extra={"Content-Disposition": "attachment"},
            )

        def do_GET(self) -> None:  # noqa: N802
            route = self._route()
            if route == "/healthz":
                # 探活不带 Bearer：容器 healthcheck 不便持有密钥，响应体也不含敏感信息。
                self._json(200, _health_payload(server.rt, resolved))
                return
            if route.startswith("/bundles/"):
                self._serve_bundle(route[len("/bundles/"):])
                return
            if route != "/mcp":
                self._json(404, {"error": "not found"})
                return
            if not self._check_auth():
                self._json(401, {"error": "unauthorized"})
                return
            # 无 SSE：GET 仅用于健康探测
            self._json(405, {"error": "GET 不提供事件流（json-only）"})

        def do_POST(self) -> None:  # noqa: N802
            if self._route() != "/mcp":
                self._json(404, {"error": "not found"})
                return
            if not self._check_origin():
                self._json(403, {"error": "origin rejected"})
                return
            if not self._check_auth():
                self._json(401, {"error": "unauthorized"})
                return
            retry = limiter.allow(resolved.token or "-")
            if retry > 0:
                self._json(
                    429,
                    {
                        "ok": False,
                        "error": {
                            "code": "rate_limited",
                            "message": "超过 MEMEX_HTTP_QPS_PER_TOKEN 配额",
                            "details": {"retry_after_seconds": round(retry, 3)},
                        },
                    },
                    extra={"Retry-After": str(max(1, int(retry) + 1))},
                )
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
            # D7：不再下发 Mcp-Session-Id。本服务没有跨请求的传输层状态可关联，
            # 下发一个只写不查的头等于对外承诺了一个不存在的会话模型。
            if resp is None:
                self._send(202, b"")
                return
            self._json(200, resp)

    # 后台清扫：manager.sweep 的 docstring 与 operations.md §1 都承诺过这条路径。
    # 远程常驻时纯靠「下一次工具调用」不够——没人调用就永远不清扫，故后台线程是必需的。
    _stop = threading.Event()

    def _sweeper() -> None:
        while not _stop.wait(60.0):
            try:
                session.sweep(server.rt.conn, resolved)
            except Exception:  # noqa: BLE001 - 清扫失败不得拖垮服务进程
                import traceback
                traceback.print_exc()

    sweeper = threading.Thread(target=_sweeper, name="memex-sweep", daemon=True)
    sweeper.start()

    # request_queue_size：socketserver.TCPServer 默认 5，即 TCP accept backlog 只有 5。
    # 实测并发 n>=64 时大批连接被内核重置（ConnectionResetError），n=32 尚可。
    # tech-design §4.8 的目标是「几十个并发 agent」，5 远远不够。
    class _Server(ThreadingHTTPServer):
        request_queue_size = 128

    httpd = _Server((host, port), Handler)
    try:
        httpd.serve_forever()
    finally:
        _stop.set()
        httpd.server_close()
        server.close()


__all__ = ["Server", "serve_stdio", "serve_http", "PROTOCOL_VERSION"]
