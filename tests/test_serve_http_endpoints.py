"""远程 HTTP 形态的端点契约（deployment.md §4）。

真实起 serve_http（ThreadingHTTPServer）走真 socket：要钉死的正是「票据校验失败
到底是 403 还是 500」这类只有真 Handler 才暴露的行为 —— `_denied` 曾在 server.py
里被调用却从未定义，票据一错就 NameError -> 500，与 §4.2「失败一律 403」相悖。
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.fetch import bundle as bundle_mod  # noqa: E402
from memex.mcp.server import serve_http  # noqa: E402
from memex.store import db as store_db  # noqa: E402

TOKEN = "test-token-0123456789abcdef"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx 走这里
        return exc.code, dict(exc.headers), exc.read()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    home = tmp_path_factory.mktemp("memex-home")
    env = {
        "MEMEX_HOME": str(home),
        "MEMEX_EMBEDDER": "hash:64",
        "MEMEX_TOKEN": TOKEN,
        "MEMEX_PUBLIC_BASE_URL": "http://127.0.0.1:" + str(port),
        "MEMEX_ALLOWED_ORIGINS": "https://app.example.com",
    }
    cfg = Config.from_env(env)
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    threading.Thread(
        target=serve_http,
        args=(cfg,),
        kwargs={"host": "127.0.0.1", "port": port},
        daemon=True,  # serve_forever 没有对外关闭句柄，靠守护线程随测试进程退出
    ).start()
    base = "http://127.0.0.1:" + str(port)
    deadline = time.time() + 10
    while True:
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1).read()
            break
        except Exception:  # noqa: BLE001 - 端口未就绪就重试
            if time.time() > deadline:
                raise
            time.sleep(0.05)
    return cfg, base


def _make_bundle(cfg, repo_id="acme__widgets", commit="a" * 40) -> tuple[str, str, int]:
    path = bundle_mod.bundle_path_for(cfg, repo_id, commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"BUNDLE-BYTES")
    exp = int(time.time()) + 3600
    return commit, bundle_mod.sign_ticket(cfg, repo_id, commit, exp), exp


# ---- /healthz ------------------------------------------------------------- #
def test_healthz_is_public_and_leaks_nothing(server):
    cfg, base = server
    status, headers, body = _get(base + "/healthz")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    # §4.3：响应体不得泄露路径 / 库名 / token
    text = body.decode("utf-8")
    assert str(cfg.home) not in text
    assert cfg.token not in text


# ---- /bundles/{repo_id} --------------------------------------------------- #
def test_bundle_bad_ticket_is_403_not_500(server):
    """_denied 曾是未定义名：票据错本应 403，实际 NameError->500。"""
    cfg, base = server
    status, _, body = _get(base + "/bundles/acme__widgets?commit=deadbeef&exp=9999999999&sig=bad")
    assert status == 403, "签名错必须是 403（§4.2），不能是 500"
    err = json.loads(body)["error"]
    assert err["code"] == "invalid_argument"


def test_bundle_traversal_repo_id_is_403(server):
    cfg, base = server
    status, _, _ = _get(base + "/bundles/%2e%2e?commit=x&exp=9999999999&sig=x")
    assert status == 403


def test_bundle_expired_ticket_is_403(server):
    cfg, base = server
    commit = "b" * 40
    past = int(time.time()) - 10
    path = bundle_mod.bundle_path_for(cfg, "acme__widgets", commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    sig = bundle_mod.sign_ticket(cfg, "acme__widgets", commit, past)
    status, _, _ = _get(base + f"/bundles/acme__widgets?commit={commit}&exp={past}&sig={sig}")
    assert status == 403


def test_bundle_valid_ticket_downloads_as_attachment(server):
    cfg, base = server
    commit, sig, exp = _make_bundle(cfg)
    url = base + f"/bundles/acme__widgets?commit={commit}&exp={exp}&sig={sig}"
    status, headers, body = _get(url)
    assert status == 200
    assert headers.get("Content-Type") == "application/octet-stream"
    assert headers.get("Content-Disposition") == "attachment"  # §4.2
    assert int(headers["Content-Length"]) == len(body)
    assert body == b"BUNDLE-BYTES"
    # 下载后不删文件（§4.2）：同票据可重复下
    status2, _, body2 = _get(url)
    assert status2 == 200 and body2 == b"BUNDLE-BYTES"


def test_bundle_missing_file_with_valid_ticket_is_404(server):
    cfg, base = server
    commit = "c" * 40
    exp = int(time.time()) + 3600
    sig = bundle_mod.sign_ticket(cfg, "acme__widgets", commit, exp)
    status, _, body = _get(base + f"/bundles/acme__widgets?commit={commit}&exp={exp}&sig={sig}")
    assert status == 404
    assert json.loads(body)["error"]["code"] == "not_found"


# ---- /mcp ----------------------------------------------------------------- #
def test_mcp_post_without_bearer_is_401(server):
    cfg, base = server
    req = urllib.request.Request(
        base + "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401


def test_mcp_post_rejected_origin_is_403(server):
    cfg, base = server
    req = urllib.request.Request(
        base + "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + TOKEN,
            "Origin": "https://evil.example.com",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 403
