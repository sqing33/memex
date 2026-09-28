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
SCHEMA_VERSION = "2"

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

# 卡片 mechanism_desc（建向量的机制描述）的最低单元数。
# 注意：它与 MIN_PRINCIPLE_UNITS 不是同一个字段的阈值——report.schema.json 对
# mechanism_desc 用的是 minLength:20（字符），validator.py 用 units < 20 判
# principle_too_short。这里显式给常量，避免 vibecraft 快速导入路径把两个阈值
# 混淆成 40（deployment.md C5 的措辞自相矛盾，以 schema/validator 为准）。
MIN_MECHANISM_UNITS = 20

# —— 契约占位符（一律视为「空」，docs/report-contract.md §3）——
BLANK_PLACEHOLDERS = ("n/a", "na", "无", "未知", "待补充", "todo", "tbd", "???")

# —— 五原理轴雷同门禁（G23）——
# 阈值取「三轴以上雷同即拒」：五轴是五个维度，三轴雷同就没有信息量了，
# 而四轴雷同 / 五轴全同自然也落在同一条规则内。
# 判据是 *distinct* 轴数 <= MAX_AXIS_REUSE（validator.py 的 len(set(sigs))）：
# distinct <= 3 里天然包含「三轴雷同」这一档（a=a=a+b+c 时 distinct=3）。
# 别改小成 2——那会放行「三轴雷同」，与 report-contract.md §3「三轴以上雷同即拒」冲突。
MAX_AXIS_REUSE = 3
# 判定雷同前先剥掉的标点与空白（反引号用 chr 拼，避免源码里出现裸反引号）。
AXIS_NEGLECT = (" \t\r\n"
    ".,;:!?、，。；：！？「」『』（）"
    "()【】[]{}<>《》…—"
    "～~·|/" + chr(92) + chr(34) + "@#$%^&*+=_" + chr(96) + chr(39) + "rq’‘“”")

# —— 召回融合（docs/tech-design.md §2.5）——
RRF_K = 60
KIND_PRIOR: dict[str, float] = {
    "card": 1.0,
    "feature": 0.9,
    "pattern": 0.6,
    "report_section": 0.4,
}

# —— 检索重排（G25 实测定案；docs/decisions.md `G25 rerank 选型`）——
# 15 探针真模型实测：ms-marco-MiniLM-L-6-v2 有害（top-1 10/15 降到 4/15），
# BAAI/bge-reranker-base 有效（top-1 10/15 提到 13/15，MRR 0.776 提到 0.910）。
# 默认 off 是实测结论不是省事：开之前先看上面那两行数字。
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"
RERANK_MAX_LENGTH = 512
