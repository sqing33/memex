"""help 工具的 5 个固定 topic（B10）。

这些内容与 report.schema.json / validate_report 同源：散文（本文件）、结构校验
（contract.validator）、下发给 agent 的契约（contract.contract）三处不漂移。

每个 topic：{title, markdown, related:[topic...]}。
"""
from __future__ import annotations

from typing import Any

from ..contract import contract as contract_mod

_NL = chr(10)
_AXES = contract_mod.AXIS_TITLES
_CHECKLIST = contract_mod.ANALYSIS_CHECKLIST


def _axes_md() -> str:
    return _NL.join("- " + k + "：" + v for k, v in _AXES.items())


def _checklist_md() -> str:
    return _NL.join(str(i + 1) + ". " + s for i, s in enumerate(_CHECKLIST))


_ANALYZE_FLOW = """## 一次分析怎么做（agent 驱动）

1.  **登记仓库**：`fetch_repo`（公开仓）或 `upload_repo_bundle`（离线/私有/内网）。
    记下返回的 `repo_id` 与 `commit_sha`。
2.  **取证据包**：`get_evidence_pack`（目录树 + 入口点 + 符号清单，**不含代码切片**）。
3.  **开会话**：`begin_analysis`，一次性拿到契约 + 步骤清单 + `session_id`。
    会话绑定 `commit_sha`；提交前若克隆已变，会报 `stale_repo`。
4.  **读代码**：用本地克隆（`repo_path`）或 `request_repo_bundle` 取回本地读，
    必要时用 `read_file_slice` 按需取真实字节。**别整文件塞**。
5.  **写报告**：挑出 >=3 个功能，每个功能填五轴原理 + >=1 张卡片；`mechanism_desc` 与
    `intent` 必须写英文，`summary`/`title`/原理轴用母语。
6.  **自查**：`validate_report`（幂等、可无限次调用；知识库只读）。**带上 `session_id`**——
    `is_valid=true` 时它会把会话推进到 `validated`，第 7 步才能提交。`is_valid=false` 时 `ok` 仍为
    `true`——「校验器工作了」和「报告通过了」是两件事。
7.  **落库**：`commit_report`。硬门禁：`code_mismatch` 必须为 0；同幂等键内容一致是 no-op；
    内容不同需 `force:true`。落库后自动建 chunk、算向量、跑聚类（`min_repos=2`）。

**召回侧**：`search_implementations` -> `get_card` -> （可选）`list_patterns`。

### 步骤清单（begin_analysis 也会下发同一份）
@@CHECKLIST@@
"""


_REPORT_CONTRACT = """## memex/report/1 契约速览

报告是单个 JSON 对象，顶层字段：

- `schema_id`（常量 `"memex/report/1"`）
- `one_liner`：一句话概括
- `characteristics`：项目特征（1–8 条，每条带 `evidence`）
- `entry_points`：入口点（>=1）
- `features`：功能（>=3，每个含五轴 + 卡片 + 英文 `intent`）
- `cross_feature_risks`：跨功能风险（>=1）

### 五个固定原理轴（每个 >=40 单元）
@@AXES@@

> 单元（unit）计数规则：CJK 字符各计 1，拉丁/数字连续串各计 1。上限为「至少」，越丰富越好。

### 语言分层（§5）
- **必须英文**（进向量、跨语言前提）：`mechanism_desc`、每个功能的 `intent`。
- **母语（中文）**：`title`、`summary`、五轴文本、`one_liner`、`tags`。
- 违反 -> `unicode_language_mismatch`。

### 卡片代码要求
- `snippet` / `skeleton`：**必须**带非空 `code_spans`（真实路径 + 行号），否则 `code_required`。
- `mechanism` / `gotcha` / `decision`：**禁止** `code_spans`，否则 `code_forbidden`。
- agent 给的 `code` 只用于**比对**（重切覆盖），不落库；不一致 -> `code_mismatch`（硬门禁）。

### 问题码（validate_report 的 problems[].code，闭集）
unknown_field / missing_field / too_few_features / duplicate_feature_key /
blank_principle / principle_too_short / axis_reuse / bad_evidence_path / line_out_of_range /
code_span_mismatch / code_required / code_forbidden / bad_enum / unicode_language_mismatch

每条问题都带 `where`（JSON Pointer，如 `/features/2/cards/0/code`）以便精确定位。

### 报告 Markdown 的 4 个固定 H2
1. Project Characteristics and Signature Implementations
2. Executive Principle Summary
3. Feature Principle Analysis
4. Cross-feature Coupling and System Risks
"""


