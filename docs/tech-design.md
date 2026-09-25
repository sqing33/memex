# 技术设计：memex

> 承接 [`solution-analysis.md`](solution-analysis.md)（做什么、为什么）与 [`decisions.md`](decisions.md)（26 条取舍：A–E 五组 + G 组第 1 批）。
> 本文回答三件事：**用什么语言写、内容怎么存、基建怎么搭**。
> 取舍原则同前：质量优先进度，唯一硬约束是 agent 的上下文窗口。

---

## 一、技术选型

### 1.1 结论

| 层 | 选型 | 理由 |
|---|---|---|
| 主语言 | **Python 3.11+** | 分析引擎、`ast` 校验、数据生态都在 Python；与 agent 分发渠道天然重合 |
| 强类型层 | **mypy --strict** | 契约密集的项目，类型检查是免费的回归测试 |
| 打包 / 工具链 | **uv**（含 `uvx` 运行） | 一条命令装完；`uvx memex` 让 MCP 接入不用污染宿主环境 |
| 存储 | **SQLite + FTS5 + sqlite-vec**（单文件） | 零运维、事务；召回规模（几十仓 / 十万 chunk）远未到瓶颈 |
| 向量 | **sqlite-vec**（默认）／应用层暴力余弦（兜底） | 见 §2.4 |
| 嵌入 | **sentence-transformers**（默认本地）或 **HTTP embedding 服务** | 见 §1.4 |
| 协议 | **MCP：stdio（本地）+ Streamable HTTP（远程）** | 见 §4 |
| 代码解析 | **tree-sitter**（本地 wheel）+ 标准库 `ast` | 见 §1.3 |
| 站点导出的 HTML 来源 | **服务端渲染字符串模板**（不用 Node） | 见 §2.7 |
| Web 页面运行时 | **无**（静态导出） | 不引入常驻服务 |

### 1.2 为什么是 Python，以及必须承认的代价

用 Python 有两个**同向**的强理由，但有一个必须处理的代价。

**理由 1：分析和校验生态在 Python。** 静态证据包（数文件、抽符号、模块树）、
`ast` 语法校验、embedding 模型、聚类——这套东西的成熟库几乎全在 Python。
换到 Node/Rust 意味着把最难的那部分用最不合适的工具重写一遍。

**理由 2：分发渠道和 agent 生态同源。** 目标用户是「用 Claude Code / Cursor 写代码的人」，
这些人几乎都有 Python。而且可以走 `uvx memex serve-mcp`——**零安装、不污染宿主环境**，
这对「一个 MCP 小工具」的采纳率是决定性的。

**代价：启动延迟。** Python 解释器冷启动 + `import torch`/`sentence_transformers` 约 13s，
首次加载模型再加十几秒。**但启动检查绝不阻塞 MCP `initialize`**：进程起来后立刻响应握手，
真模型在**后台线程**预热（§4.6）。在**本地 stdio** 形态下这是「客户端启动时付一次」；
在**远程常驻**形态下（§4）服务长期在线，只付一次——两种形态下都可接受。
（对比：如果每次工具调用都起进程，Python 就不能接受——本项目不是这种形态。）

**被否掉的选项：**

| 选项 | 否掉的原因 |
|---|---|
| **Go / Rust** | 启动快、单二进制分发漂亮，但分析/ML 生态要重造，是拿最强项换最弱项 |
| **TypeScript** | 只解决「MCP 恰好也是 TS 生态」，但把分析引擎、`ast` 校验、embedding 全推倒 |
| **Rust + Python 混合** | 两套构建、两套部署；当前规模下收益为负 |

### 1.3 代码解析：分级，不是二选一

`decisions.md` D3 要求「切片语法可解析」，这需要解析器。**按语言分级**，而不是一刀切依赖 tree-sitter：

| 级别 | 语言 | 手段 |
|---|---|---|
| 一等 | Python | 标准库 `ast` —— 零依赖、权威、永远可用 |
| 一等 | JS / TS | tree-sitter（官方 wheel 覆盖 Linux/macOS/Windows） |
| 二等 | Go / Java / Rust / C++ … | tree-sitter 若有对应 grammar 则用 |
| 降级 | 其余 | 退回「行存在 + 非空 + 非纯注释」校验，并在卡片上标 `syntax_unverified: true` |

**关键：解析器只用于「校验与定位」，不用于「生成」。** 生成永远是 agent 的模型在做。
所以缺一个 grammar 的后果只是「这个语言的卡片多一道人工背书」，不是功能瘫痪。

`tree-sitter` 装在 `[analysis]` extra 而非核心依赖——不装也能跑通 Python 全流程。

### 1.4 嵌入模型：默认本地，可切远程

D1 定下「真语义模型是默认」，具体落地：

| 形态 | 实现 | 何时用 |
|---|---|---|
| **默认** | `sentence-transformers` 多语种模型（如 `paraphrase-multilingual-MiniLM-L12-v2`，约 470MB） | 装机即用，离线可跑，无需 key |
| **可选** | HTTP embedding 服务（OpenAI 兼容 / 本地 vLLM / Ollama） | 想要更好的多语效果，或不想下模型 |
| **冒烟** | hash embedder | 只在「目标机器装不上任何依赖」时跑测试；**不是推荐形态** |

