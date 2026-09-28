# memex

跨仓库「实现借鉴」知识库 —— **以 MCP 服务形式提供给 coding agent**。

分析、判断、生成全部由调用方 agent 完成；本项目只负责**暴露接口、存取内容、强制校验**，
外加一个可选的静态站点用于浏览已有数据。

---

## 它解决什么问题

写一个新功能时，你手上有一个真实问题：**别人已经解决过它了，但散在几百个仓库里。**

现有的代码检索工具（grep、GitHub 搜索、各种「AI 读代码」工具）几乎都在做同一件事：
**读懂一个仓库**。它们从「文件」「符号」「字符串」出发，答的是「这段代码在干什么」。

memex 做的是缺掉的那一环 —— 从「意图」出发，答的是**「还有谁解决过类似的问题」**：

> 我要给一个 HTTP 服务加「请求体只能被一个提取器消费」的保证。
> Vue 项目里有没有人用别的语言解过同一个问题？后来怎么演化的？踩过什么坑？

这不是「找相似代码」，而是「找相似的**问题与解法**」。区别有三处，都体现在代码里：

| | 传统检索 | memex |
|---|---|---|
| 入口 | 文件 / 符号 / 关键词 | 一句**功能意图**的自然语言描述 |
| 粒度 | 文件、函数 | **功能（feature）** —— 需求真正对应的那个单元 |
| 返回 | 文本片段 | 带**机制解释 + 证据链**的卡片 |

### 三个设计支点

1. **知识粒度定在「功能级」**，不是仓库级也不是文件级。
   仓库级太粗（没法说「借鉴它的哪部分」），文件级太碎（同一个功能散在十几个文件里）。
   只有「功能」能对上「某个功能」这个需求单元。

2. **对语言中立的「机制描述」建向量**，而非对代码建向量。
   这是跨语言匹配的前提：Rust 的 `impl FromRequest` 和 Python 的 `__call__` 语法上毫无相似之处，
   但「body 只交给元组最后一项，前置项共享 parts」这个**机制**可以用同一句话描述，
   于是 Rust 的实现能被中文查询命中，也能被 Go 的实现命中。

3. **证据链强制校验** —— 落库前路径必须真实存在、代码必须从真实文件重切。
   没有这一条，知识库里就会堆满 agent 编出来的代码，借鉴它们等于照抄幻觉。

---

## 一次完整的使用流程

分析一个仓库由 agent 驱动，共四步；其中只有第二步（生成）完全交给 agent，其余三步都有服务端把关。

**① 抓取 + 取证据**

```bash
fetch_repo(repo_url="https://github.com/tokio-rs/axum")
  → 返回 clone 路径、目录树、入口点符号列表（静态证据包，不含代码）
get_evidence_pack(repo_id=...)            → 目录树 + 入口 + 符号清单
read_file_slice(repo_id=..., path=..., start=..., end=...)  → 按需读几段
```

机械活全在服务端：agent 用自己的 `Read` / `Grep` 直读克隆目录，
而不是把 MCP 当文件系统代理 —— 否则上下文窗口会被仓库内容吃光。

**② agent 分析并写报告**（这一步完全由 agent 做，服务端不参与生成）

agent 按报告契约产出 JSON：**每个功能**配一组**五原理轴**的判断，
外加若干张**卡片**。卡片有五种：

| kind | 讲什么 | `code_spans` |
|---|---|---|
| `mechanism` | 怎么运转，不含代码 | **不允许出现** |
| `snippet` | 可直接粘贴的真实片段 | **必须非空** |
| `skeleton` | 签名 + 关键分支的骨架 | **必须非空** |
| `gotcha` | 坑与隐含前提 | 不允许出现 |
| `decision` | 选了 A 而非 B 及理由 | 不允许出现 |

**③ 校验 + 落库**（写入路径，服务端强制）

```bash
begin_analysis(repo_url=...)            → analysis_id + evidence pack + 契约 + 检查清单
validate_report(analysis_id=..., report=...)   → 逐条校验，改到 is_valid=true
commit_report(analysis_id=...)          → 重切证据 + 建索引 + 跨仓聚类
```

`validate_report` 会拒绝这些（返回 `problems[]`，附 JSON Pointer 定位）：

- 证据路径在真实仓库里不存在
- `snippet` / `skeleton` 没给 `code_spans`，或其余 kind 给了
- 五原理轴写「无 / N/A / 待补充」这类占位符
- 中英分层用错：`mechanism_desc` 与 `intent` 必须英文，其余必须中文

