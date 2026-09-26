"""服务端 batch 直跑（gaps.md G21）——已定案**不实现**，本模块只保留拒绝入口。

`analyze_repo` 是「服务端 LLM 批量分析」路径的入口。`decisions.md` G21 写侧补记已定案：
服务端 LLM 批量分析正是把判断力搬回 server，违反 AGENTS.md 第一原则
「机械活留在 server，判断力交给 agent」；且 server 端没有 LLM 凭据、
没有提示词版本管理、没有成本上限。

所以本模块**不是「排期 V5 的占位」，而是「决定不做的显式拒绝」**——
错误码用封闭集里的 `unsupported`，让调用方的能力探测如实得到「这条路没有」，
而不是拿到一个跑不通的半成品。要真做，形态必须是 `MEMEX_BATCH_LLM` 显式配置
+ 每次调用记 `analyst=batch/<模型名>` + 独立成本上限开关 + 默认关。
"""

from __future__ import annotations

from typing import Any

from ..core import MemexError


def analyze_repo(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """无条件拒绝：请改走 agent 工具流。

    无论入参如何都抛 MemexError，错误码为封闭集里的 unsupported，
    便于调用方做能力探测。
    """
    raise MemexError(
        "unsupported",
        "analyze_repo（服务端 batch 直跑）已决定不实现：判断力属于 agent，请走 agent 工具流（begin_analysis → get_evidence_pack → validate_report → commit_report）",
        {"decided": "not_implemented", "ref": "decisions.md G21"},
    )
