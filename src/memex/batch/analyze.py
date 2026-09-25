"""服务端 batch 直跑（gaps.md G21）——V1 不实现，排期 V5。

`analyze_repo` 是「服务端 LLM 批量分析」路径的入口。G21 决定 V1–V4 只用
agent 工具流（begin_analysis → take_evidence → commit_analysis），batch 需要与
V5 的「规模」目标同批落地，因此本模块在 V1 只保留入口并直接拒绝，
避免调用方误以为它可用而绕开 agent 驱动架构。
"""

from __future__ import annotations

from typing import Any

from ..core import MemexError


def analyze_repo(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """V1 拒绝：batch 直跑排期 V5，请改走 agent 工具流。

    无论入参如何都抛 MemexError，错误码为封闭集里的 unsupported，
    并在 details.planned 标注排期，便于调用方做能力探测。
    """
    raise MemexError(
        "unsupported",
        "analyze_repo（服务端 batch 直跑）排期 V5，V1 请走 agent 工具流",
        {"planned": "V5"},
    )
