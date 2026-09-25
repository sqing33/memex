import sys
sys.path.insert(0, "src")
from memex.contract import report_contract, analysis_contract, validate_report, render_report
c = report_contract()
print("axes", [a["key"] for a in c["axes"]])
print("kinds", c["card_kinds"])
print("anchors keys", sorted(c["anchors"]))
ac = analysis_contract()
print("schema_url", ac["schema_url"])
print("schema defs", sorted(ac["schema"]["$defs"])[:3])
print("checklist n", len(c["checklist"]))

# 构造一份「最小合法」报告（英文 mechanism/intent），跑一遍校验
def ev(p="a.py", s=1, e=1):
    return {"path": p, "start_line": s, "end_line": e}
long = "Runtime control flow explanation that is definitely long enough to satisfy the forty unit threshold easily here and then some more words to push the total number well past forty information units for sure yes indeed really quite long now."
feat = {
    "key": "bounded-retry-engine",
    "title": "有界重试",
    "summary": "上游抖动时用上限截断的指数退避重试，超出预算即失败。",
    "principles": {
        "runtime_control_flow": long,
        "data_flow": long,
        "state_lifecycle": long,
        "failure_recovery": long,
        "concurrency_timing": long,
    },
    "evidence": [ev()],
    "cards": [
        {"kind": "mechanism", "title": "退避增长", "summary": "退避按 base*factor^attempt 增长并截断。",
         "reusable": True, "mechanism_desc": "Backoff grows multiplicatively and is clamped by a cap after each attempt.",
         "evidence": [ev()]},
        {"kind": "snippet", "title": "计算退避", "summary": "计算第 attempt 次退避时长。",
         "reusable": True, "mechanism_desc": "Compute backoff duration for the given attempt with jitter applied after clamping.",
         "evidence": [ev()], "code_spans": [ev()], "code": "x"},
    ],
    "intent": "When I need bounded exponential retry for flaky upstream calls.",
}
report = {
    "schema_id": "memex/report/1",
    "one_liner": "一个演示用的重试引擎报告。",
    "characteristics": [{"title": "有界退避", "detail": "用上限截断的指数退避处理上游抖动。", "evidence": [ev()]}],
    "entry_points": [{"path": "a.py", "role": "main 入口", "kind": "main"}],
    "features": [feat, dict(feat, key="b"), dict(feat, key="c")],
    "cross_feature_risks": [{"title": "共享状态", "detail": "多个功能共享同一计数器可能竞态。", "evidence": [ev()]}],
}
r = validate_report(report)
print("is_valid(no repo_root)", r["is_valid"], "problems", [p["code"] for p in r["problems"]])
md = render_report(report)
print("H2", [l for l in md.splitlines() if l.startswith("## ")])
print("md bytes", len(md))
