# 报告契约（散文版）—— `memex/report/1`

> 这是**散文契约**，供人和 agent 阅读：它讲「为什么这样要求」。
> 机器可读版本在 [`report.schema.json`](report.schema.json)；工具签名在 [`mcp-tools.md`](mcp-tools.md)。
> 三份同源：`schema.json` 是权威结构，本文是它的解释，`validate_report` 按 schema 执行。
> 通过 `help("report-contract")` 也能在会话里取到本文。

---

## 1. 这份契约要解决什么

memex 的价值是「**跨仓库、跨语言的实现复用**」。要做到这一点，报告必须满足一个硬条件：

> **两个不同仓库里、用不同语言写的同一个机制，读起来要长得像。**

所以报告不是「写得好看就行」，而是**结构化到可比较、可检索、可跨语言对齐**。契约就是这条底线。

一句话概括：**固定骨架 + 强制证据 + 中英分层的语言规则**。

---

## 2. 固定骨架（4 个 H2）

一份报告渲染成 Markdown 后，恰好有 4 个二级标题，顺序固定：

1. `## Project Characteristics and Signature Implementations`
2. `## Executive Principle Summary`
3. `## Feature Principle Analysis`
4. `## Cross-feature Coupling and System Risks`

对应 JSON 的 `characteristics` / （由 features 汇总）/ `features` / `cross_feature_risks`。
固定骨架 = 跨仓报告可比 = 聚类有意义。

**顶层字段**（全部必填，多一个字段就拒收）：

| 字段 | 含义 |
|---|---|
| `schema_id` | 固定 `"memex/report/1"`，原样回填 |
| `one_liner` | 一句话：这仓库是什么、做什么 |
| `characteristics` | 1–8 条「招牌实现」 |
| `entry_points` | 程序入口与装配点 |
| `features` | **≥ 3** 个功能级知识单元 |
| `cross_feature_risks` | ≥ 1 条跨功能耦合/系统风险 |

> **未知字段一律拒绝**（A4）。不是「忽略」，是**报错**——因为未知字段往往意味着
> agent 在与旧版契约对话，或者写错了字段名；静默忽略会让错误的数据入库。

---

## 3. 一个「功能」= 五个原理轴 + 卡片 + 意图探针

`features[]` 的每一项（`Feature`）必须包含：

- `key`：kebab-case（`^[a-z0-9]+(-[a-z0-9]+)*$`），**仓内唯一**。如 `bounded-retry-engine`。
- `title` / `summary`：功能叫什么、解决什么问题、外部行为是什么。
- `principles`：**五轴，全必填**（见下）。
- `evidence`：≥ 1 条证据（必须真实存在）。
- `cards`：≥ 1 张卡。
- `intent`：**一句英文**意图探针（见 §5）。

### 五个原理轴（固定，不可增删）

| 轴 | 中文 | 要回答的问题 |
|---|---|---|
| `runtime_control_flow` | 运行/控制流 | 控制如何流转：谁调用谁、何时触发、循环/递归边界、提前返回条件 |
| `data_flow` | 数据流 | 数据从哪来、经过什么结构、到哪去、在哪被转换/序列化 |
| `state_lifecycle` | 状态生命周期 | 有哪些状态、谁创建/变更/销毁、持久化在哪、何时失效 |
| `failure_recovery` | 失败与恢复 | 失败如何被检测、降级、重试、回滚、上报 |
| `concurrency_timing` | 并发与时序 | 并发模型、锁/队列/超时/顺序保证、竞态如何避免 |

**为什么固定这五轴**：它们是「任何实现都躲不开的五个问题」。固定下来，A 仓的
`bounded-retry-engine` 与 B 仓的 `runWithRetry` 才能逐轴对照——这是「从已有项目借鉴」的读法。

**禁止**：任何一轴写 `N/A` / `NA` / `无` / `未知` / `待补充` / `TODO`。写不出说明这个功能
还没被真正理解，**应当先读懂再提交**，而不是用占位符糊过去。
> 校验器把上述占位符都当「空」处理，与「缺字段」同等对待。

**五轴必须互不相同（G23）**：把同一句话抄五遍不叫分析，那会让五原理轴在检索与
聚类上完全失去信息量——而「逐轴对照两个仓库」正是读法本身。校验器按
**去标点、去空白后的归一化文本**统计 distinct 轴数，**三轴以上雷同**即判 `axis_reuse`
（所以四轴雷同、五轴全同都会被拒）。只差标点或空格的改写不算不同——
那种改写是蒙混，不是回答了另一个维度的问题。

### 段落长度（G13 的语言自适应计法）

阈值不按「字符数」算，按**信息单元数**（`units`）算：