配置形态与 `recall/store/embed.py` 已有的 `resolve_embedder()` 一致，扩展为显式三选。

**维度处理**：不同模型的向量维度不同。`chunk_vectors` 表记录 `dim`，**建库时固定模型**；
换模型需 `reindex`（已有 `cli reindex` 的雏形）。不做「多模型并存」——收益低、复杂度高。

---

## 二、内容怎么存

### 2.1 存储形态：一个目录，一个库文件

```
$MEMEX_HOME/                    （默认 ~/.memex）
  memex.db                      ← SQLite：全部结构化内容 + 向量 + FTS5
  repos/
    <host>__<owner>__<name>/    ← 浅克隆缓存，agent 直读用
  site/                         ← export-site 的静态产物（可选）
```

**单文件是刻意的。** 备份 = 拷一个文件；迁移 = 拷一个目录；调试 = `sqlite3 memex.db` 直接看。
不用 PostgreSQL/向量库：当前量级（几十仓、十万级 chunk）SQLite 余弦暴力扫描在毫秒级。

> **远程部署时改为两库**：`memex.db`（真源）+ `index.db`（chunk / 向量 / FTS，可重建）。
> 理由与开关见 §4.5——本地形态单文件更简单，服务端形态分离更稳妥。两者同一套 DDL。

### 2.2 数据模型（13 表）

沿用 `recall/store/db.py` 已验证的 DDL，按新架构调整（见 §2.3；★ 标记处为相对 recall 的新增）。

```
① 元与仓库
  meta(key, value)                           schema_version / embedder / dim / created_at
  repos(repo_id PK, full_name, url, host, default_branch,
        language, stars, license, description,
        subpath,                               ← ★ G15：monorepo 子目录范围（省略=全仓）
        identity_key UQ,                       ← ★ G10：真身份锚（github.com#<数字 id>）
        aliases_json,                          ← ★ G10：历史 full_name 轨迹
        fork_of, is_fork,                      ← ★ G10：fork 上游全名 / 是否 fork
        source,                                ← ★ G22：'clone' | 'tar' | 'upload' | 'local'
        is_stale, head_sha, cloned_at, repo_path, is_local)
        identity_key: 逐宿主最稳的锚；无 API 时退化为 'host#owner/name' 并记 warnings（G10）
        source:       'upload' = 经 T17 投喂（G22）

② 分析与产物（每次分析一行）
  analyses(analysis_id PK, repo_id FK, commit_sha, contract_version, depth,
           analyst, producer, status, report_md, report_json,
           counts_json, quality_json, created_at, finished_at,
           UNIQUE(repo_id, commit_sha, contract_version))   ← 幂等键（G19）
           analyst:  自由文本（如 "claude-code/opus-4.5" / "batch/st-embed"），
                     仅用于质量归因，不参与幂等
           producer: 'agent' | 'batch'（G21），质量指标按此分组统计
           reindex_state: 'indexed' | 'pending'（G12：mechanism_desc 非语言中立者排除出检索）

③ 知识单元（功能 → 卡片 → 证据）
  features(feature_id PK, analysis_id FK, slug, title, summary, position)
  cards(card_id PK, feature_id FK, kind, reusable, title, summary,
        mechanism_desc,                       ← ★ 建向量的文本
        language, symbol,
        code_spans_json,                      ← ★ A1：有序段列表
        quality_json)
  evidence(evidence_id PK, card_id FK, path, start_line, end_line, symbol,
           file_sha, excerpt)                 ← 证据链（强制校验后写入）

④ 跨仓模式（聚类产物，可从 cards 重建）
  patterns(pattern_id PK, key UQ, title, tags_json, card_count, repo_count)
  pattern_members(pattern_id FK, card_id FK, score, PK(pattern_id, card_id))
  pattern_intents(pattern_id FK, text)        ← 非对称因子分解出的「意图探针」向量文本

⑤ 检索（可重建，不进备份的必需集）
  chunks(chunk_id PK, kind, ref_id, card_id, text, repo_id, language, heading)
        kind ∈ feature | card | pattern | report_section
        ref_id: pattern chunk 存 pattern_key（'pat:<key>'）；repo_id 对 pattern 允许 NULL（★ G14）
  chunk_vectors(chunk_id PK, embedder, dim, vec BLOB)   ← ★ G11：每行记自己的编码模型
                                                        vec = float32 小端打包
  chunk_fts                                        ← FTS5，tokenize='trigram'

⑥ 会话与可观测（★ G18）
  sessions(session_id PK, repo_id FK, state, created_at, expires_at, abandoned_at, meta_json)
        state ∈ begun | evidence_taken | drafting | validated | committed | abandoned
  session_stats(session_id PK, repo_id, turns, tool_calls, tokens_est,
                wall_seconds, outcome, created_at)   ← ★ 回收会话前先归档到这里，永不删
```

**可重建性分层**（决定备份策略）：

| 层 | 内容 | 能否重建 |
|---|---|---|
| 真源 | `repos` + `analyses`（报告原文）+ `cards` / `evidence` | **不能**（agent 的劳动） |
| 派生 | `features` / `patterns` / `pattern_members` | 能从真源重算 |
| 索引 | `chunks` / `chunk_vectors` / `chunk_fts` | 能从真源重算（`reindex`） |

