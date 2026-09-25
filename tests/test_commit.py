"""提交路径 + 索引 + 检索 的端到端测试（V1 垂直切片的内核）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.analyze import commit_report  # noqa: E402
from memex.core import Config  # noqa: E402
from memex.embeddings import hash_embedder  # noqa: E402
from memex.session import manager as sm  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.store.search import search  # noqa: E402

AX = " ".join(
    "This axis explains in considerable detail exactly how the component behaves across normal and "
    "abnormal situations including retries timeouts cancellation partial failures and concurrent access "
    "so the reader fully understands the design choices and their practical implications for real deployments today".split()
)
assert len(AX.split()) >= 40, "夹具轴文本必须 >= 40 单位"
PRIN = {k: AX for k in ["runtime_control_flow", "data_flow", "state_lifecycle", "failure_recovery", "concurrency_timing"]}
S = "Implements a bounded retry loop with exponential backoff and jitter for all remote calls"


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "import time\n\nRETRIES = 3\n\n\ndef fetch(url):\n    for i in range(RETRIES):\n        try:\n            return _get(url)\n        except OSError:\n            time.sleep(2 ** i)\n    raise OSError('exhausted')\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Demo\n\nA demo repo with bounded retries.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def _report():
    def feat(i):
        return {
            "key": f"retry-loop-{i}",
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
    import os

    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
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
    return cfg, conn, root, head


def test_commit_search_roundtrip(tmp_path):
    cfg, conn, root, head = _setup(tmp_path)
    emb = hash_embedder(64)
    sess = sm.begin_session(conn, cfg, "github.com__demo__demo", head, depth="standard", analyst="test-agent", include_pack=False)
    sm.set_state(conn, sess, "drafting")
    sm.set_state(conn, sess, "validated")
    res = commit_report(conn, cfg, emb, session_id=sess["session_id"], report=_report(), analyst="test-agent")
    assert res["code_mismatch"] == 0
    assert res["features"] == 3
    assert res["cards"] == 3
    rows = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert rows == 3  # evidence 表按 card 归属（feature 级证据随 report_json 保存）
    assert conn.execute("SELECT state FROM sessions WHERE session_id = ?", (sess["session_id"],)).fetchone()[0] == "committed"
    assert conn.execute("SELECT COUNT(*) FROM session_stats WHERE session_id = ?", (sess["session_id"],)).fetchone()[0] == 1

    out = search(conn, emb, "bounded retry", limit=5)
    assert out["count"] >= 1, out
    kinds = {it["kind"] for it in out["items"]}
    assert "card" in kinds or "feature" in kinds
    card = next(it for it in out["items"] if it["kind"] == "card")
    assert "retry" in card["card"]["mechanism_desc"].lower()


def test_idempotent_same_content(tmp_path):
    cfg, conn, root, head = _setup(tmp_path)
    emb = hash_embedder(64)
    def run():
        s = sm.begin_session(conn, cfg, "github.com__demo__demo", head, depth="standard", analyst="a", include_pack=False)
        sm.set_state(conn, s, "drafting"); sm.set_state(conn, s, "validated")
        return commit_report(conn, cfg, emb, session_id=s["session_id"], report=_report(), analyst="a")
    r1 = run()
    r2 = run()
    assert r2["already_analyzed"] is True and r2["analysis_id"] == r1["analysis_id"]
    assert conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 3


def test_conflict_then_force(tmp_path):
    cfg, conn, root, head = _setup(tmp_path)
    emb = hash_embedder(64)
    def run(rep, force=False):
        s = sm.begin_session(conn, cfg, "github.com__demo__demo", head, depth="standard", analyst="a", include_pack=False)
        sm.set_state(conn, s, "drafting"); sm.set_state(conn, s, "validated")
        return commit_report(conn, cfg, emb, session_id=s["session_id"], report=rep, analyst="a", force=force)
    run(_report())
    rep2 = _report()
    rep2["one_liner"] = "A changed one-liner for the same commit."
    from memex.core import MemexError
    try:
        run(rep2)
        assert False, "expected conflict"
    except MemexError as exc:
        assert exc.code == "conflict"
    r = run(rep2, force=True)
    assert r["updated"] is True
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 3