```
units = (CJK / 仮名 / ハングル 的字符数) + (拉丁字母 / 数字的连续串数)
regex_cjk  = [\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]
regex_word = [A-Za-z0-9_]+
```

| 位置 | 最低单元数 |
|---|---|
| 每个原理轴段落 | **40** |
| `summary`（功能/卡片） | 12 |
| `intent` | 5 |
| 卡片 `mechanism_desc` | 20 |

理由：中文 40 字与英文 40 词的信息量差近一倍，按字符算阈值对两种语言不等价。
`min_principle_units = 40` 是 recall 旧实现 `min_principle_chars = 40` 的等价换算。
> `mechanism_desc` 单独一行：它是**建向量的机制描述**（`report.schema.json` 里写作
> `minLength: 20`，按字符；`validator.py` 按单元 `count_units(mech) < 20` 判
> `principle_too_short`）。与「每个原理轴段落 = 40」**不是同一个字段**，
> 快速导入路径（`import_/vibecraft.py`）也不得混用两者（`constants.MIN_MECHANISM_UNITS`）。

**这些阈值待 V1/V2 跑出分布后按实测调整**（见 `gaps.md` G23）。

---

## 4. 卡片（`Card`）——五类，各有定义与正反例

卡片是「可被后来者借鉴的最小单位」。`reusable: true` 的卡片才会参与聚类与召回建 chunk。

| `kind` | 一句话定义 | 是否必须带代码（`code_spans`） |
|---|---|---|
| `mechanism` | 讲「怎么运转」，不含代码 | **不允许出现** |
| `snippet` | 可直接粘贴的真实片段 | **必须非空** |
| `skeleton` | 签名 + 关键分支的骨架 | **必须非空** |
| `gotcha` | 坑与隐含前提 | 不允许出现 |
| `decision` | 选了 A 而非 B 及理由 | 不允许出现 |

> `code_spans` 用 `allOf/if-then-else` 强制：`kind ∈ {snippet, skeleton}` 时必须 ≥1 段；
> 其余 kind **出现即报错**（不是忽略）。段与段之间渲染时插 `// … (elided) …`。

### 4.1 `mechanism`

- **定义**：解释「怎么运转」的一句话机制，不含代码。**同一机制的不同语言实现应落成同一条。**
- **正例**：
  > Backoff grows multiplicatively (base × factor^attempt) and is clamped by a configurable cap
  > before each sleep; jitter is applied after clamping to avoid synchronized retries.
- **反例**（明令禁止）：
  > 见 `internal/retry/backoff.go` 的 `computeBackoff` 函数。
  > ✗ 这是**定位**而非机制——它没说怎么运转，换一个仓就没法对齐。

### 4.2 `snippet`

- **定义**：可直接粘贴复用的真实代码片段，自包含、可独立读懂，必须配 `code_spans`。
- **正例**：一段 15 行的 `computeBackoff`——输入 `attempt`、读取 base/factor/cap 配置、
  返回带 jitter 的时长；`code_spans` 指向 `src/retry.py:12-30`。
- **反例**：
  > 只给一句描述、不给代码；
  > 或给出代码但行号对不上真实文件。
  > ✗ 后者会被 `code_mismatch` 拦下——因为服务端会用真实文件重切并逐字比对。

### 4.3 `skeleton`

- **定义**：结构骨架——函数/类签名与关键分支，省略次要实现细节，**足以照着重写一遍**。
- **正例**：
  > `RetryEngine.run(fn)` 的骨架：循环 attempts → 调用 fn → 成功返回 → 失败判断可重试
  > → 计算退避 → sleep → 超预算抛错；每步留注释说明。
- **反例**：
  > 把整个文件贴进来（那是 snippet 甚至噪声）；
  > 或骨架缺关键分支，照着重写会漏掉错误处理。

### 4.4 `gotcha`

- **定义**：坑——看起来对但会出错的点，或必须知道的隐含前提。
  **这是最容易被忽略、也最有价值的一类。**
- **正例**：
  > `clamp` 必须在 `jitter` 之前，否则上限会被 `jitter` 突破；
  > 且 `sleep` 必须可被取消，否则关闭时最长等一个退避周期。
- **反例**：
  > 写「注意异常处理」这类放之四海皆准的空话。
  > ✗ 这不是坑，是常识；常识对任何仓库都成立，等于没写。

### 4.5 `decision`

- **定义**：取舍——选择了 A 而非 B，以及**为什么**。帮助后来者判断该不该照搬。
- **正例**：
  > 用「上限截断的指数退避」而非「固定间隔」：因为上游限流是突发型的，
  > 固定间隔在恢复后仍会撞限流；代价是平均等待更长。
- **反例**：
  > 只写「用了指数退避」不加理由。
  > ✗ 那是 `mechanism`，不是 `decision`。