**只有 `code_mismatch` 必须为 0 才允许提交** —— 这是硬门禁，不是建议。
落库时所有 `code` 字段会从真实文件**重新切一遍**，与 agent 提交的不一致就整份拒绝。

**④ 写新功能时召回**

```bash
search_implementations(query="如何保证同一份 request body 只被一个提取器消费", limit=8)
```

从真实库里跑出来的实际返回（top-1）：

```text
score 0.032266 | card | body 只能被一个提取器消费
  repo:   tokio-rs/axum (rust)
  tags:   extractor, gotcha, body, compile-time
  source: axum-core/src/extract/tuple.rs:66-68  impl_from_request::from_request
  "An extractor that consumes the body only works in the last position..."
```

注意返回的是 `source.path:start_line-end_line` —— **可以直接跳过去读原文**。
这正是第三个支点的意义：借鉴到的是有出处的实现，不是复述。

---

## 检索是怎么工作的

**三路召回 + RRF 融合**（k=60，等权），再按类型先验微调：

| 通道 | 命中什么 |
|---|---|
| vector | 语义相近的机制描述（跨语言的主力） |
| keyword | FTS5 三元组索引，精确词命中 |
| substr | LIKE 子串，能捞到缩写与专有名词 |

融合后按 `KIND_PRIOR` 定序：card 1.0 / feature 0.9 / pattern 0.6 / report_section 0.4。
可选的 rerank **默认关闭** —— 实测只有 `BAAI/bge-reranker-base` 值得开
（top-1 从 10/15 提到 13/15，MRR 0.776 → 0.910），其他模型反而变差。

聚类阈值定在 **0.60**（complete-linkage）。实测 20 对跨仓候选里只有 5 对真是同机制：
**没有零误报的阈值存在**，所以 `list_patterns` 如实给出 `min_score` 而不加默认闸门 ——
精度是 rerank 和阅读的问题，不是聚类的问题。

### 五个原理轴

每个功能都必须在五条轴上给出判断，缺一条就报 `blank_principle`：

| 轴 | 问什么 |
|---|---|
| `runtime_control_flow` 运行/控制流 | 谁调用谁、何时触发、循环/递归边界、提前返回条件 |
| `data_flow` 数据流 | 数据从哪来、经过什么结构、到哪去、在哪被转换/序列化 |
| `state_lifecycle` 状态生命周期 | 有哪些状态、谁创建/变更/销毁、持久化在哪、何时失效 |
| `failure_recovery` 失败与恢复 | 失败如何被检测、降级、重试、回滚、上报 |
| `concurrency_timing` 并发与时序 | 并发模型、锁/队列/超时/顺序保证、竞态如何避免 |

这五条是**封闭的固定集合**，所以「这个功能在这五条上怎么权衡」可以跨仓库对齐比较 ——
这是跨语言借鉴唯一能对齐的东西。

---

## 分工边界（本方案的核心约束）

| 环节 | 归属 | 理由 |
|---|---|---|
| 抓取 / clone | **server** | 机械，统一管理缓存 |
| 静态证据包、按需切片 | **server** | 纯计算不调模型；留着以免吃掉 agent 的上下文 |
| **分析、判断、生成** | **agent** | 它已有更强的模型和更真实的上下文 |
| 校验、重切、落库 | **server**（写入路径强制） | 校验必须与「谁生成的」无关 |
| 召回、检索 | **server** | 知识库自己的职责 |
| 流程引导 | **server**（会话状态机 + next_step） | 不能指望 agent 记得步骤 |

> **只有「生成和判断」交给 agent。** 这条边界是全案能否成立的关键。

---

## 工具面（17 个）

| 类别 | 工具 |
|---|---|
| 机械 | `fetch_repo` · `get_evidence_pack` · `read_file_slice` · `request_repo_bundle`（远程） · `upload_repo_bundle`（离线/私有） |
| 写入（带校验） | `begin_analysis` · `validate_report` · `commit_report` |
| 召回 | `search_implementations` · `get_card` · `list_patterns` · `get_report` · `list_repos` · `recall_stats` |
| 元 | `help`（5 个主题的结构化说明） |
| 破坏（**默认禁用**） | `forget_analysis` · `forget_repo` |

破坏类工具需显式列入 `MEMEX_TOOLS` 才放行。
逐个工具的完整签名（入参/出参 JSON Schema、注解、10 个错误码）见 `docs/mcp-tools.md`。

---

