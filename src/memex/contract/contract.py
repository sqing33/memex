"""契约的三处同源之一：向 agent 下发的那一份（begin_analysis / help 共享）。

三份不漂移（docs/report-contract.md 开头）：
  - docs/report.schema.json  —— 权威结构（本模块 import 其内嵌副本）
  - docs/report-contract.md  —— 散文解释
  - 本模块 + validator.render —— 执行与渲染

report_contract()  给「人/agent 读的精简契约」（HelpPage 与 T5 共用）
analysis_contract() 给 T5 begin_analysis 的 contract 对象（id + url + 内联 schema + anchors）
"""
from __future__ import annotations

from typing import Any

from ..constants import (
    CARD_KINDS,
    CODE_REQUIRED_KINDS,
    CONTRACT_ID,
    CONTRACT_VERSION,
    MIN_INTENT_UNITS,
    MIN_PRINCIPLE_UNITS,
    MIN_SUMMARY_UNITS,
    PRINCIPLE_KEYS,
)
from .schema_data import SCHEMA

# schema 的 $id 就是它的规范 URL（docs/report.schema.json 里声明）
SCHEMA_URL: str = SCHEMA.get("$id", "https://memex.local/contract/report.schema.json")

# 五轴中文名（与 render.AXIS_LABELS 同源，避免两处漂移，这里独立成常量供 help 用）
AXIS_TITLES: dict[str, str] = {
    "runtime_control_flow": "运行/控制流",
    "data_flow": "数据流",
    "state_lifecycle": "状态生命周期",
    "failure_recovery": "失败与恢复",
    "concurrency_timing": "并发与时序",
}

# 每种卡片 kind 的「定义 + 正例 + 反例」（B6）。直接取自 schema 的 x-card-kind-anchors——单一事实源。
ANCHORS: dict[str, Any] = SCHEMA.get("x-card-kind-anchors", {})

# 分析步骤清单（T5 下发给 agent；也用于文档化 analyze flow）
ANALYSIS_CHECKLIST: list[str] = [
    "读证据包（目录树 / 入口点 / 符号清单），先形成「这仓是什么、靠哪几个功能运转」的判断。",
    "挑出 >=3 个功能级知识单元；每个功能必须能用一句英文 intent 说明「什么需求会想借鉴它」。",
    "为每个功能逐条填满五个原理轴（运行/控制流、数据流、状态生命周期、失败与恢复、并发与时序），每轴 >= 40 信息单元，禁止占位符。",
    "为每个功能写 >=1 张卡；snippet/skeleton 必须带真实 code_spans（服务端会用真实文件重切，逐字比对，不一致即 code_mismatch）。",
    "所有 evidence 的 path 必须是仓库里真实存在的文件，行号必须在文件行数范围内。",
    "mechanism_desc 与 intent 必须写英文；summary / 原理轴 / title 用你的母语。",
    "先调 validate_report 自查；is_valid=true 后再调 commit_report。",
]


def kind_code_rule() -> dict[str, bool]:
    """每种 kind 是否必须带 code_spans。"""
    return {k: (k in CODE_REQUIRED_KINDS) for k in CARD_KINDS}


def counting_rule() -> dict[str, Any]:
    return {
        "rule": "units = CJK/仮名/ハングル 字符数 + 拉丁/数字 连续串数",
        "min_principle_units": MIN_PRINCIPLE_UNITS,
        "min_summary_units": MIN_SUMMARY_UNITS,
        "min_intent_units": MIN_INTENT_UNITS,
    }


def report_contract() -> dict[str, Any]:
    """给 agent 读的精简契约（help(report-contract) 与 T5 的 checklist 基础）。"""
    return {
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "axes": [{"key": k, "title": AXIS_TITLES[k]} for k in PRINCIPLE_KEYS],
        "card_kinds": list(CARD_KINDS),
        "kind_code_required": kind_code_rule(),
        "counting": counting_rule(),
        "language_rule": {
            "vector_text_must_be_english": ["mechanism_desc", "intent"],
            "prose_must_be_native": ["summary", "principles.*", "title"],
            "tags": "lowercase english identifiers ^[a-z0-9][a-z0-9._-]*$",
        },
        "headings": [
            "Project Characteristics and Signature Implementations",
            "Executive Principle Summary",
            "Feature Principle Analysis",
            "Cross-feature Coupling and System Risks",
        ],
        "anchors": ANCHORS,
        "checklist": list(ANALYSIS_CHECKLIST),
    }


def analysis_contract() -> dict[str, Any]:
    """T5 begin_analysis 下发的 contract 对象：id + schema_url + 内联 schema + anchors。"""
    return {
        "contract_id": CONTRACT_ID,
        "schema_url": SCHEMA_URL,
        "schema": SCHEMA,
        "anchors": ANCHORS,
    }