_CARD_KINDS = """## 五类卡片怎么区分

每类都有定义 + 正例 + 反例（`begin_analysis` 下发的 `contract.anchors` 同源）。

### mechanism（机制）
- **定义**：解释「怎么运转」的一句话机制，不含代码。
- **正例**：Backoff grows multiplicatively (base × factor^attempt) and is clamped by a configurable cap before each sleep; jitter is applied after clamping to avoid synchronized retries.
- **反例**：见 internal/retry/backoff.go 的 computeBackoff 函数。
- 代码要求：无。

### snippet（可复用代码片段）
- **定义**：可直接粘贴复用的真实代码片段，必须配 `code_spans`。
- **正例**：computeBackoff 的 15 行实现，code_spans 指向 src/retry.py 12-30。
- **反例**：只给描述、行号对不上。
- 代码要求：**必须** `code_spans`。

### skeleton（骨架）
- **定义**：结构骨架，省略次要实现细节。
- **正例**：RetryEngine.run(fn) 的骨架（含关键分支与调用顺序）。
- **反例**：整文件贴进来、缺关键分支。
- 代码要求：**必须** `code_spans`。

### gotcha（坑）
- **定义**：必须知道的隐含前提 / 反直觉陷阱。
- **正例**：jitless——clamp 必须在 jitter 之前；sleep 必须可被取消。
- **反例**：写「注意异常处理」这类空话。
- 代码要求：无。

### decision（取舍）
- **定义**：选择了 A 而非 B，以及为什么。
- **正例**：用「上限截断的指数退避」而非「固定间隔」，因为要应对服务端过载且避免同步重试。
- **反例**：只写「用了指数退避」不加理由。
- 代码要求：无。

> 只有 `reusable:true` 的卡片进入聚类与召回 chunk。
"""


_SEARCH_USAGE = """## 召回怎么用

`search_implementations` 走**三通道 RRF** 融合（k=60，等权）：

1.  **向量通道**：在卡片 `mechanism_desc`（英文、语言中立）上做语义召回——这是跨语言的前提。
2.  **关键词通道**：SQLite FTS5（trigram）BM25，查询需 >=3 字符。
3.  **子串通道**：`LIKE %q%`，兜底短查询 / 精确符号。

关键词与子串两个通道都是**两级降级**：先整串连续匹配；整串没有命中时，
降级为 **trigram OR 匹配**（按位置切 3 字符窗口）。所以整句自然语言查询
也能命中，不必自己拆成关键词。响应的 `channels` 会如实告诉你每个通道
实际参与投票的条数——若 keyword/substr 长期是 0，说明库里还没有可命中的文本。

融合后按 `kind_prior` 加权：card 1.0 > feature 0.9 > pattern 0.6 > report_section 0.4。

### 参数
- `query`（必填）：自然语言或符号名。
- `limit`：默认 8。
- `repo_id` / `language` / `kind`：过滤。
- `detail`：`brief` | `normal`（默认）| `full`；`full` 才回真实 `code` 切片。
- `rerank`：可选二次排序（默认关）。

### 读结果
每条含 `score`、`matched_by`（命中通道）、`chunk_kind`、`excerpt`、`card_id`；
`normal` 起附 `mechanism_desc`、`source`（证据锚点）；`kind=pattern` 时附 `repos` 与
`pattern_key`。拿到 `card_id` 后用 `get_card` 取完整卡片（含证据与代码）。

### 提示
- 跨语言找「同类实现」：直接用中文/英文描述机制词，向量通道负责跨语言。
- 想要可复用代码：过滤 `kind=snippet`。
"""


_PATTERNS = """## 模式（pattern）是什么

**模式 = 跨仓库的相似实现聚类**，是 memex 的核心价值（C 召回 + D 注入）。

### 怎么形成
- 在**可复用卡片**（`reusable:true`）的 `mechanism_desc` 向量上做相似度聚类
  （默认阈值 0.60，贪心 complete-linkage）。complete-linkage 要求新成员与**簇内每一个**
  已有成员都达标，不靠传递闭包拉人——连通分量会在 0.45 以下把 22/27 张不相关卡
  吸进一个假模式（实测见 decisions.md G24）。
- **只保留覆盖 >=2 个不同来源的簇**（`min_repos=2`）。来源按
  `COALESCE(fork_of, identity_key)` 去重——fork 不算独立来源，避免同一血统刷计数。
- 每次 `commit_report` 后、以及 `forget_*` 后自动重跑聚类（幂等）。

### 怎么用
- `list_patterns`：列模式，含 `repo_count` / `card_count` / `languages` / `repos` / `intents`。
- `search_implementations` 命中 `kind=pattern` 时，结果带 `pattern_key` 与成员仓库列表。
- `get_card` 的卡片会附它所属的 `patterns`。

### 意义
一个模式被多个独立仓库重复实现 => 它是**经过实践检验的通用解法**，值得跨项目借用。
"""


HELP_TOPICS: dict[str, dict[str, Any]] = {
    "analyze-flow": {
        "title": "如何完成一次 agent 驱动的仓库分析",
        "markdown": _ANALYZE_FLOW.replace("@@CHECKLIST@@", _checklist_md()),
        "related": ["report-contract", "card-kinds"],
    },
    "report-contract": {
        "title": "memex/report/1 报告契约",
        "markdown": _REPORT_CONTRACT.replace("@@AXES@@", _axes_md()),
        "related": ["card-kinds", "analyze-flow"],
    },
    "card-kinds": {
        "title": "五类卡片的定义与正反例",
        "markdown": _CARD_KINDS,
        "related": ["report-contract", "search-usage"],
    },
    "search-usage": {
        "title": "三通道 RRF 召回的使用方法",
        "markdown": _SEARCH_USAGE,
        "related": ["patterns", "card-kinds"],
    },
    "patterns": {
        "title": "跨仓库模式聚类",
        "markdown": _PATTERNS,
        "related": ["search-usage", "analyze-flow"],
    },
}

__all__ = ["HELP_TOPICS"]