所以备份只需 `analyses + cards + evidence + repos`（真源四件套）；索引炸了大不了重建。
**`session_stats` 例外**：它不是真源也不是索引，而是**不可复得的过程数据**（V1 判「agent 驱动
可不可行」的唯一依据），因此**既不参与 reindex 也不随会话回收而删**（G18）。

### 2.3 相对 recall 的改动

1. **卡片代码从单区间 → `code_spans_json` 多段列表**（A1）。校验从「切一次」变成「切 N 次」，
   每段独立验路径存在 + 行号合法 + 语法可解析（D3），渲染时插 `// … (elided) …`。
2. **新增 `pattern_intents` 与模式的非对称分解**（见 §2.5）——不是「聚类出的题」反过来当探针，
   而是把意图探针作为**独立的一等资产**存下来。
3. **`analyses` 增加 `quality_json`**（5.8 可观测性）：`code_mismatch`（硬门禁）、
   轴完整度、证据覆盖率、会话 turn/token。
4. **`repos` 增加身份四列** `identity_key` / `aliases_json` / `fork_of` / `is_fork`（G10），
   `full_name` 的唯一键**改为 `identity_key`**；聚类按 `source_group = COALESCE(fork_of, identity_key)`
   计 distinct（G10）。
5. **新增 `sessions` / `session_stats` 两表**（G18）：会话状态从进程内存搬进 SQLite（§2.6），
   回收前先把过程统计归档，**保证 V1 的关键数据不随会话蒸发**。

### 2.4 向量怎么放

三个候选，按可行性排序：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. sqlite-vec 扩展** | `LoadExtension("vec0")`，SQL 原生 KNN | 最优雅，但 wheel 需与平台匹配；VibeCraft 用的是这条路（`sqlite3_vec_init`） |
| **B. 应用层暴力余弦** | `vec` 存 float32 BLOB，Python/numpy 全量算 | **零依赖、完全可控**；十万级 chunk × 512 维 ≈ 200MB、单次扫描几十毫秒——**当前量级完全够用** |
| **C. 独立向量库** | LanceDB / Qdrant | 过度设计，破坏「单文件」形态 |

**决定：默认 sqlite-vec；检测不到扩展时自动退回应用层暴力余弦。** 同一个 `search` 接口，两种后端。

- Python 侧用 `sqlite-vec` 包（`import sqlite_vec; sqlite_vec.load(conn)`），无需手工下二进制。
- 这与 VibeCraft 的选择一致（它用 Go + `sqlite3_vec_init`，见 §2.8），量的规模也一致。
- 暴力余弦作为**永不失效的兜底**保留：扩展在个别平台装不上时，最坏情况只是召回变慢，不是功能消失。

不用 FAISS/HNSW：召回量级决定了收益为零，而它带来「索引要单独维护、删改不灵活」的代价。

### 2.5 跨语言检索：非对称因子分解

这是**本设计里唯一一个超出原方案的算法改动**，值得单独说。

困难：检索时人/agent 给的是**意图**（"我要做带退避的重试"），
而库里存的是**实现描述**（"指数退避，按次数翻倍再用上限截断"）。
两者**措辞不同、长度不同、粒度不同**——直接算余弦，分数会被「表述相似」淹没，而非「功能相同」。

做法：**把「意图」和「实现描述」当成两个分布，学一个映射。**

```
① 数据来源（零人工标注）
   已有：每个 pattern 由 ≥2 个仓库的卡片组成 → 同一功能的不同实现 = 天然正样本对
   另加：agent 每次 search 的 query → 它最终点开/采纳的卡片 = 在线正样本对

② 训练
   用对称编码器初始化，两个头：
        query → E_q(·)      意图编码器
        card  → E_d(·)      描述编码器
   目标：同一 pattern 内的卡与「该 pattern 的意图描述」互为正样本（in-batch 负样本）
   规模：几百~几千对即可收敛；CPU 可训（这是个小模型，不是预训练）

③ 为什么能成立：这是「非对称检索」的标准解法
   （对应 literature 里的 query-document 非对称 embedding / 双塔 + 非对称头）
   新库（无 pattern）时退化为对称编码器 —— **冷启动不阻塞**，随着库变大有数据可训

④ 意图探针的来源（pattern_intents）
   不是把聚类结果反过来当探针（那是循环论证），而是分析时让 agent 额外产出一句
   「什么需求会想借鉴这个」——这句话才是探针，独立于聚类。
```

**诚实的边界**：这条路上「学一个映射」的效果取决于数据量与多样性，
小库上很可能**不显著优于**直接用好的多语种对称模型。
融合之后还有一步 **kind 先验**（G14）：RRF 只按排名投票、不加权（三通道等权），
`final = fused * kind_prior[kind]`，初值 `card 1.0 / feature 0.9 / pattern 0.6 / report_section 0.4`。
这样「同一量纲」的最终分才被调，避免 VibeCraft 那种「两个不同量纲的分先相加」（§2.8）。

所以落地顺序是：**先做对称 + RRF + kind 先验 + rerank（V1~V3 全部可验证），
非对称分解作为 V4 的一个实验分支去量化对比**——用数字决定要不要上，不靠信仰。

### 2.6 会话状态机存哪

**会话状态也存 SQLite，不存进程内存。**

