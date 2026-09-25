"""MCP 端到端冒烟：Server.handle_message 走完 分析 -> 提交 -> 召回 全流程。

不触网：仓库行直接登记（等价于 fetch_repo 之后的状态），随后全部通过 MCP 工具流完成。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.mcp.server import Server  # noqa: E402
from memex.store import db as store_db  # noqa: E402

AX = " ".join(
    "This axis explains in considerable detail exactly how the component behaves across normal and "
    "abnormal situations including retries timeouts cancellation partial failures and concurrent access "
    "so the reader fully understands the design choices and their practical implications for real deployments today".split()
)
assert len(AX.split()) >= 40
PRIN = {k: AX for k in ["runtime_control_flow", "data_flow", "state_lifecycle", "failure_recovery", "concurrency_timing"]}
S = "Implements a bounded retry loop with exponential backoff and jitter for all remote calls"


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    main = (
        "import time" + chr(10) + chr(10)
        + "RETRIES = 3" + chr(10) + chr(10) + chr(10)
        + "def fetch(url):" + chr(10)
        + "    for i in range(RETRIES):" + chr(10)
        + "        try:" + chr(10)
        + "            return _get(url)" + chr(10)
        + "        except OSError:" + chr(10)
        + "            time.sleep(2 ** i)" + chr(10)
        + "    raise OSError('exhausted')" + chr(10)
    )
    (root / "main.py").write_text(main, encoding="utf-8")
    (root / "README.md").write_text("# Demo" + chr(10) + chr(10) + "A demo repo with bounded retries." + chr(10), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def _report():
    def feat(i):
        return {
            "key": "retry-loop-" + str(i),
            "title": "Retry Loop Implementation",
            "summary": S,
            "principles": PRIN,
            "evidence": [{"path": "main.py", "start_line": 6, "end_line": 10}],
            "intent": "Reuse a bounded retry loop for resilient remote calls.",
            "cards": [{
                "kind": "mechanism",
                "title": "Bounded retry mechanism",
                "summary": S,
                "reusable": True,
                "mechanism_desc": S + " and a final give up after the attempt budget is exhausted.",
                "evidence": [{"path": "main.py", "start_line": 6, "end_line": 10}],
            }],
        }
    return {
        "schema_id": "memex/report/1",
        "one_liner": "A tiny repo demonstrating bounded retries.",
        "characteristics": [{
            "title": "Resilient fetching",
            "detail": "All remote calls go through a bounded retry loop with exponential backoff.",
            "evidence": [{"path": "main.py", "start_line": 6, "end_line": 10}],
        }],
        "entry_points": [{"path": "main.py", "role": "Library entry with fetch()", "kind": "main"}],
        "cross_feature_risks": [{
            "title": "Sleep-based backoff blocks the caller",
            "detail": "Time-based backoff blocks the calling thread under high concurrency.",
            "evidence": [{"path": "main.py", "start_line": 8, "end_line": 9}],
        }],
        "features": [feat(0), feat(1), feat(2)],
    }


def _setup(tmp_path):
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    root = _make_repo(tmp_path)
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    conn.execute(
        "INSERT INTO repos(repo_id, full_name, url, host, identity_key, source, head_sha, repo_path, default_branch) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("github.com__demo__demo", "demo/demo", "https://github.com/demo/demo", "github.com",
         "github.com#demo/demo", "fetch", head, str(root), "main"),
    )
    conn.close()
    return cfg, head


def _call(srv, name, args, req_id):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": {"name": name, "arguments": args}}
    resp = srv.handle_message(msg)
    assert resp is not None and "result" in resp, resp
    return resp["result"]["structuredContent"]


def test_mcp_end_to_end(tmp_path):
    cfg, head = _setup(tmp_path)
    srv = Server(cfg)
    try:
        # initialize
        init = srv.handle_message({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"clientInfo": {"name": "smoke"}}})
        assert init["result"]["serverInfo"]["name"] == "memex"
        assert init["result"]["capabilities"]["tools"]["listChanged"] is False

        # tools/list -> 17
        tl = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert len(tl["result"]["tools"]) == 17

        # 协议错误：未知工具 -> -32602
        bad = srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        assert bad["error"]["code"] == -32602, bad

        # G27：resources/* -> -32601
        res = srv.handle_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        assert res["error"]["code"] == -32601, res

        # destructive 默认关闭 -> disabled
        dis = _call(srv, "forget_repo", {"repo_id": "github.com__demo__demo", "confirm": True}, 4)
        assert dis["ok"] is False and dis["error"]["code"] == "disabled", dis

        # 证据包
        pack = _call(srv, "get_evidence_pack", {"repo_id": "github.com__demo__demo", "depth": "standard"}, 5)
        assert pack["ok"] is True
        assert pack["commit_sha"] == head
        assert pack["repo"]["repo_id"] == "github.com__demo__demo"
        assert pack["stats"]["files"] >= 2

        # 开会话
        beg = _call(srv, "begin_analysis", {"repo_id": "github.com__demo__demo", "depth": "standard", "analyst": "smoke"}, 6)
        assert beg["ok"] is True and beg["session_id"].startswith("sess_")
        sid = beg["session_id"]

        # 校验（未通过也要 ok=true）
        val = _call(srv, "validate_report", {"report": _report(), "repo_id": "github.com__demo__demo", "session_id": sid}, 7)
        assert val["ok"] is True and val["is_valid"] is True, val

        # 落库
        com = _call(srv, "commit_report", {"session_id": sid, "report": _report()}, 8)
        assert com["ok"] is True and com["is_committed"] is True, com
        assert com["quality"]["code_mismatch"] == 0
        assert com["counts"]["features"] == 3 and com["counts"]["cards"] == 3

        # 召回
        sr = _call(srv, "search_implementations", {"query": "bounded retry", "limit": 5}, 9)
        assert sr["ok"] is True and len(sr["results"]) >= 1, sr
        card_item = next(it for it in sr["results"] if it["chunk_kind"] == "card")
        cid = card_item["card_id"]

        # 取卡
        gc = _call(srv, "get_card", {"card_id": cid, "detail": "full"}, 10)
        assert gc["ok"] is True and gc["card"]["card_id"] == cid

        # 报告
        gr = _call(srv, "get_report", {"repo_id": "github.com__demo__demo", "format": "both"}, 11)
        assert gr["ok"] is True and gr["analysis"]["status"] == "committed"

        # 列表
        lr = _call(srv, "list_repos", {}, 12)
        assert lr["ok"] is True and lr["count"] == 1
        lp = _call(srv, "list_patterns", {}, 13)
        assert lp["ok"] is True

        # 统计 + help
        st = _call(srv, "recall_stats", {}, 14)
        assert st["ok"] is True and st["counts"]["repos"] == 1
        hp = _call(srv, "help", {"topic": "card-kinds"}, 15)
        assert hp["ok"] is True and "mechanism" in hp["markdown"]
        hp2 = _call(srv, "help", {"topic": "nope"}, 16)
        assert hp2["ok"] is False and hp2["error"]["code"] == "invalid_argument"
    finally:
        srv.close()
