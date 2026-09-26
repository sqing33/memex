"""契约校验器测试（A5：只读、幂等、可独立调用）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.contract import validate_report  # noqa: E402

from _axes import PRIN  # noqa: E402

AX = " ".join(
    "This axis explains in considerable detail exactly how the component behaves across normal and "
    "abnormal situations including retries timeouts cancellation partial failures and concurrent access "
    "so the reader fully understands the design choices and their practical implications for real deployments today".split()
)
assert len(AX.split()) >= 40, "夹具轴文本必须 >= 40 单位"
# 五轴必须各写各的：G23 的 axis_reuse 门禁会拒掉一字不差的五轴。

S = "Implements a bounded retry loop with exponential backoff and jitter for all remote calls"


def _feature(i, card):
    return {
        "key": f"retry-loop-{i}",
        "title": "Retry Loop Implementation",
        "summary": S,
        "principles": PRIN,
        "evidence": [{"path": "main.py", "start_line": 1, "end_line": 2}],
        "intent": "Reuse a bounded retry loop for resilient remote calls.",
        "cards": [card],
    }


def _report(card):
    return {
        "schema_id": "memex/report/1",
        "one_liner": "A retry engine",
        "characteristics": [
            {"title": "Resilient", "detail": "Uses bounded retries across all network calls in the system.",
             "evidence": [{"path": "main.py", "start_line": 1, "end_line": 2}]}
        ],
        "entry_points": [{"path": "main.py", "role": "Entry point for the CLI", "kind": "main"}],
        "cross_feature_risks": [
            {"title": "Risk of shared state", "detail": "Shared mutable state between features is unsafe.",
             "evidence": [{"path": "main.py", "start_line": 1, "end_line": 1}]}
        ],
        "features": [_feature(0, card), _feature(1, card), _feature(2, card)],
    }


CARD_OK = {
    "kind": "snippet",
    "title": "Bounded retry mechanism",
    "summary": S,
    "reusable": True,
    "mechanism_desc": S + " and a final give up after the attempt budget is exhausted.",
    "evidence": [{"path": "main.py", "start_line": 1, "end_line": 2}],
    "code_spans": [{"path": "main.py", "start_line": 1, "end_line": 2}],
    "code": "def a():\n    return 1",
}


def _root(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("def a():\n    return 1\ndef b():\n    return 2\n", encoding="utf-8")
    return tmp_path


def test_valid_report(tmp_path):
    res = validate_report(_report(CARD_OK), repo_root=_root(tmp_path))
    assert res["ok"] is True
    assert res["is_valid"] is True, res["problems"]
    assert res["counts"]["code_mismatch"] == 0
    assert res["counts"]["code_checked"] == 3


def test_code_mismatch(tmp_path):
    card = dict(CARD_OK, code="def a():\n    return 999")
    res = validate_report(_report(card), repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert res["counts"]["code_mismatch"] == 3
    assert {p["code"] for p in res["problems"]} == {"code_span_mismatch"}


def test_unknown_field(tmp_path):
    rep = _report(CARD_OK)
    rep["extra"] = 1
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "unknown_field" for p in res["problems"])


def test_duplicate_feature_key(tmp_path):
    rep = _report(CARD_OK)
    rep["features"][1]["key"] = rep["features"][0]["key"]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "duplicate_feature_key" for p in res["problems"])


def test_too_few_features(tmp_path):
    rep = _report(CARD_OK)
    rep["features"] = rep["features"][:2]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "too_few_features" for p in res["problems"])


def test_snippet_requires_code(tmp_path):
    card = {k: v for k, v in CARD_OK.items() if k not in ("code", "code_spans")}
    rep = _report(card)
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "code_required" for p in res["problems"])


def test_mechanism_forbids_code_spans(tmp_path):
    card = {k: v for k, v in CARD_OK.items() if k != "code"}
    card["kind"] = "mechanism"
    rep = _report(card)
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "code_forbidden" for p in res["problems"])


def test_bad_evidence_path(tmp_path):
    rep = _report(CARD_OK)
    rep["features"][0]["evidence"] = [{"path": "nope.py", "start_line": 1, "end_line": 1}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert any(p["code"] == "bad_evidence_path" for p in res["problems"])


def test_readonly_and_idempotent(tmp_path):
    root = _root(tmp_path)
    import copy

    snapshot = copy.deepcopy(_report(CARD_OK))
    a = validate_report(snapshot, repo_root=root)
    b = validate_report(snapshot, repo_root=root)
    assert a == b
    assert snapshot == copy.deepcopy(_report(CARD_OK))