理由：MCP server 可能被客户端重启（升级、崩溃、用户重连卡住的会话同样要能续）；
而且 agent 驱动下「一个会话跑很久、跨很多次工具调用」是常态，
进程内存里的会话一重启就没了，agent 会卡在半路。

```
sessions(session_id PK, repo_id FK, state, created_at, expires_at, abandoned_at, meta_json)
  state ∈ begun | evidence_taken | drafting | validated | committed | abandoned
session_stats(session_id PK, repo_id, turns, tool_calls, tokens_est, wall_seconds, outcome, created_at)
```

**G18 定下的回收语义**（三步，顺序不可换）：

1. **归档统计**：turns / tool_calls / tokens_est / wall_seconds 写入 `session_stats`。
2. **留痕**：置 `state='abandoned'` + `abandoned_at`，**行保留**。
3. **物理删除**：过 `MEMEX_SESSION_RETENTION_SECONDS`（默认 7 天）后删 `sessions` 行；
   **`session_stats` 永不删**（V1 判「agent 驱动可不可行」的唯一依据）。

TTL 到期（`MEMEX_SESSION_TTL_SECONDS`，默认 2 小时）由**下一次任意工具调用顺带清扫**
（本地 stdio 不做后台线程——单进程里不值得）。

> **远程形态下**这一点要复核：多客户端并发时会话更多、且 `Mcp-Session-Id` 断开（404）与业务会话无关，
> 惰性清扫可能不及时。`serve-http` 因此**默认起一个低频后台清扫线程（每 60s）**
> （`ThreadingHTTPServer` 下无副作用）；是否真有必要由 G26 实测决定。
> 但**不要**因为运输层 session 失效就丢弃业务会话——两者独立（§4.3）。

### 2.7 静态站点怎么生成

`sentence-transformers` 同理不缺模板引擎：**用 Python 标准库 `string.Template` + 手写 HTML**，
或引入 `jinja2`（已在依赖树里，因为很多 ML 包传递依赖它）。

**不引入 Node / Vite。** 站点只是一页仓库目录（C11），用不着前端框架——
引入 Node 会把「一个 Python 包」变成「两个工具链」，与选型理由矛盾。

### 2.8 VibeCraft 存量实现对照与回填（G12）

#### 2.8.1 存储对照

VibeCraft 的 Repo Library **已经用 SQLite 存了向量**，而且做法与本文几乎一致。
读源码（`backend/internal/repolib/searchdb/`）确认：

| 维度 | VibeCraft 实际做法 | memex |
|---|---|---|
| 引擎 | `mattn/go-sqlite3`（另有 `modernc.org/sqlite`） | 标准库 `sqlite3` |
| 向量扩展 | **sqlite-vec 0.1.6**，自动下载平台资产，`LoadExtension(path, "sqlite3_vec_init")` | 同（Python 包） |
| 向量表 | `CREATE VIRTUAL TABLE kb_chunk_vec USING vec0(embedding float[N], analysis_id, source_kind)` | 同形 |
| 写入 | `packFloat32LE`（4 字节小端）→ `INSERT OR REPLACE` | 同（`dim` 一并存） |
| 检索 | `WHERE embedding MATCH ? AND k = ?` + `1/(1+distance)` | 同 |
| 嵌入模型 | **hugot（纯 Go ONNX）跑 `all-MiniLM-L6-v2`**，可配置下载 | sentence-transformers |
| 全文检索 | FTS5 **external-content** 表 + 三个触发器同步，BM25 | FTS5 trigram |
| 融合 | 加权求和：keyword **0.45** / vector **0.55** / title **0.9**，再乘 sourceKind 权重（card 1.35 / report_section 0.92 / evidence 0.75） | RRF（等权）+ 融合后的 `kind_prior` + rerank |
| 数据库位置 | `<repoDir>/search/search.db`（**独立于主业务库**） | `memex.db` 单文件 |

**结论：你的判断是对的——VibeCraft 就是「SQLite 存向量」。** 这给了 memex 两个信心：
① 这条路在你的规模上已被实证可行；② 向量层可以**原样搬**，不是新风险。

**memex 与之的三处不同，都是有意的：**

1. **融合用 RRF 而非加权求和。** 加权求和要调权重（0.45/0.55/0.9 是手调的），
   且 `1/(1+distance)` 与 `1/(1+bm25)` 两种分数**不同量纲**却直接相加。
   RRF 只用排名，天然免调参、免量纲问题。
2. **索引与业务库合一。** VibeCraft 分两个库（业务库 + `search.db`），
   换来的是「索引可独立重建」。memex 用**可重建性分层**（§2.2）达到同一目的，
   但只维护一个文件——备份/迁移简单得多。
3. **嵌入不变，但向量文本变了。** VibeCraft 索引的 `search_text` 是 chunk 的文本
   （代码/报告片段）；memex 索引的是**语言中立的机制描述**（§2.5）。
   这是全案最关键的差异，也是跨语言能力的前提。

#### 2.8.2 回填（G12）：`cli import-vibecraft <path>`

VibeCraft 的库格式已实地核对（读其 `backend/internal/store/` 与 `services/repo-analyzer/`）：

