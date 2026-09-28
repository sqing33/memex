"""自造一份完全合法的报告夹具（E23：不再依赖被 gitignore 的产物）。

历史问题：test_validator_axes.py / test_quality_metrics.py 写死
`Path("/workspace/memex/reports/axum_report.json")`，而 `reports/` 在 .gitignore
第 18 行——仓库里根本没有这份文件。CI / 他机 / 清库后必然 collection error 或
5 连败，报的还是「文件不存在」而不是「质量指标算错了」。夹具必须自带。

夹具的约束（必须能过**完整契约校验器**，否则测的就不是指标/门禁本身）：
- features >= 3；每 feature 五原理轴 >= 40 单元且互不相同（G23 axis_reuse）；
- 每 feature 有 evidence + intent（英文，>= 5 单元）；每卡有 evidence；
- 卡片 kind=snippet 必须给 code_spans + code，且 code 与服务端重切一致——
  所以引用文件由 write_repo() 现写，行号才可能对得上。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _axes import PRIN  # noqa: E402

for _k in PRIN:
    assert len(PRIN[_k].split()) >= 40, f"轴 {_k} 必须 >= 40 单元"

# 与服务端重切逐字一致：validator._normalize 会比 code 与切片文本。
_RETRY_CODE = "\n".join([
    "def fetch(url):",
    "    for attempt in range(3):",
    "        resp = post(url)",
    "        if resp.ok:",
    "            return resp",
    "    return None",
])
_RETRY_LINES = len(_RETRY_CODE.splitlines())


def write_repo(root: Path) -> Path:
    """把报告引用的代码文件写进 root，返回 root（当作 validate_report 的 repo_root）。"""
    (root / "engine.py").write_text(_RETRY_CODE + "\n", encoding="utf-8")
    (root / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    return root


def _feature(i: int) -> dict:
    return {
        "key": f"retry-loop-{i}",
        "title": f"Retry Loop {i}",
        "summary": "A bounded retry helper shared by every remote call in this module and reused widely.",
        "principles": dict(PRIN),
        "evidence": [{"path": "engine.py", "start_line": 1, "end_line": _RETRY_LINES}],
        "intent": "Reuse a bounded retry loop for resilient remote calls.",
        "cards": [{
            "kind": "snippet",
            "title": f"Bounded retry loop {i}",
            "summary": "Retries a failed remote call a few times before giving up for good.",
            "reusable": True,
            "mechanism_desc": (
                "The call site retries a failed remote request with bounded "
                "exponential backoff, and only gives up after the final attempt "
                "has also failed on the server side."
            ),
            "evidence": [{"path": "engine.py", "start_line": 1, "end_line": _RETRY_LINES}],
            "code_spans": [{"path": "engine.py", "start_line": 1, "end_line": _RETRY_LINES}],
            "code": _RETRY_CODE,
        }],
    }


def legal_report() -> dict:
    """返回一份完全合法的报告（调用方自己 deepcopy 后改，避免相互污染）。"""
    return {
        "schema_id": "memex/report/1",
        "one_liner": "A retry engine",
        "characteristics": [
            {"title": "Resilient",
             "detail": "Uses bounded retries across all network calls in the system.",
             "evidence": [{"path": "engine.py", "start_line": 1, "end_line": 1}]}
        ],
        "entry_points": [{"path": "main.py", "role": "Entry point for the CLI", "kind": "main"}],
        "cross_feature_risks": [
            {"title": "Risk of shared state",
             "detail": "Shared mutable state between features is unsafe.",
             "evidence": [{"path": "engine.py", "start_line": 1, "end_line": 1}]}
        ],
        "features": [_feature(0), _feature(1), _feature(2)],
    }
