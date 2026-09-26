# -*- coding: utf-8 -*-
"""C11 改写后新增的渲染测试（单仓详情 + 跨仓模式页）。

单独放一个文件是因为这部分测试共用两个大种子：一棵完整的 report_json（报告契约
六键齐全）与 patterns/pattern_members 行。放进 test_site_export.py 会把「文件名
安全」那组测试的上下文冲淡。
"""
from __future__ import annotations

import json
from pathlib import Path

from test_site_export import _read, _seed_analysis, _seed_repo  # noqa: F401

from memex.site.render import export_site


def _ev(path, start, end, symbol, note):
    return {"path": path, "start_line": start, "end_line": end, "symbol": symbol, "note": note}


REPORT = {
    "schema_id": "memex/report/1",
    "one_liner": "一个用来验证站点渲染的报告",
    "characteristics": [
        {
            "title": "招牌实现",
            "detail": "正文里应当出现的一句特征描述，用来证明 characteristics 渲染了。",
            "evidence": [_ev("src/a.py", 1, 9, "f", "特征证据")],
        }
    ],
    "entry_points": [
        {"kind": "main", "path": "src/main.py", "role": "程序主入口，渲染时应出现"},
    ],
    "features": [
        {
            "key": "retry-loop",
            "title": "重试循环",
            "summary": "功能摘要应当出现，用来证明 features 渲染了。",
            "intent": "Retry a flaky call with bounded attempts and exponential backoff.",
            "principles": {
                "runtime_control_flow": "运行/控制流正文：入口如何进循环、什么时候跳出。",
                "data_flow": "数据流正文：attempt 计数如何流经每次调用。",
                "state_lifecycle": "状态生命周期正文：退避状态何时创建与销毁。",
                "failure_recovery": "失败与恢复正文：可重试错误与终态错误如何区分。",
                "concurrency_timing": "并发与时序正文：单次调用内的时序约束。",
            },
            "evidence": [_ev("src/retry.py", 12, 40, "run", "功能证据")],
            "cards": [
                {
                    "kind": "mechanism",
                    "title": "上限截断的指数退避",
                    "summary": "卡片摘要应当出现。",
                    "mechanism_desc": "Backoff grows multiplicatively and is clamped by a cap before sleeping.",
                    "reusable": True,
                    "tags": ["backoff", "retry"],
                    "evidence": [_ev("src/retry.py", 20, 28, "compute_backoff", "卡片证据")],
                }
            ],
        }
    ],
    "cross_feature_risks": [
        {
            "title": "跨功能耦合",
            "detail": "风险正文应当出现，用来证明 cross_feature_risks 渲染了。",
            "evidence": [_ev("src/a.py", 3, 5, "g", "风险证据")],
        }
    ],
}


def _seed_full_analysis(paths, repo_id, *, aid, report=None, md="# 原始报告", **cols):
    _seed_analysis(
        paths,
        repo_id,
        aid=aid,
        report=json.dumps(report if report is not None else REPORT, ensure_ascii=False),
        md=md,
        **cols,
    )