| 内容 | 位置 |
|---|---|
| 主业务库 | `<repoLibraryDir>/*.db`：`repo_analysis_results`（旧名 `repo_analysis_runs`，migrate 时改名重建）、`repo_knowledge_cards`、`repo_knowledge_evidence`、`repo_sources`、`repo_snapshots` |
| 报告文本 | `<storage_path>/report.md`，`storage_path = <repoLibraryDir>/repositories/<repo_key>/analyses/<analysis_id>` |
| 检索索引 | `<repoDir>/search/search.db`（`kb_chunks` / `_fts` / `_vec`）——**纯派生，不搬**，回填后由 memex 自己 `reindex` |

**两处落差（决定回填率）**：

1. **卡片 kind 体系对不上**：VibeCraft 只有三种 `card_type`（`project_characteristic` /
   `feature_pattern` / `integration_note`），memex 是五类（`snippet` / `skeleton` / `mechanism` /
   `gotcha` / `decision`）。映射见 `decisions.md` G12 表。
2. **证据粒度对不上**：VibeCraft 的证据只有 `path + line + snippet` + 一个 `dimension` 标签，
   **没有区间、没有语法校验、没有 `code_mismatch` 概念**。所以回填的卡片必须**从真实文件重切**：
   拿 `path` + `line` 去真实文件取区间，**切不出/对不上 → 该卡片判 `code_mismatch` → 丢弃**。

**硬约束**：回填同样要过 `validate_report`（否则等于绕过证据链门禁）；`mechanism_desc` 必须
**语言中立**（G13/D1），VibeCraft 的中文 `mechanism` 需 agent 补写英文描述前先标
`reindex_state='pending'` 排除出检索；产物标 `producer=batch` / `analyst=vibecraft-import`。
**代价要诚实**：两处落差决定了「相当一部分旧卡片回填不过」，所以 `--dry-run` 必做、
`evidence_hit_rate` 必报——**能救几张是几张**，不追求全量。

---

## 三、基建

### 3.1 目录结构

```
memex/
  pyproject.toml            uv / pip 双兼容；[analysis] extra 放 tree-sitter
  src/memex/
    __init__.py
    core.py                 配置、路径、slugify、repo URL 解析
    cli.py                  init / serve-mcp / serve-http / reindex / migrate / export-site / stats
                            / forget-repo / import-vibecraft
    store/                  db.py（DDL+迁移）· embed.py · search.py（RRF + kind_prior 先验 + rerank）
    fetch/                  clone / tarball 兜底 / 元数据 / head_sha 比对 / 镜像重写（G22）
                            / 身份解析（G10） / git bundle 下发（T4）与上传（T17）
    evidence/               codeindex：模块树 · 入口点 · 符号清单（不调模型）
    contract/               schema.py（契约 + 校验器）· syntax.py（ast / tree-sitter）
    session/                状态机
    mcp/                    协议层（stdio + Streamable HTTP，只回 JSON）+ 17 个工具的 handler
                            能力声明只含 tools；resources/prompts 不实现（G27）
    patterns/               cluster.py（凝聚聚类）· intent.py（非对称分解）· chunk.py（pattern chunk 合成，G14）
    import_/                VibeCraft 回填（G12）；目录名带下划线避免与关键字冲突
    batch/                  服务端 LLM 批量分析（原 recall/analyze/llm.py + pipeline.py 迁移而来）
    site/                   export-site 的模板与渲染
  docs/                     本文档 + 方案 + 决策
  tests/                    离线 fixture（mini_a/mini_b 双语言对）+ 单测
```

### 3.2 依赖策略：分三层

| 层 | 内容 | 装不上时 |
|---|---|---|
| **核心** | 标准库 only（`sqlite3` / `ast` / `json` / `argparse` / `urllib` / `subprocess`） | 永远可用 |
| **默认** | `numpy`（向量化余弦）· `sentence-transformers`（嵌入） | 退化到纯 Python 余弦 + hash embedder，功能降级但可跑 |
| **可选** | `tree-sitter` + grammars（多语言语法校验） | 该语言退回行存在校验 |

**核心层坚持零第三方**，是为了让「MCP server 能在任何机器上启动」这件事永远成立：
agent 的工具链崩掉一个 MCP 连接是很烦的事，而只要核心层零依赖，最坏情况是「召回质量差」，
不会是「连不上」。这与 D1「真模型是推荐形态」不矛盾——**推荐不等于强制**。

### 3.3 配置

```
$MEMEX_HOME              默认 ~/.memex（远程：/var/lib/memex）
$MEMEX_EMBEDDER          默认 sentence-transformers 模型名；可设 hash:512 / http:<url>
$MEMEX_RERANK            默认 off；可设 cross-encoder 模型名
$MEMEX_TOKEN             远程形态：访问所需的 Bearer Token（见 §4.4）
```

### 3.4 分发与接入

```bash
uvx memex serve-mcp                      # 本地：零安装，stdio
# 或
uv tool install memex && memex serve-mcp

claude mcp add memex -- uvx memex serve-mcp

# 远程：常驻服务 + Streamable HTTP（见第四章）
memex serve-http --host 127.0.0.1 --port 8931
claude mcp add --transport http memex https://memex.example.com/mcp \
  --header "Authorization: Bearer $MEMEX_TOKEN"
```

### 3.5 测试策略

