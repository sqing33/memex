"""全局常量与封闭枚举。

这些值必须与 docs/ 下的契约文档保持同源：
- 报告契约：docs/report.schema.json (x-contract-id / x-contract-version)
- 工具签名：docs/mcp-tools.md §1（错误码 / detail / next_step）
- 数据库：docs/tech-design.md §2.2（schema_version）
改动前先改 docs（AGENTS.md 二）。
"""

from __future__ import annotations

import re

# —— 契约标识（docs/report.schema.json）——
CONTRACT_ID = "memex/report/1"
CONTRACT_VERSION = "1"

# —— 数据库 schema 版本（meta.schema_version，docs/tech-design.md §2.2）——
SCHEMA_VERSION = "1"

# —— 五个原理轴（docs/report-contract.md §3）——
PRINCIPLE_KEYS: tuple[str, ...] = (
    "runtime_control_flow",
    "data_flow",
    "state_lifecycle",
    "failure_recovery",
    "concurrency_timing",
)

# —— 五类卡片（docs/report-contract.md §4）——
CARD_KINDS: tuple[str, ...] = ("mechanism", "snippet", "skeleton", "gotcha", "decision")
# kind ∈ 此集合时 code_spans 必填且非空；其余 kind 出现 code_spans 即报错
CODE_REQUIRED_KINDS: tuple[str, ...] = ("snippet", "skeleton")

# —— 召回 chunk 类型（docs/tech-design.md §2.2 chunks.kind）——
CHUNK_KINDS: tuple[str, ...] = ("feature", "card", "pattern", "report_section")

# —— detail 三档（docs/mcp-tools.md §1.2）——
DETAIL_LEVELS: tuple[str, ...] = ("brief", "normal", "full")
DEFAULT_DETAIL = "normal"

# —— 错误码封闭集（docs/mcp-tools.md §1.4，共 10 个，不新增同义码）——
ERROR_CODES: tuple[str, ...] = (
    "invalid_argument",
    "not_found",
    "invalid_report",
    "conflict",
    "stale_repo",
    "fetch_failed",
    "unsupported",
    "rate_limited",
    "disabled",
    "internal",
)

# —— 工具类别（docs/mcp-tools.md §3，G28 禁用清单的机械来源）——
TOOL_CATEGORIES: tuple[str, ...] = ("read", "write", "network", "destructive")
DEFAULT_TOOLS = "read,write,network"  # destructive 从不默认开

# —— 分析生产者与报告状态（docs/tech-design.md §2.2）——
PRODUCERS: tuple[str, ...] = ("agent", "batch")
ANALYSIS_STATUS: tuple[str, ...] = ("drafting", "committed", "failed", "stale")

# —— 会话状态机（docs/tech-design.md §2.6）——
SESSION_STATES: tuple[str, ...] = (
    "begun",
    "evidence_taken",
    "drafting",
    "validated",
    "committed",
    "abandoned",
)

# —— repo 抓取来源（docs/mcp-tools.md RepoSummary.source）——
REPO_SOURCES: tuple[str, ...] = ("clone", "tar", "upload", "local")

# —— 段落长度按「信息单元」计（docs/report-contract.md §3，G13）——
REGEX_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
REGEX_WORD = re.compile(r"[A-Za-z0-9_]+")
MIN_PRINCIPLE_UNITS = 40
MIN_SUMMARY_UNITS = 12
MIN_INTENT_UNITS = 5

# —— 契约占位符（一律视为「空」，docs/report-contract.md §3）——
BLANK_PLACEHOLDERS = ("n/a", "na", "无", "未知", "待补充", "todo", "tbd", "???")

# —— 召回融合（docs/tech-design.md §2.5）——
RRF_K = 60
KIND_PRIOR: dict[str, float] = {
    "card": 1.0,
    "feature": 0.9,
    "pattern": 0.6,
    "report_section": 0.4,
}
