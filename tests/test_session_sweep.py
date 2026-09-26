"""G26：会话清扫必须真的在工具调用路径上跑（此前 session.sweep 是死代码）。

背景：docs/operations.md §1 与 manager.sweep 的 docstring 都承诺「每次工具调用时
顺带执行」，serve-http 另有 60s 后台线程。但实现里 sweep() 零调用点——
真实库会话全是 committed（无积压），所以从未暴露。

这里造一个**已过期**的活跃会话，断言「任何一次工具调用」都会把它归档成
abandoned。这样既验证了 sweep 被接上，也验证它真的按 TTL 干活，
而不是只被调用了一次空转。
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _axes import PRIN  # noqa: E402
from memex.core import Config  # noqa: E402
from memex.mcp.server import Server  # noqa: E402
from memex.store import db as store_db  # noqa: E402

AX = " ".join(
    "This axis explains in considerable detail exactly how the component behaves across normal and "
    "abnormal situations including retries timeouts cancellation partial failures and concurrent access "
    "so the reader fully understands the design choices and their practical implications for real deployments today".split()
)
S = "Implements a bounded retry loop with exponential backoff and jitter for all remote calls"


def _report():
    def feat(i):
        return {
            "key": "retry-loop-" + str(i),
            "title": "Retry Loop Implementation",
            "summary": S,
            "principles": PRIN,
            "evidence": [{"path": "main.py", "start_line": 1, "end_line": 3, "symbol": "fetch"}],
            "cards": [
                {
                    "kind": "mechanism",
                    "title": "Bounded retry with backoff",
                    "summary": S,
                    "reusable": True,
                    "mechanism_desc": S,
                    "evidence": [{"path": "main.py", "start_line": 4, "end_line": 9, "symbol": "fetch"}],
                }
            ],
            "intent": "borrow how a bounded retry loop is implemented",
        }

    return {
        "schema_id": "memex/report/1",
        "one_liner": "A demo repo exercising bounded retries with exponential backoff and jitter",
        "characteristics": [{"title": "Bounded retry", "detail": S}],
        "entry_points": [{"path": "main.py", "role": S, "kind": "main"}],
        "features": [feat(1), feat(2), feat(3)],
    }


def _setup(tmp_path):
    os_env = {"MEMEX_HOME": str(tmp_path / "home"), "MEMEX_EMBEDDER": "hash:64"}
    import os

    os.environ.update(os_env)
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("import time\nRETRIES = 3\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=root, check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    conn = store_db.connect(cfg.paths.db)
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, identity_key, source, head_sha, repo_path, default_branch)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("github.com__demo__demo", "demo/demo", "https://github.com/demo/demo", "github.com",
         "github.com#demo/demo", "fetch", head, str(root), "main"),
    )
    conn.close()
    return cfg, head


def _call(srv, name, args, req_id=1):
    resp = srv.handle_message(
        {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
         "params": {"name": name, "arguments": args}}
    )
    assert resp is not None and "result" in resp, resp
    return resp["result"]["structuredContent"]


def test_tool_call_sweeps_expired_sessions(tmp_path):
    """任意一次工具调用都应把 TTL 到期的活跃会话归档为 abandoned。"""
    cfg, _head = _setup(tmp_path)
    srv = Server(cfg)
    try:
        beg = _call(srv, "begin_analysis",
                    {"repo_id": "github.com__demo__demo", "depth": "standard", "analyst": "smoke"})
        assert beg["ok"] is True
        sid = beg["session_id"]

        # 手工把 expires_at 推到过去，模拟 TTL 已到期的僵尸会话
        conn = store_db.connect(cfg.paths.db)
        conn.execute(
            "UPDATE sessions SET expires_at = '1970-01-01T00:00:00Z' WHERE session_id = ?",
            (sid,),
        )
        conn.close()

        # 任何一次工具调用都应顺带清扫
        res = _call(srv, "list_repos", {})
        assert res["ok"] is True, res

        conn = store_db.connect(cfg.paths.db)
        row = conn.execute(
            "SELECT state, abandoned_at FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["state"] == "abandoned", (
            "工具调用后过期会话应被清扫为 abandoned，实际 state=" + repr(row["state"])
        )
        assert row["abandoned_at"], "归档必须落 abandoned_at（回收三步的第 ② 步）"

        # 统计必须先落 session_stats（回收三步的第 ① 步，且永不删）
        conn = store_db.connect(cfg.paths.db)
        st = conn.execute(
            "SELECT COUNT(*) n FROM session_stats WHERE session_id = ?", (sid,)
        ).fetchone()
        conn.close()
        assert st["n"] == 1, "清扫必须先归档统计再回收，否则 V1 的可行性数据就丢了"
    finally:
        srv.close()