沿用 `/workspace/recall/tests` 已验证的模式：**双语言 fixture 对**（`mini_a` Python / `mini_b` TS
实现同样两个能力）+ hash embedder 全离线。

三层测试：

| 层 | 测什么 | 是否需网络/模型 |
|---|---|---|
| 单测 | 校验器（假路径必拒、短段落必拒、残代码必拒）· 切片 · 状态机转移 · RRF 融合 | 否 |
| 集成（离线） | 全流程：fixture → 证据包 → 提交 → 落库 → 召回 → 聚类 | 否（hash embedder） |
| 端到端（真） | V1 单仓闭环：真 agent 分析一个真仓库，测 turn / token / `code_mismatch` | 是 |

**离线层必须能在「零第三方依赖」下跑通**——它是「核心层零依赖」这条承诺的自动验证。

---

## 四、部署形态：远程常驻（你的方案）

### 4.1 结论先行

**语言不变，仍是 Python。** 远程化提出了一批新要求，但**没有一项指向 Go/Rust**：

| 远程化带来的新要求 | 是否需要换语言 | 怎么解决 |
|---|---|---|
| 要跑常驻服务、多客户端并发连接 | ✗ | 标准库 `http.server` + `ThreadingHTTPServer` 即可；或 uvicorn 一行（§4.3） |
| 要处理认证与网络暴露 | ✗ | 与语言无关，见 §4.4 |
| 常驻进程 CPU/内存占用敏感 | ✗ | 瓶颈是嵌入模型，不是语言；靠预置与可切远程解决（§4.6） |
| 启动延迟 | ✗ | **远程形态下启动只发生一次**，Python 的这个唯一短板被消掉了 |

**唯一的真实变化是：`fetch_repo` 返回的本地路径对 agent 不再可达。** 这是本次改动里
最需要设计的一处，见 §4.2。

### 4.2 远程化最大的影响：agent 读不到克隆目录了

方案里最有效的一条对策是「`fetch_repo` 返回**本地克隆路径**，让 agent 用自己的 `Read`/`Grep`
直读，不把 MCP 当文件系统代理」。**远程部署会切断这条路**——server 上的 `/var/memex/repos/...`
在开发机上是不可见的。

这不是小事：若退回「每个文件都通过 MCP 读」，就正好犯了方案要避免的错（把 MCP 当文件系统代理），
且每次读文件都过网络往返。

**三条候选，推荐第三条：**

| 方案 | 做法 | 评价 |
|---|---|---|
| A. 全走 `read_file_slice` | agent 每次读文件都调 MCP | ✗ 网络往返 × N；正是方案要避免的形态 |
| B. 分析也在开发机上做 | 分析阶段本地 clone，只把结果推到服务器 | ◐ 可行但「常驻中心库」的意义打折 |
| **C. 服务端 `git bundle` 下发** | 把克隆打成单文件，agent 拉回本地自己读 | **✓ 推荐** |

**方案 C 的细节**（值得做对，因为这是远程形态的成败点）：

```
server: fetch_repo(url)  → clone 到服务器（长期保存 = 知识库的源）
        ↓
agent 要分析时：request_repo_bundle(repo_id)
        ↓ server 返回
        { url: "https://memex.example.com/repos/<id>/bundle",
          sha256: "...", size: 12_345_678, commit_sha: "..." }
        ↓ agent 侧一行命令
        git clone https://.../bundle memex-source/<repo>   # 或 curl + git bundle unbundle
        ↓ 之后 agent 用自己的 Read/Grep 读本地文件，零 MCP 往返
```

好处：①「机械活留 server」的原则得以保留；② 传输是**一次性的**，不是 per-file；
③ `git bundle` 是 git 原生格式，无需自定义协议；④ sha256 校验顺带解决传输完整性。

**降级路径**：若 agent 侧不方便执行 `git`，退到 `read_file_slice`——但**要提醒它批量读**，
并把「文件树 + 符号清单」在证据包里给足，减少它需要读的文件数。

> **反向的一条路（G22）**：若 server 无外网 / 无私有仓凭据，`fetch_repo` 的两条抓取路都断。
> 此时用 **T17 `upload_repo_bundle`**：开发机 `git bundle create repo.bundle --all` → 上传 →
> server `git bundle verify` + clone 登记。对偶关系：T4 是 server→agent（下发），T17 是 agent→server（上传）。
> 它同时解掉 G4 的「开发机有凭据、服务器没有」变体（server 全程不接触 token）。

> 如果最终只想跑单机（本地 stdio），§4 整章可以跳过，前面设计不受影响。

### 4.3 运输层：从 stdio 换成 Streamable HTTP

MCP 规范（2025-06-18）定义了两种标准运输：`stdio` 与 **Streamable HTTP**。
远程部署要用后者。规范里与远程直接相关的硬性要求：

| 规范要求 | 落地 |
|---|---|
| 服务端必须提供**单一 endpoint 路径**，同时支持 POST 和 GET | `/mcp` |
| POST 的 `Accept` 必须同时列 `application/json` 和 `text/event-stream`；服务端二选一回 | 我们**只回 `application/json`**（见下） |
| **必须校验 `Origin` 头**，防 DNS rebinding | 白名单；配了域名后只允许自己的域 |
| 本地建议只绑 `127.0.0.1` | 远程部署必然绑外网 → **必须配认证** |
| 建议实现认证 | 见下 |
| 可选 `Mcp-Session-Id`（初始化时下发） | 与我们的 `begin_analysis` 会话**不是一回事**，见下 |