---

## 5. 语言规则（G13）——中英分层

这是本契约里最容易被忽视、却直接影响核心机制的一条。规则只有一句话：

> **建向量的文本用英文；给人读的正文用用户母语。**

具体分工：

| 字段 | 语言 | 为什么 |
|---|---|---|
| `mechanism_desc`（★建向量） | **必须英文** | 向量空间只需一句浓缩描述保持一致，就能跨语言对齐 |
| `intent`（★意图探针） | **必须英文** | 同上；它是「什么需求会想借鉴这个」，要语言中立 |
| `summary` / 原理轴段落 / `title` | **用户母语** | 给人读，母语体验最好 |
| `tags` | 小写英文标识（`^[a-z0-9][a-z0-9._-]*$`） | 用于 FTS/keyword 通道 |

**为什么不是「全英文」或「全中文」**：

- 全英文：中文用户读报告要翻译，牺牲日常体验。
- 全中文：非中文仓库的术语翻译可能失真，且**跨语言聚类会失效**——
  多语种模型会把中文描述与英文描述分得比同语言的不同机制更远，`min_repos >= 2` **永远不满足**。

所以只让「建索引的那一句」保持语言中立，其余保持母语。这是「C 方案的简化版」。

> 校验：`mechanism_desc` 与 `intent` 若检出非 ASCII 主体（`unicode_language_mismatch`）则报错；
> 原理轴与 `summary` 若纯 ASCII 且短于阈值，也会提示。

---

## 6. 证据链（强制）

**问题**：LLM 会编造 `src/utils.ts:42` 这种看起来合理、实际不存在的引用。这是信任的根基。

**规则**：每条 `evidence` 的 `path` 必须是仓库中**真实存在**的文件；`start_line` / `end_line`
必须在文件行数范围内。不一致即**阻塞**。

- `snippet` / `skeleton` 卡的代码由**服务端从真实文件重切覆盖**；与 agent 给的 `code` 不一致时
  计入 `code_mismatch`——**这是唯一的硬门禁，必须为 0**（B9）。
- 校验器还会尝试**解析**切片（Python `ast` / tree-sitter，D3），不只是「行存在」。

`EvidenceRef` 的形状（对象，不用紧凑字符串）：

```json
{ "path": "src/retry.py", "start_line": 12, "end_line": 30,
  "symbol": "compute_backoff", "note": "上限截断与 jitter 的顺序" }
```

> 紧凑串 `"src/retry.py:12-30"` 允许出现在**展示**里，但**契约里一律用对象**。

---

## 7. 校验结果怎么读

`validate_report` 返回 `{ok, is_valid, problems[], warnings[], counts}`。注意：

- **`is_valid=false` 时 `ok` 仍为 `true`**——校验器**正常工作**不等于**报告通过**。
  业务失败才走 `ok:false`。
- `problems[]` 的每一项是 `{code, where, message}`，`where` 是 **JSON Pointer**（如 `/features/0/principles/data_flow`）。
- `Problem.code` 是**封闭枚举**：

| code | 含义 |
|---|---|
| `unknown_field` | 出现契约未定义的字段（A4） |
| `missing_field` | 缺必填字段 |
| `too_few_features` | `features` < 3 |
| `duplicate_feature_key` | `key` 仓内重复 |
| `blank_principle` | 某轴为空 / 写了 `N/A`、`待补充` 等占位符 |
| `principle_too_short` | 某轴 < 40 units；或卡片 `mechanism_desc` < 20 units |
| `axis_reuse` | 五原理轴有 3 轴以上正文雷同（归一化后同一文本） |
| `bad_evidence_path` | 证据路径不存在 |
| `line_out_of_range` | 行号越界 |
| `code_span_mismatch` | 切片与服务端重切不一致（计入 `code_mismatch`） |
| `code_required` | snippet/skeleton 缺 `code_spans` |
| `code_forbidden` | 非 snippet/skeleton 却带了 `code_spans` |
| `bad_enum` | 枚举值非法（kind / entry_point.kind / topic 等） |
| `unicode_language_mismatch` | 语言规则违反（§5） |

**失败时返回全量问题**，让 agent 一次改完，而不是挤牙膏。

---

## 8. 与其它文档的关系

| 文档 | 关系 |
|---|---|
| [`report.schema.json`](report.schema.json) | 本文的机器可读权威版本；`validate_report` 按它执行 |
| [`mcp-tools.md`](mcp-tools.md) | 工具签名；`commit_report` / `validate_report` 的入参就引用本契约 |
| [`gaps.md`](gaps.md) | G13（语言，本文已定）、G23（阈值待实测）、G3（schema，本文已定） |
| [`decisions.md`](decisions.md) | G 组：本文对应的决策条目 |