## 快速开始

```bash
# 安装（核心零第三方依赖；向量化需要 default 附加层）
pip install -e ".[default]"

# 建库
memex init

# 本地 stdio 形态接入 coding agent
claude mcp add memex -- memex serve-mcp
# 远程 Streamable HTTP 形态
memex serve-http --host 127.0.0.1 --port 8931
```

所有子命令：

| 命令 | 作用 |
|---|---|
| `memex init` | 建库、写 `meta`、初始化目录 |
| `memex serve-mcp` / `serve-http` | 两种服务形态 |
| `memex reindex [--embedder X] [--repo ID]` | 重算向量（换嵌入模型后用） |
| `memex export-site [--out DIR]` | 导出静态站点 |
| `memex stats` | 知识库总览 + 按 producer 分组的质量 |
| `memex forget-repo <id> --yes` | 删除仓库及其全部分析 |
| `memex import-vibecraft <path>` | 从 VibeCraft 回填 |
| `memex migrate [--to X] [--dry-run]` | 真源层迁移 |

导出站点会生成三层页面：`index.html` 仓库清单、`patterns.html` 跨仓模式、
每个仓库一页**完整报告正文**（功能 / 卡片 / 证据行号 / 折叠的原始 markdown）。
适合导出后丢给同事翻阅，或作为归档快照。

环境变量总表、启动检查清单、危险操作与常见故障见 `docs/operations.md`。

---

## 部署形态

两种，同一套代码：

| | 本地 stdio | 远程常驻（推荐） |
|---|---|---|
| 运输 | `stdio` | **Streamable HTTP** |
| 接入 | `claude mcp add memex -- memex serve-mcp` | HTTP + `Authorization: Bearer` |
| 认证 | 不需要 | **必须有**（静态 Bearer Token 起步） |

远程形态下 agent 在开发机上**读不到服务器的克隆目录**，
所以 `fetch_repo` 之后要用 `request_repo_bundle` 把仓库以 `git bundle` 单文件下发。
架构理由见 `docs/tech-design.md` 第四章，
**终态的可执行规格（Docker 镜像、端点、配置、待实现清单）见 `docs/deployment.md`**。

---

## 文档

| 文件 | 内容 |
|---|---|
| `docs/solution-analysis.md` | 需求拆解、生态调研、路线对比、agent 驱动的推荐方案、风险与最终形态 |
| `docs/decisions.md` | **50 条已定决定**（A–E 五组 + G 组）及其理由 |
| `docs/gaps.md` | 空缺清单：曾有 28 项「还没定」，现已全部定案 |
| `docs/tech-design.md` | 技术设计：语言选型、存储与数据模型、依赖分层、远程部署、并发实测 |
| `docs/mcp-tools.md` | 17 个工具的完整入参/出参 JSON Schema + 全局约定 |
| `docs/report.schema.json` | 报告契约（机器可读）`memex/report/1` |
| `docs/report-contract.md` | 报告契约（散文）：四 H2 骨架、五原理轴、卡片五类正反例、中英分层规则 |
| `docs/operations.md` | 环境变量、子命令、启动检查、危险操作、故障处置 |
| `docs/deployment.md` | **最终形态部署规格**：Docker 镜像、三个 HTTP 端点、认证与限流、客户端接入、待实现改动清单 |

> `decisions.md` 记「已定 + 理由」，`gaps.md` 记「未定 + 建议」——两份互补。

---

## 当前状态

- **测试 185 例全绿**（CI 镜像不含 Node，前端产物用例整组显式跳过，见 `docs/deployment.md` §11），`mypy --strict` 51 个源文件零告警；核心零第三方依赖。
- **验收**：拿真实复杂仓库（PTNexus，Go）跑完 20 步 MCP 全流程，20/20 通过。
- **检索基线**（15 条中文探针）：top-1 命中 10/15，top-10 命中 15/15，MRR 0.776。
- **并发**：远程 HTTP 形态每请求一线程、**每线程一条 SQLite 连接**（硬约束，见 `docs/operations.md` §1.1）；瓶颈在嵌入模型，不在服务层。规模数字属部署后实测项。

## 已知限制

- 嵌入模型首次加载较慢（默认 `paraphrase-multilingual-MiniLM-L12-v2`，384 维）。
- 跨仓聚类的**精度有限**（见上文阈值一节）—— 模式是「线索」，不是「结论」。
- 只支持 git 托管的仓库（`fetch_repo` 会校验 `repo_url` 宿主白名单）。