**两个容易踩的坑：**

1. **SSE 与我们的同步语义冲突。** 规范允许服务端用 SSE 流式回，也允许直接回 JSON。
   我们的工具（`search` / `commit_report`）都是**请求-响应**语义，没有 server→client 推送需求，
   所以**只实现 `application/json` 分支**，简化实现。若将来要做「长时批量分析进度推送」，
   再加 SSE 分支。

2. **别把 MCP 的 session 和我们分析会话混为一谈。** `Mcp-Session-Id` 是**运输层**概念
   （连接级，可重连/恢复）；我们的 `session_id` 是**业务层**概念（`begin_analysis` 开的分析会话，
   存 SQLite、有 TTL）。两者独立，不要复用同一个 id——运输层会 404 后重建，业务会话不该因此丢。

**多客户端并发**：远程后必然多个开发机同时连。Python 标准库 `ThreadingHTTPServer`
即可支撑（本项目是 IO 密集：等 clone、等 embedding；每请求几十毫秒到几秒）。
考虑到「质量优先」，可以直接上 `uvicorn` + `starlette`（依赖很轻），
把并发的天花板抬高；**但**核心层「零第三方」的承诺要保住——HTTP 层做成可选 extra，
缺失时退到标准库实现。

### 4.4 认证：自用也必须有

远程后服务暴露在公网，**裸奔等于把你的知识库和 clone 能力送给全网**（还能被当跳板 clone 任意仓库）。

规范推荐的是 OAuth 2.1（授权服务器发现 + RFC 8707 resource 参数 + PKCE + 动态客户端注册）。
这套是**给多用户第三方服务**设计的，完整实现不轻。按用途分两档：

| 档 | 场景 | 做法 |
|---|---|---|
| **推荐（你的场景）** | 单人/小团队，服务器自己有 | **静态 Bearer Token**：`Authorization: Bearer <token>`，由反向代理（Caddy/Nginx）或应用层校验 |
| 完整 | 要对陌生用户开放 | OAuth 2.1：`WWW-Authenticate` 指 PRM、token audience 校验…… |

Claude Code 侧两种都支持，静态 token 只需一行：

```bash
claude mcp add --transport http memex https://memex.example.com/mcp \
  --header "Authorization: Bearer $MEMEX_TOKEN"
```

**其余必做项**（与 token 方案无关）：

- **`Origin` 校验**（规范 MUST）：白名单，不匹配直接 403。
- **HTTPS 强制**：反向代理终结 TLS；服务本身只监听内网端口。
- **每 token 限流 + clone 白名单**：`fetch_repo` 能 clone 任意 URL，是滥用面最宽的工具——
  限制并发与仓库大小上限，避免被当作免费 clone 代理。
- **`analyze_repo` 类危险工具默认禁用**（沿用 recall 已有的 gate 思路）。

### 4.5 存储从「用户目录」变成「服务器资产」

远程后 `~/.memex` 的定位要变：**它不再是「我的个人缓存」，而是「服务端数据库」**。

| 项 | 本地 stdio | 远程常驻 |
|---|---|---|
| `MEMEX_HOME` | `~/.memex` | `/var/lib/memex`（由 systemd 指定，勿放代码目录） |
| 备份 | 用户自己拷 | **运维责任**：`sqlite3 .backup` 定时 + 离线留存（见 §2.2 可重建性分层，只备份真源层） |
| SQLite 模式 | WAL（默认） | WAL；**注意 WAL 依赖同机文件系统**，网络盘（NFS）不安全 |
| 并发写 | 单进程 | 多客户端 → 保持「单写者」：SQLite 单写事务 + `busy_timeout`；写入集中在 `commit_report`，压力很低 |
| 索引重建 | 手动 | 建议加 `memex reindex` 的 cron（换模型后必须跑） |

**一个建议**：**索引与真源分库**。VibeCraft 就是这么做的（业务库 + 独立 `search.db`）。
远程形态下这点更有价值——`memex.db`（真源，体积小、千金难买）可以频繁备份，
`index.db`（chunk + 向量，体积大、可重建）可以随时`reindex`、甚至不进备份。
这比 §2.2 的「单文件」更稳妥；**单文件的简单性仍适合本地形态**，故设为可配置：
本地默认合一，远程推荐分离。

### 4.6 常驻运行：不要用 nohup

```
① systemd（推荐）
   [Unit] After=network.target
   [Service] User=memex  WorkingDirectory=/var/lib/memex
             Environment=MEMEX_HOME=/var/lib/memex
             Environment=MEMEX_EMBEDDER=...
             ExecStart=/usr/local/bin/memex serve-http --host 127.0.0.1 --port 8931
             Restart=always  RestartSec=3
   → 由反向代理（Caddy/Nginx）终结 TLS 并转发；服务不直接听公网

② 容器
   Dockerfile + `--model-cache` 卷；镜像里预下嵌入模型（否则首次启动会联网下载几百 MB）

③ 启动预热（后台线程，**不阻塞 `initialize`**）
   进程起来后在**后台线程**把嵌入模型 load 进内存（否则第一次 search 要等十几秒）。
   不能同步加载：stdio 客户端 `initialize` 超时通常 60s，而离线环境下 huggingface_hub
   联网回源会带退避重试挂几分钟。预热失败 → 首次需要嵌入的工具调用**显式报错**
   （不静默降级）并缓存失败，联网加载有墙钟上限（默认 20s）
```

**嵌入模型放哪**：sentence-transformers 模型约 470MB，应**烘进镜像/预置到服务器**，
不要在请求路径上下载。若服务器 CPU 弱、要更强的多语效果，就切 `MEMEX_EMBEDDER=http:<url>`
打到一台专门的 embedding 服务（§1.4），把重活分离出去。

### 4.7 远程带来的额外好处（顺带解决两个原方案痛点）

1. **批量分析不再是大问题。** 原方案里「agent 驱动导致批量分析变苦力活」是 B2 的短板。
   远程常驻后，服务器可以**自己在无人时段跑 batch 模式**（原 `llm.py` 路径），
   与 agent 交互式分析共用同一个库、同一套校验。短板被补掉。
2. **知识库天然共享。** 一个团队共用一个 memex，A 分析的仓库 B 立刻能召回——
   这正是「跨仓模式发现」最想要的样本来源（`min_repos=2` 更容易满足）。

### 4.8 如果哪天确实需要 Go/Rust

只有当下面**同时**成立时才值得考虑，且是**局部重写**而非全案换语言：

- 单机需要支撑**几十个并发 agent**，且
- Python 的 GIL / 内存占用实测成为瓶颈（先测，别猜），且
- 愿意接受「分析/校验/嵌入仍留在 Python，只把 HTTP 网关或 MCP 协议层用 Go 重写」

**即：即便到那时，也是把边缘层换掉，而不是把分析引擎换掉。** 本项目最值钱、最难写的部分
（契约校验、证据重切、语义召回、聚类）全在 Python 生态里，换语言的收益是负的。

---

## 五、与方案文档的对应

| 方案要求 | 本文落地 |
|---|---|
| 17 个工具 | §3.1 `mcp/` 目录；**逐个签名见 [`mcp-tools.md`](mcp-tools.md)**（G1/G2/G8/G22 已定） |
| 报告契约 | [`report.schema.json`](report.schema.json)（机器可读，G3）+ [`report-contract.md`](report-contract.md)（散文，G13） |
| 分析者归因与幂等键 | §2.2 `analyses`：`analyst` / `producer` / `contract_version`（G19/G21） |
| `code_spans` 多段 | §2.3 改动 1 |
| 校验在写入路径强制 | §2.3 + §1.3（语法校验分级） |
| `code_mismatch` 硬门禁 | §2.3 改动 3（`quality_json`） |
| 真语义模型默认 | §1.4 |
| RRF + kind 先验 + rerank | §1.1 + §2.4 + §2.5（G14） |
| 会话 TTL 与回收 | §2.6（**存 SQLite**，`session_stats` 归档，G18） |
| 仓库身份（改名/fork） | §2.2 `repos.identity_key` + §2.3 改动 4（G10） |
| VibeCraft 回填 | §2.8.2（`cli import-vibecraft`，G12） |
| pattern 参与召回 | §2.5 kind 先验 + §2.2 `chunks`（G14） |
| 离线 / 镜像 / 私有投喂 | §4.2 反向注 + 第四章（`upload_repo_bundle` T17，G22） |
| `resources`/`prompts` | §3.1 `mcp/` 注（不实现，G27） |
| 站点只做 catalog | §2.7（远程下的暴露面见 `solution-analysis.md` §5.9 注记） |
| 跨语言 | §2.5（非对称分解，V4 实验分支） |
| **远程常驻部署** | **第四章**（语言不变 §4.1 / `git bundle` §4.2 / Streamable HTTP §4.3 / 认证 §4.4 / 分库 §4.5 / systemd §4.6） |

对应 `decisions.md` 的 **E 组（E14–E18）**——本轮远程化新增的五条决策，理由记在那里。

---

## 附：尚未定案的事项

本文只写**已定**的部分。G 组**第 1 批**（G1 工具签名 / G2 错误模型 / G3 契约 schema / G13 语言 /
G19 归因与幂等键）、**第 2 批**（G4 凭据 / G5 护栏数字 / G6 SSRF / G7 限流 / G8 删除 /
G9 迁移 / G11 混模型 / G15 monorepo / G16 边界 / G17 重试 / G20 竞态 / G21 batch / G28 禁用清单）
与**第 3 批**（G10 身份 / G12 回填 / G14 pattern chunk / G18 会话回收 / G22 镜像与离线 / G27 不实现
resources·prompts）**已定案**，落为 [`mcp-tools.md`](mcp-tools.md)、[`report.schema.json`](report.schema.json)、
[`report-contract.md`](report-contract.md)、[`operations.md`](operations.md)，
决策理由见 [`decisions.md`](decisions.md) **G 组**。

仅剩**第 4 批** 4 项（质量阈值、聚类阈值、rerank 选型、远程会话清扫）**全部待实测**，逐条列在 [`gaps.md`](gaps.md)，
含证据位置、可选方案、我的建议与阻塞度分级。**定案后继续追加为 `decisions.md` 的 G 组。**
