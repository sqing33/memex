# 空缺清单（Gap Analysis）

> **这是什么**：对现有四份文档（`solution-analysis.md` / `decisions.md` / `tech-design.md` / `src/README.md`）
> 做一次全量核对，把「还没定、没写、没提过」的东西逐条列出来，每条给出**证据位置、为什么重要、
> 可选方案、我的建议、阻塞度**。
>
> **怎么做的**：对四份既有文档（1432 行）逐章通读 + 关键词全量 grep（`private repo` / `凭据` /
> `删除` / `schema_version` / `回填` / `resources` / `threshold` / `monorepo` / `max_files` /
> `rerank` / `失败` / `abandoned` / `语言中立` …），零命中的即列入下方。
>
> **状态图例**：🔴 阻塞（不定没法实现）/ 🟠 重要（实现前必须定）/ 🟡 可晚定 / 🔵 待实测（现在拍数字必错）。
>
> **关于文中 `file:line` 证据位置**：那是**产出本清单时**的文档行号。此后 `tech-design.md` 等
> 被大幅扩写，行号已漂移——**请按章节名定位，不要按行号跳转**。

**结论先行**：架构、数据模型、存储、语言、运输、认证、部署这七块**已经完备到可以开工**。
没定的是三类东西——**① 接口与契约的机器可读细节（唯一真正的硬空白）**、
**② 一圈边界情况与安全护栏**、**③ 必须跑起来才知道的数字**。

---

## 摘要

| # | 空缺 | 类别 | 严重度 | 状态 |
|---|---|---|---|---|
| G1 | 14 个工具的完整签名（入参/出参 JSON Schema） | 契约 | 🔴 | **✔ 已定** → `mcp-tools.md` |
| G2 | 工具的 MCP 元数据与错误模型 | 契约 | 🟠 | **✔ 已定** → `mcp-tools.md` |
| G3 | 报告契约的机器可读 schema | 契约 | 🟠 | **✔ 已定** → `report.schema.json` |
| G4 | 私有仓库与凭据 | 安全 | 🟠 | **✔ 已定**（第 2 批）→ `operations.md` §1.2 |
| G5 | 仓库护栏的具体数字 | 安全 | 🟠 | **✔ 已定**（第 2 批）→ `operations.md` §1.3 |
| G6 | 任意 git URL / 非 GitHub 宿主 / SSRF | 安全 | 🟠 | **✔ 已定**（第 2 批） |
| G7 | 限流与并发配额 | 安全 | 🟡 | **✔ 已定**（第 2 批）→ `operations.md` §1.3 |
| G8 | 删除与淘汰语义 | 生命周期 | 🟠 | **✔ 已定**（第 2 批）→ `mcp-tools.md` T15/T16 |
| G9 | `schema_version` 迁移策略 | 生命周期 | 🟠 | **✔ 已定**（第 2 批）→ `operations.md` §4 |
| G10 | 仓库身份（改名/转移/fork） | 生命周期 | 🟠 | **✔ 已定**（第 3 批）→ `repos.identity_key` |
| G11 | 嵌入模型迁移与「混模型库」 | 生命周期 | 🟠 | **✔ 已定**（第 2 批） |
| G12 | 从 VibeCraft 回填 | 生命周期 | 🟡 | **✔ 已定**（第 3 批）→ `cli import-vibecraft` |
| G13 | 报告与机制描述用哪种语言写 | 内容 | 🔴 | **✔ 已定** → `report-contract.md` §5 |
| G14 | `chunks.kind` 里的 `pattern` 要不要建 | 内容 | 🟡 | **✔ 已定**（第 3 批）→ 建 |
| G15 | monorepo / 子目录范围 | 内容 | 🟠 | **✔ 已定**（第 2 批） |
| G16 | 边界输入（空仓/非代码仓/超大文件） | 内容 | 🟠 | **✔ 已定**（第 2 批）→ `operations.md` §2 |
| G17 | 失败与重试语义 | 运行 | 🟠 | **✔ 已定**（第 2 批）→ `operations.md` §1.2 |
| G18 | 会话 `abandoned` 判定与 TTL 值 | 运行 | 🟡 | **✔ 已定**（第 3 批）→ `session_stats` |
| G19 | **分析者归因——`model` 字段谁填** | 运行 | 🔴 | **✔ 已定** → 幂等键改 `contract_version` |
| G20 | 会话期间仓库被更新（切片竞态） | 运行 | 🟠 | **✔ 已定**（第 2 批） |
| G21 | batch 模式与 agent 模式共库的语义 | 运行 | 🟠 | **✔ 已定**（第 2 批） |
| G22 | 网络受限环境（镜像 / 离线） | 运行 | 🟡 | **✔ 已定**（第 3 批）→ 镜像 + T17 |
| G23 | 质量指标阈值 | 数字 | 🔵 | **✔ 已定** → 硬门禁不定阈值 |
| G24 | 聚类 threshold | 数字 | 🔵 | **✔ 已定**（第 4 批）→ 0.60 + complete-linkage |
| G25 | rerank 选型 | 数字 | 🔵 | ✔ 已定；bge-reranker-base 有效，ms-marco 有害，默认保持 off 
| G26 | 远程会话清扫方式 | 数字 | 🔵 | **✔ 已定**（第 4 批）→ 双路清扫 + 并发修到 n=200 |
| G27 | MCP `resources` / `prompts` 能力用不用 | 能力面 | 🟡 | **✔ 已定**（第 3 批）→ 不实现 |
| G28 | 危险工具禁用清单 | 能力面 | 🟠 | **✔ 已定**（第 2 批）→ `mcp-tools.md` §3 |
| G29 | 三通道 RRF 投票权重 | 数字 | 🟠 | **✔ 已定**（第 4 批）→ substr 改条件投票（仅 keyword 返空时兜底） |

**共 29 项**：其中 🔴 3 项（G1 / G13 / G19）、🟠 16 项、🟡 6 项、🔵 4 项——即 G23 / G24 / G25 / G26，**四项全部已定**。
> G29 是 V3 检索阶段实测过程中新发现的空缺（检索投票权重），已当场实测定案。
第 3 批把 G10 的严重度由 🟡 上调为 🟠（改名会污染 `min_repos`，早点处理更便宜）。
**第 1 批已定 5 项**（G1 / G2 / G3 / G13 / G19）+ **第 2 批已定 13 项**
（G4–G9 / G11 / G15–G17 / G20 / G21 / G28）+ **第 3 批已定 6 项**
（G10 / G12 / G14 / G18 / G22 / G27）+ **第 4 批已定 5 项**
（G23 / G24 / G25 / G26 / G29）= 5 + 13 + 6 + 5 = **29 项，理由与结论记在**
**`decisions.md`** 的 **G 组**。其中 **5 项当初全部需实测**（G23 / G24 / G25 / G26 / G29，
V1–V4 跑起来用真实数据定，现在拍必错），现已全部定案；G25 收尾于真实 cross-encoder 实测。

---

## 第一部分 · 硬空白：接口与契约

### ✔ G1. 14 个工具的完整签名 —— 已定案

> **已定**：落为 [`mcp-tools.md`](mcp-tools.md)（当时 14 个工具的完整 JSON Schema + 14 条全局约定；
> 后 G8 补入 `forget_analysis` / `forget_repo`、G22 补入 `upload_repo_bundle`，现为 **17 个工具**）。
> 结论与理由见 [`decisions.md`](decisions.md) G 组 G1。以下为当时的问题陈述，保留备查。

**现状**：`tech-design.md` §五 的对应表里当时只写着「14 个工具 | §3.1 `mcp/` 目录；**签名细节见后续《MCP 工具签名》**」（现该表为「17 个工具」，指向本文 `mcp-tools.md`）。
`solution-analysis.md` §5.3 只有一张「工具名 + 一句话说明」的表，
**没有任何一个工具的入参、出参、错误形状**。

**为什么重要**：这是唯一一处「文档承诺了要写但还没写」的东西，也是 **V1 的直接前置**——
agent 要按流程调用，就必须先知道每个工具吃什么、吐什么。没有它，`mcp/` 目录无从下手。

**具体缺的是什么**（举几个必要的）：

| 需要定 | 例子 |
|---|---|
| 每个工具入参的字段名、类型、必选性、默认值 | `search_implementations(query, limit=8, repo?, language?, kind?, detail?)` —— 文档只给了名字 |
| 出参**对象结构**而不是「一句话」 | `search_implementations` 返回 `{count, results:[{score, repo, kind, title, section, tags, matched_by, excerpt, source:{path,start,end}, evidence:[…], code?}]}` —— 现在的表述是「跨仓结果 + 真实代码 + 证据」 |
| 命名风格统一 | `repo` vs `repo_id` vs `repo_url`；`limit` vs `top_k`；`detail` 的三档取值 |
| 分页 | `list_repos` / `list_patterns` 仓多了要分页吗？游标还是 offset？ |
| `next_step` 的字面形态 | §5.4 说「响应里带 `next_step`」，但它是字符串还是 `{action, args}` 结构？ |
| `evidence_pack` 的形状 | §5.3 说「目录树 + 入口点 + 符号清单」，但**三者各自的 JSON 结构**没定 |

**建议**：单独写一份 `docs/mcp-tools.md`，逐个工具给完整 JSON Schema，并先定几条**全局约定**：
命名统一（对外一律 `repo_id` + `repo_full_name` 双字段）、所有列表返回 `{count, items}` 信封、
`detail` 固定三档 `brief | normal | full`、错误一律走结构化对象（见 G2）。

**阻塞度**：🔴。建议**第一个**做。

---

### ✔ G2. 工具的 MCP 元数据与错误模型 —— 已定案

> **已定**：注解四件套（`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`）
> 每工具必填；`outputSchema` + `structuredContent` 必填；**协议错误只表示「用错工具」，
> 一切业务失败走 `{ok:false,error:{code,...}}`**。落为 [`mcp-tools.md`](mcp-tools.md) §1/§3。
> 理由见 [`decisions.md`](decisions.md) G 组 G2。

**现状**：零命中（`grep outputSchema|structuredContent|annotations` 无结果）。
`decisions.md` E15 只定了「只实现 `application/json` 分支」，**没定工具层的元数据与错误约定**。

**缺三样**：

1. **工具注解（annotations）**。MCP 2025-06-18 给工具加了
   `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` 四个提示位。
   本项目**天然需要它们**：`search_implementations` / `get_card` / `list_*` 是只读；
   `commit_report` / `fetch_repo` 有副作用；`validate_report` 是幂等的。
   关系 E16 的「危险工具默认禁用」——**没有注解就没法机械地判断哪些算危险**（见 G28）。
2. **结构化输出**。2025-06-18 支持 `outputSchema` + `structuredContent`。
   本项目出参都是结构化对象（不应逼 agent 解析散文），应当用起来；否则就是「JSON 塞在 text 里」。
3. **错误模型**。规范区分两类：**协议错误**（JSON-RPC `error`，如 -32602 未知工具）与
   **工具执行错误**（正常 result 里 `isError: true`）。
   本项目的语义是——`commit_report` 校验失败**不是**协议错误，是「正常返回、`ok:false` + 问题列表」；
   `fetch_repo` 网络失败也是「正常返回的失败」。**这条边界必须写死**，否则不同工具行为不一致。

**建议**：定三条全局约定——① 所有工具都带 annotations；② 所有工具都定义 `outputSchema`；
③ **协议错误只用于「调用方用错了工具」**（未知工具、参数类型错），
**一切业务失败都走 `{ok:false, error:{code, message, …}}` 的正常返回**。

**阻塞度**：🟠。

---

### ✔ G3. 报告契约的机器可读 schema —— 已定案

> **已定**：落为 [`report.schema.json`](report.schema.json)（JSON Schema draft 2020-12，
> `x-contract-id = "memex/report/1"`），散文解释见 [`report-contract.md`](report-contract.md)。
> 理由见 [`decisions.md`](decisions.md) G 组 G3。

**现状**：契约是**中文自然语言**描述的（`recall/analyze/schema.py` 的 `SCHEMA_DOC` 是中文散文；
`solution-analysis.md` §5.6 说「固定 4 个段落 + 5 个原理轴」）。
`validate_report` 是**手写校验器**，不是 schema 驱动。

**为什么重要**：agent 驱动的架构下，契约要**同时**给 agent 读（它要按契约产出）和给 server 用（校验）。
现在只有「给人/给 agent 读的散文」，缺**机器可读的那一份**（JSON Schema）。

**缺的**：`code_spans[]` / `evidence[]` / `cards[]` 的字段级 schema；
`kind` 五值的枚举与锚定例的**结构化存放位置**（`decisions.md` B6 说「契约里给定义+正例+反例」，
但没说正反例存在**哪里**——是嵌在 schema 里，还是 `help(report-contract)` 的文本里，还是单独文件？）。

**建议**：写一份 `contract/report.schema.json`，散文契约与它同源（`help` 渲染散文、
`validate_report` 用它做结构校验、`begin_analysis` 把它连同锚定例一起下发）。
**锚定例放 schema 的 `examples` / `x-examples` 字段**，这样三处不会漂移。

**阻塞度**：🟠。与 G1 同批做。

---

## 第二部分 · 安全与滥用面（远程部署后从「可选」升级为「必须」）

> 触发点：`decisions.md` E16 已确认「服务暴露公网后必须认证」。
> 但**认证只是第一层**，下面几条是同一批必须回答的，目前全部零命中。

### ✔ G4. 私有仓库与凭据 —— 已定案

> **已定（第 2 批）**：server 侧持凭据、per-host 映射，凭据绝不流经 MCP 参数；clone 用 `git -c http.extraheader` 不落盘；显式区分 401/403 与 404。见 [`decisions.md`](decisions.md) G4 + [`operations.md`](operations.md) §1.2。

**现状**：`grep GITHUB_TOKEN|private|credential|SSH` → **零命中**。
`fetch_repo(url)` 只设计为抓公开仓（clone / tarball / GitHub API 三条路）。

**为什么重要**：用户自己最想分析的，往往就是**自己的私有仓**（需求原话是「我分析了一个仓库」，
多数人第一反应是自己的项目）。而私有仓在远程形态下**必须由 server 侧持凭据**——
agent 在开发机上，token 不能经 MCP 参数传（会进日志、进对话历史）。

**需要定**：

| 问题 | 选项 |
|---|---|
| 凭据放哪 | ① server 环境变量（单 token，抓该账号能看的所有仓）② 配置文件里 per-host 的 token ③ 让 agent 传 token（**否决**——泄进对话历史） |
| clone 时怎么用 | HTTPS + `http.extraheader`（避免写进 `.git/config`）/ 部署密钥 / `GITHUB_TOKEN` 走 API 而非 clone |
| 粒度 | 全库级 vs 每仓级授权 |
| 失败语义 | 无凭据访问私有仓 → 明确报「需要凭据」，而不是莫名的 404（现在 `fetch` 失败会回落 tarball 再报 404，信息误导） |

**建议**：**server 环境变量 + per-host 映射**（`MEMEX_GIT_TOKEN__github_com=…`），
clone 用 `git -c http.extraheader=…` 且**不落盘到 `.git/config`**；私有仓抓取失败要**明确区分**
「404 不存在」与「403 无权限」并在返回值里说清楚。

**阻塞度**：🟠。若第一个要分析的仓就是私有仓，则升级为 🔴。

---

### ✔ G5. 仓库护栏的具体数字 —— 已定案

> **已定（第 2 批）**：`DEPTH_BUDGET` 迁入并重定义为「证据包详略 + 校验严格度」；附全局硬上限（单文件 1 MiB / bundle 512 MiB / 仓 2 GiB）；超限一律截断 + 显式标注。见 [`decisions.md`](decisions.md) G5 + [`operations.md`](operations.md) §1.3。

**现状**：`tech-design.md:462` 只说「限制并发与仓库大小上限」，**没有数字**。
而 recall 里已经有一组实测过的值（`DEPTH_BUDGET`：fast/standard/deep = 400/2000/4000 文件；
README 上限 3000/6000/12000 字符）——**它没被写进 memex 文档**，但 `analyses` 表却留着 `depth` 字段
（`tech-design.md:114`）。也就是说：**字段在，语义没跟过来**。

**需要定**：

- 文件数上限 / 单文件字节上限 / 仓库总字节上限（超了怎么办——截断并标 `truncated`，还是拒绝？）
- `depth` 三档在**新架构下的语义**：原 `DEPTH_BUDGET` 控制的是「喂给 server LLM 多少」，
  现在分析是 agent 做的，`depth` 该控制什么？（我的判断：控制**证据包的详略** + **校验的严格度**，
  而不是「给模型多少」）
- `request_repo_bundle` 的 bundle 大小上限（远程下发的传输量，见 G22）

**建议**：把 `DEPTH_BUDGET` 迁进 `tech-design.md` 并**重新定义语义**为「证据包详略」；
超上限的仓**截断 + 标记**而非拒绝（拒绝会让「分析大仓」直接不可用），
但截断必须**显式写进报告与召回结果**（否则 agent 以为分析是全量的）。

**阻塞度**：🟠。

---

### ✔ G6. 任意 git URL / 非 GitHub 宿主 / SSRF —— 已定案

> **已定（第 2 批）**：宿主白名单（`github.com`/`gitlab.com`/`gitee.com`）+ 强制 `https` + 解析后拒绝环回/私网/链路本地/云元数据段；`local:` 仅 stdio 形态可用。见 [`decisions.md`](decisions.md) G6。

**现状**：`repos.host` 字段存在（`tech-design.md:109`），说明设计上想到了多宿主，但
**`fetch_repo` 接受什么形状的 URL 完全没定**。需求原话是「填 GitHub 项目的地址」，
`tech-design.md:393` 的 bundle 示例也假设了 GitHub。

**为什么重要**（远程形态下的安全问题）：
server 会去 clone **用户给的任意 URL**——这是一个典型的 **SSRF 面**：
`http://169.254.169.254/…`（云元数据）、`http://localhost:6379`、内网地址都可能被探。
`decisions.md` E16 提到「仓库大小上限」，但**没提 URL 校验**。

**需要定**：

| 问题 | 选项 |
|---|---|
| 支持哪些宿主 | ① 只 GitHub ② GitHub + GitLab + Gitee（白名单）③ 任意 git URL（需要更强的护栏） |
| URL 校验 | 协议白名单（`https` only，禁 `file://` / `git://` / `http://`）、**解析后禁内网/环回/链路本地地址**、禁自定义 `GIT_SSH_COMMAND` |
| 非 GitHub 仓的元数据 | `stars` / `license` / `language` 从哪来（GitHub API 只覆盖 GitHub） |
| 本地路径 | `local:` 前缀与「裸目录路径」现在**可以**被当成本地仓（recall 已修）——**远程形态下必须禁掉**，否则 agent 能读服务器任意目录 |

**建议**：远程形态**白名单宿主 + 强制 `https` + 解析后地址段黑名单**；
`local:` 路径解析**仅在 stdio 本地形态启用**，`serve-http` 下一律拒绝。
非 GitHub 仓的元数据降级为「git 能拿到的」（无 stars）。

**阻塞度**：🟠。与 G4 同批。

---

### ✔ G7. 限流与并发配额 —— 已定案

> **已定（第 2 批）**：三类闸分开——请求 30 QPS/token、**clone 全局并发 3**、embed 并发 1；超限返回 `rate_limited` + `details.retry_after_seconds`。见 [`decisions.md`](decisions.md) G7 + [`operations.md`](operations.md) §1.3。

**现状**：E16 提到「每 token 限流」，**维度与数值未定**；`tech-design.md` §4.3 只说用
`ThreadingHTTPServer` 处理并发。

**缺**：限流按什么维度（请求数 / 并发 clone 数 / 并发分析会话数）；
clone 是最重的操作（吃磁盘 + 带宽 + 时间），**必须有独立的并发闸**，否则一个 agent 连开 5 个 clone 就把服务器拖垮；
`embedding` 也是重活（见 §4.3 的线程模型）。

**建议**：三类闸分开——① HTTP 请求 QPS（每 token）；② **并发 clone 数**（全局，建议 2~3）；
③ 并发 embedding 批次数（全局 1，或按 CPU）。数值待 V1 实测（🔵）。

**阻塞度**：🟡（机制要定，数值后补）。

---

## 第三部分 · 数据生命周期

### ✔ G8. 删除与淘汰语义 —— 已定案

> **已定（第 2 批）**：新增 `forget_analysis` / `forget_repo`（破坏性、默认禁用、须 `confirm:true`），硬删 + 级联 + 自动重跑聚类；**不引入草稿暂存表**。工具面 14 → **16**（后 G22 再加 `upload_repo_bundle`，现 **17**）。见 [`decisions.md`](decisions.md) G8 + [`mcp-tools.md`](mcp-tools.md) T15/T16。

**现状**：**零命中**。`grep 删除|delete|废弃|移除` 无结果。
（`abandoned` 是**会话**状态，不是仓库。）

**为什么重要**：这是知识库的**基本操作**，缺了会出真问题——
分析错了要撤销、仓库归档了要下架、`force` 覆盖后旧 analysis 怎么办、
`patterns` 是派生的（删了仓要不要重聚类）。

**需要定**：

| 操作 | 语义 |
|---|---|
| 删一个**分析**（analysis） | 级联删 `features` / `cards` / `evidence` / `chunks` / 向量；`patterns` 失效 → 需重聚类 |
| 删一个**仓库** | 级联删其上全部 analyses + `repos` 行 + 克隆缓存目录 |
| **软删 vs 硬删** | 建议硬删（本地/自用场景，无审计需求），但**先确认**——远程共享库可能想留痕 |
| **质量门禁落库前**的暂存 | `commit_report` 校验不过时**不落库**，那 agent 的草稿存哪？（现在答案：不存，agent 自己拿着。要确认这是不是有意的） |
| 淘汰后的 `patterns` | 重算（`discover` 本身就是幂等重算，recall 已验证） |

**建议**：加 `forget_repo(repo_id)` / `forget_analysis(analysis_id)` 两个工具（**破坏性**，见 G2 注解），
硬删 + 级联 + 重聚类；所有删除**要求显式确认参数**（如 `confirm: true`）。工具面 14 → 16。

**阻塞度**：🟠。

---

### ✔ G9. `schema_version` 迁移策略 —— 已定案

> **已定（第 2 批）**：启动比对 `meta.schema_version`；更高→拒绝启动，更低→有迁移函数则自动迁移否则提示 `memex migrate`；真源写显式迁移（大改走「导出→重建」），索引层直接 `reindex`。见 [`decisions.md`](decisions.md) G9 + [`operations.md`](operations.md) §4。

**现状**：`meta(key,value)` 里有 `schema_version` 字段（`tech-design.md:108`、recall 里是 `2`）；
`tech-design.md:286` 的模块列表里出现了「`db.py`（DDL+迁移）」字样，
但**全文没有任何迁移策略**——版本不匹配时的行为、迁移函数、`cli` 入口，一概未定。

**为什么重要**：契约几乎一定会改——本方案自己就已经改过一次大形状
（卡片从「单连续区间」改成 `code_spans` 有序段列表，见 A1）。旧库怎么办？

**需要定**：

- 版本不匹配时的行为：① 拒绝启动并提示 ② 自动迁移 ③ 允许读、拒写
- 迁移是**前向**（旧库→新库）还是**重建**（导出真源 → 建新库 → 重灌索引）
- 由于索引层可重建（`tech-design.md:147` 的可重建性分层），**真源层的迁移**才是难点

**建议**：**显式迁移 + 拒绝静默降级**。真源层写 `migrate_<from>_<to>()` 函数，
**必要时用「导出→重建」**（因为真源就是 `analyses` 报告 JSON + `cards`/`evidence`，
天然可重灌）；`cli` 加 `memex migrate`。索引层直接 `reindex`。

**阻塞度**：🟠（V1 就要有 `schema_version` 检查，否则以后没法安全升级）。

---

### ✔ G10. 仓库身份（改名 / 转移 / fork） —— 已定案

> **已定（第 3 批）**：`repos.identity_key`（GitHub 数字 `id`，UQ）作真身份锚；`repo_id` 创建后不可变；改名/转移**合并**到既有 `repo_id`（保留历史分析），`full_name` 只作展示 + `aliases_json` 记轨迹；fork 记 `fork_of` 但不去重，聚类按 `source_group = COALESCE(fork_of, identity_key)` 计 distinct。见 [`decisions.md`](decisions.md) G10 + [`tech-design.md`](tech-design.md) §2.2。

**现状**：`repos.full_name UQ`。**没说**改名/转移后怎么办（零命中）。

**问题**：GitHub 上 `old/name` 改名后，clone 会**重定向**到 `new/name`——
此时 `git remote get-url origin` 仍是旧 URL，但实际内容是新仓。库里会**出现两条**
（旧 `full_name` 一条、新 `full_name` 一条），模式聚类的 `min_repos=2` 会被**同一个仓的两次分析**
满足——**污染 pattern**（这正是 V5「跨语料去重」想解决的问题，但改名是更早、更常见的入口）。

**建议**：`fetch_repo` 时**对比 clone 后的真实远端 URL**（`git config --get remote.origin.url` +
`git rev-parse` + 重定向后的 actual URL），不一致则**更新 `full_name` 并合并**；
fork 关系（GitHub API 的 `fork: true` + `parent`）**记录但不去重**，在聚类时把同源仓的票合并。

**阻塞度**：🟡（但若 V4 聚类质量要可信，提前到 🟠 更好）。

---

### ✔ G11. 嵌入模型迁移与「混模型库」 —— 已定案

> **已定（第 2 批）**：`meta.embedder` 是唯一当前索引模型；`chunk_vectors` 加 `embedder` 列；**写入与检索两端都断言**，不一致 → `conflict` 并提示 `memex reindex --embedder X`。见 [`decisions.md`](decisions.md) G11。

**现状**：`tech-design.md:80` 定了**原则**——「建库时固定模型；换模型需 `reindex`；
**不做多模型并存**」。但**没有定「万一混了怎么办」的检出与阻断机制**，也没定维度变化（512→768）时
`chunk_vectors.dim` 的处理。

**问题**：向量只由 embedder 产生，而库里的向量是**逐次分析累积写入**的。
一旦有人在没跑 `reindex` 的情况下换了模型（或从别处拷了一个库进来），
`chunk_vectors` 里就会**同时存在两种嵌入空间的向量**——余弦比较毫无意义，
而且**不会报错**，只会静默把召回质量毁掉。「不做多模型并存」是**意图**，不是**机制**。

**需要定**：

- `chunk_vectors` 是否加 `embedder` 列（每个 chunk 记自己被哪个模型编码）
- 检索时：① 混模型库直接**拒绝检索并提示 `reindex`** ② 按当前 embedder 过滤（旧向量不可召回）
- 维度变化时 `chunk_vectors.dim` 是**固定列**（则跨维度直接不可能）还是每行独立

**建议**：**`meta.embedder` 只有一个值（即「当前索引模型」）**，把它作为**写入时的断言**——
每次写向量前核对 embedder 一致，不一致直接拒绝写入；检索入口**先断言全部向量与 `meta.embedder` 一致**，
不一致则拒绝并给出「跑 `memex reindex --embedder X`」的指引。这样把「不做多模型并存」从意图变成**强制**。

**阻塞度**：🟠。

---

### ✔ G12. 从 VibeCraft 回填 —— 已定案

> **已定（第 3 批）**：`cli import-vibecraft <path>` 最佳努力搬运 + 强制过 `validate_report` + 从真实文件重切证据；过不了的卡片**丢弃并计数**；`mechanism_desc` 非语言中立者入库但标 `reindex_state='pending'` 排除出检索；产物 `producer=batch` / `analyst=vibecraft-import`；`--dry-run` 必做。见 [`decisions.md`](decisions.md) G12。

**现状**：`solution-analysis.md:386` 只有一句「报告契约可比、可互相导入」，
**没有具体回填方案**。

**为什么重要**：用户已经用 VibeCraft 分析过若干仓库。若能回填，**省掉重分析的全部 agent 成本**——
这是「复用契约而非代码」这条决策最直接的兑现。

**需要定**：

- VibeCraft 的库在哪、什么格式（SQLite？报告 JSON？）——需要**实地核对 `search.db` / 主库的 schema**
- 字段映射表（VibeCraft 报告 4 段落 + 5 轴 → memex 契约）
- **校验**：回填也要过 `validate_report`（否则等于绕过证据链门禁）；VibeCraft 的卡片行号是否
  能从它的真实文件重切（它存了 `evidence` 与 `code`）、`code_mismatch` 是否为 0
- 回填的 `model` 字段记什么（见 G19）

**建议**：写 `cli import-vibecraft <path>`，**复用同一个 `validate_report`**，
把「是否全部命中证据链」作为回填成功的判据。

**阻塞度**：🟡（但成本低、收益直接，值得早做）。

---

## 第四部分 · 内容与语言

### ✔ G13. 报告与机制描述用哪种语言写 —— 已定案

> **已定：中英分层。** `mechanism_desc` 与 `intent` **强制英文**（建向量/意图探针），
> `summary`、五轴段落、`title` 用**用户母语**；长度阈值从「字符数」改为「信息单元数」
> （`units = CJK 字符数 + 拉丁/数字连续串数`，`min_principle_units = 40`）。
> 落为 [`report-contract.md`](report-contract.md) §5 与 [`report.schema.json`](report.schema.json) 的
> `x-notes` / `x-counting`。理由见 [`decisions.md`](decisions.md) G 组 G13。

**现状**：**零命中**。全案的核心机制是「对**语言中立的机制描述**建向量」
（`solution-analysis.md:127/179`、`tech-design.md:270`），但**从没说过这些描述用什么自然语言写**。

**为什么重要**：这是**跨语言聚类的可操作性前提**，而且影响三件事：

1. **聚类**：如果 A 仓的报告是中文、B 仓是英文，即使讲的是同一个机制，
   多语种模型也可能把它们分得比同语言的不同机制更远——**`min_repos=2` 会永远不满足**。
2. **检索**：用户用中文提问、库里是英文描述，跨语召回质量直接掉。
3. **契约**：`validate_report` 的段落长度阈值（`min_principle_chars=40`）**是按字符算的**——
   中文 40 字与英文 40 词信息量差一倍，阈值对两种语言不等价。

**可选项**：

| 方案 | 说明 |
|---|---|
| **A. 固定一种语言（英文）** | 跨语言一致性最好（英文语料最多、模型最强）；代价：中文用户读报告要翻译 |
| **B. 固定一种语言（中文）** | 对用户友好；代价：非中文仓库的术语翻译可能失真，且模型对中文机制描述的质量略低 |
| **C. 双语：机制描述双语，报告原文单语** | 描述层用双语并排（召回命中任一都行），报告保持原文 |
| **D. 不管** | 明显不行——直接破坏核心机制 |

**我的建议**：**C 的简化版——机制描述（`mechanism_desc`，即建向量的那段）强制英文
（或「一个语言中立短语 + 一句英文」），报告与卡片正文用用户母语**。
理由：向量空间只需要「一句浓缩描述」保持一致，而不需要整篇报告一致；
这样既保证跨语言聚类，又不牺牲人读报告的体验。
**并据此把 `min_principle_chars` 改为按「语言自适应」或改成「去掉标点后的 token 数」**。

**阻塞度**：🔴——它决定了 `mechanism_desc` 的生成规则，而 `mechanism_desc` 是**整个方案建索引的那段文本**。

---

### ✔ G14. `chunks.kind` 里的 `pattern` 要不要建 —— 已定案

> **已定（第 3 批）**：**建**。每 pattern 一个 `kind='pattern'` chunk（文本 = 成员 `mechanism_desc` 合成）；重聚类按 key 整批覆盖；RRF 不加权，融合后施加 `kind_prior`（pattern 0.6 < card 1.0）。见 [`decisions.md`](decisions.md) G14 + [`tech-design.md`](tech-design.md) §2.2/§2.4。

**现状**：`tech-design.md:136` 的枚举列了 `kind ∈ feature | card | pattern | report_section`，
但 recall 实测**只建了 feature / card / report_section 三类**（pattern 未建）。
memex 文档**没表态** pattern 是否作为 chunk 参与召回。

**问题**：`patterns` 表本身有 `title` / `tags_json`，
如果不建 chunk，那**跨仓模式簇本身不可被检索**——用户只能检索到成员卡片，看不到「这是个模式」。

**建议**：**建**。每个 pattern 一个 `summary` chunk（kind=`pattern`，`text` = 由成员卡片的
`mechanism_desc` 合成的模式描述），这样「有没有人做过 X」这类查询能直接命中模式层，
且排序上可以给 pattern 一个**较低的基础权重**（避免模式永远压过具体卡片）。
**注意**：pattern chunk 是**派生+易变**（重聚类就变），重建时必须整批替换。

**阻塞度**：🟡。

---

### ✔ G15. monorepo / 子目录范围 —— 已定案

> **已定（第 2 批）**：`fetch_repo(repo_url, subpath?)`；workspace 自动识别仅用于分组/限深不改范围；**一个 monorepo = 一个 repo**（否则 `min_repos>=2` 被同仓多包满足而污染模式）。见 [`decisions.md`](decisions.md) G15。

**现状**：**零命中**（只有竞品 `code-graph-rag` 被描述为「monorepo 导向」）。
需求原话是「填 GitHub 地址」，没讨论过**一个仓里有多个项目**怎么办。

**问题**：monorepo 里「功能」的边界与「包」的边界重合但不相同；
`get_evidence_pack` 的「目录树 + 入口点」在 monorepo 下会**巨大**（吃掉 agent 上下文，
正是最大风险）；而且 monorepo 的**证据路径需要相对哪个根**也很混乱。

**需要定**：

- 分析范围：整仓 / 指定子目录 / 自动识别 workspace（`package.json#workspaces` / `go.work` / `pyproject.toml`）
- `get_evidence_pack` 在 monorepo 下的行为：按包分组？分批？只给顶层？
- 一个 monorepo 算**一个 repo** 还是**多个**（影响 `min_repos=2`！）

**建议**：**`fetch_repo` 支持可选 `subpath`**（agent 指定要分析哪个子目录），
`get_evidence_pack` 按 workspace 边界**分组并限深**；monorepo 仍算一个 repo
（否则 `min_repos=2` 会被同一个 monorepo 的多个包满足，污染模式）。

**阻塞度**：🟠。

---

### ✔ G16. 边界输入 —— 已定案

> **已定（第 2 批）**：固定黑名单 ∪ `.gitignore`（尊重但不信）；空仓/非代码仓给 `warnings` 由 agent 判断；单文件 >1 MiB 跳过；无 license 显式 `null`。见 [`decisions.md`](decisions.md) G16 + [`operations.md`](operations.md) §2。

**现状**：**零命中**。文档没提过非正常输入。

| 输入 | 现在会怎样 | 需要定 |
|---|---|---|
| 空仓 / 只有 README | 证据包几乎为空 | 明确拒绝还是「分析产物 = 一个 feature」？ |
| 非代码仓（文档站、数据集） | 符号清单为空 | 拒绝并说明 |
| 单个超大文件（10 万行） | 切片可能很慢/很大 | 单文件上限 + 超限拒绝切片 |
| 二进制/生成文件（`dist/`、`vendor/`、`node_modules/`） | 污染符号清单与证据 | **忽略规则**（`.gitignore` 尊重？固定黑名单？） |
| 巨型仓（linux kernel 级） | clone 慢、证据包爆 | 与 G5 上限联动 |
| 无 license / 非标准 license | `license` 字段空 | 召回结果里怎么表达「来源许可不明」 |

**建议**：定一份**固定忽略规则**（`.gitignore` + 语言惯例黑名单 + 单文件/单目录上限），
**尊重 `.gitignore` 但不信它**（分析者还是能显式 `read_file_slice` 取到）；
边界输入一律**「能分析就分析，但把 `warnings[]` 明确返回」**，不静默失败。

**阻塞度**：🟠（证据包与校验器都会碰到）。

---

## 第五部分 · 运行语义

### ✔ G17. 失败与重试语义 —— 已定案

> **已定（第 2 批）**：clone 300s / 3 次指数退避（仅 5xx 与网络错误，404/403 不重试）；tarball 120s/1 次；embedding 60s/2 次；错误带 `retryable`；重活「先临时后原子切」，`fetch_repo` 幂等。见 [`decisions.md`](decisions.md) G17 + [`operations.md`](operations.md) §1.2。

**现状**：只覆盖了**校验失败**（`commit_report` 返回问题列表）。
**clone 失败 / 网络失败 / tarball 兜底失败 / embedding 失败**的重试策略**零命中**。

**需要定**：

- clone 失败：重试几次、退避多久（GitHub 偶发 5xx 很常见）
- 超时：clone / embedding / 单次 MCP 调用的超时各是多少（远程形态下 agent 在等）
- **部分失败**：`request_repo_bundle` 打包到一半失败、`reindex` 中途失败（索引写到一半）
- **幂等性**：失败的 `fetch_repo` 重试会不会产生两条 `repos` 行

**建议**：clone 重试「3 次指数退避」；所有重活（clone / embed / reindex）**先写临时状态再原子切**，
失败能干净重跑；错误对象带 `retryable: bool` 让 agent 知道该不该重试。

**阻塞度**：🟠。

---

### ✔ G18. 会话 `abandoned` 判定与 TTL —— 已定案

> **已定（第 3 批）**：`abandoned` **超时自动**（不新增工具）；TTL `MEMEX_SESSION_TTL_SECONDS=7200`（值待 V1 实测）；回收三步「归档统计 → 留痕 → 过期物理删」，`session_stats` **不删**；`begin_analysis` 对同 `(repo_id, commit_sha, contract_version)` 的未完会话返回既有会话。见 [`decisions.md`](decisions.md) G18 + [`operations.md`](operations.md) §1.5。

**现状**：`tech-design.md:224` 的状态机里有 `abandoned`，**判定条件没定义**；
`tech-design.md:230` 的远程 TTL 清扫是「若实测积压再加后台线程」。

**需要定**：TTL 具体值（建议按「一次分析的实际耗时」定，V1 实测 🔵）；
`abandoned` 是**超时**判定还是**显式**动作；超时后是删会话还是留痕（涉及 V1 的 turn/token 统计，
统计数据**不能因为会话被回收就丢**）。

**建议**：`abandoned` = **超时自动**；但**统计先落库再回收**（V1 的关键数据不能随会话蒸发）。
TTL 值待 V1。

**阻塞度**：🟡（值 🔵）。

---

### ✔ G19. 分析者归因与幂等键 —— 已定案

> **已定**：`analyses` 加 `analyst`（自由文本）与 `producer`（`agent|batch`）列；
> **幂等键从 `(repo_id, commit_sha, model)` 改为 `(repo_id, commit_sha, contract_version)`**。
> `analyst` 仅用于质量归因，不参与幂等。理由见 [`decisions.md`](decisions.md) G 组 G19。

**现状**：`analyses` 有 `model` 字段，且它是**幂等键的一部分** `(repo_id, commit_sha, model)`。
但 agent 驱动架构下——**server 根本不知道 agent 用的是哪个模型**。
文档**从没讨论过**这件事（`grep` 无命中）。

**为什么重要**（三个后果）：

1. **幂等键失效**：`model` 填不出来，「同 commit 同 model 内容不同要 `force`」这条规则（B7）无从执行。
2. **质量无法归因**：`quality_json` 里的 `code_mismatch` / 轴完整度，**分不清是哪个 agent/模型产出的**——
   `solution-analysis.md` §5.8 的可观测性目标（「分析质量」）**失去自变量**。
3. **batch 与 agent 两条路混库**（见 G21）时更需要它区分。

**需要定**：`model` 是 ① agent 自报（不可信但可用）② server 从 MCP 会话记录（也拿不到模型名）
③ 换个键：**用「分析者身份」替代「模型名」**（agent 自报 `analyst: {name, model?, version?}`）。

**我的建议**：`analyses` **加 `analyst` 列**（自由文本，如 `claude-code/opus-4.5` 或
`batch/sentence-transformers+x`），**幂等键改为 `(repo_id, commit_sha, contract_version)`**——
因为真正影响「报告是否可比」的是**契约版本**，不是模型名；
模型/agent 名归入 `analyst` 只用于质量归因，不参与幂等。

**阻塞度**：🔴——它同时是幂等键和可观测性的地基，且**改表结构**，越晚越贵。

---

### ✔ G20. 会话期间仓库被更新（切片竞态） —— 已定案

> **已定（第 2 批）**：会话绑定 commit；`commit_report` 前核对，不一致 → `stale_repo` + `{session_commit, current_commit}`，**会话保留**；绝不让 agent 看到假的 `code_mismatch`。见 [`decisions.md`](decisions.md) G20。

**现状**：**零命中**。B7 解决了「两个 agent 提交同一仓」的幂等，**没解决**
「agent 开会话后、提交前，克隆目录被别的 `fetch_repo` 更新或清掉」。

**问题**：`commit_report` 的校验核心是**「从真实文件按行号重切」**。
如果会话期间 `repos/<x>/` 被更新到新 commit，**行号全变**——
校验会报一堆 `code_mismatch`，但**不是 agent 的错**（它按开会话时的代码写的）。
更糟的情况：克隆目录被清掉，校验直接失败。

**建议**：会话**绑定 commit**——`begin_analysis` 返回 `commit_sha`；
`commit_report` 前**校验克隆目录仍是那个 commit**，不是则明确报
「仓库在分析期间被更新（会话基于 A，当前是 B）」并**保留会话**让 agent 决定：
重开会话 / 强制用当前版本（再校验一次）。**不要**让 agent 看到一堆假的 `code_mismatch`。

**阻塞度**：🟠（远程多客户端下极易触发）。

---

### ✔ G21. batch 模式与 agent 模式共库的语义 —— 已定案

> **已定（第 2 批）**：`producer ∈ {agent, batch}`；两路共用同一库与同一套校验；质量按 producer 分组；batch 的 `analyst` 记 `batch/<模型>`；**V1–V4 只用 agent，batch 排 V5**。见 [`decisions.md`](decisions.md) G21。

**现状**：`solution-analysis.md` §4.2 决定「以 B2（agent 驱动）为主叙事，
**B1（服务端 LLM）作为补充的批量模式保留**」，两者共用同一个库、同一套校验。
**但共库的具体语义没定。**

**缺**：

- 两条路产出的 analysis，在 `search` 结果里**要不要区分**（用户可能想知道「这条是机器批量的」）
- batch 用 server LLM 时，`analyst` / `model` 填什么（与 G19 联动）
- **质量差异**：batch 的 `code_mismatch` 与 agent 的相比如何——**需要分别统计**，
  否则两条路的质量数据混在一起，谁好谁坏看不出来
- batch 是否也算「agent 驱动」的可观测性体系的一部分（我认为**是**，同一套指标）
- **优先级**：batch 是 V1 就该有，还是 V5？（文档没排序）

**建议**：`analyses` 加 `producer` 枚举 `agent | batch`；质量指标**按 producer 分组统计**；
batch 排到 **V5**（它与 V5 的「规模」目标天然同批，V1~V4 用 agent 即可）。

**阻塞度**：🟠（字段要早加，实现可晚）。

---

### ✔ G22. 网络受限环境（镜像 / 离线） —— 已定案

> **已定（第 3 批）**：① `MEMEX_GIT_MIRROR` 前缀重写（探测失败回落直连，凭据仍按原始 host 取）；② 新增 MCP 工具 `upload_repo_bundle`（**工具面 16 → 17**，兼解 G4 的「开发机有凭据/服务器没有」变体）；③ 嵌入模型不可用**拒启 + 给三条出路**，只有显式 `hash:512` 才降级。见 [`decisions.md`](decisions.md) G22 + [`mcp-tools.md`](mcp-tools.md) T17。

**现状**：**抓取侧的镜像/离线路径零命中**（`grep 镜像|mirror` 只命中 `tech-design.md:496/502`
的「嵌入模型烘进镜像」，与「仓库抓取走镜像」无关）。

**问题**：远程服务器在**公司内网 / 无外网 / GitHub 被限**的环境下，
`fetch_repo` 的 clone 与 tarball 两条路都断了。另外**嵌入模型约 470MB**
（`tech-design.md:73`）——`tech-design.md:496/502` 已建议「烘进镜像 / 预置到服务器」，
**但没说预置失败时怎么办**。

**建议**：`fetch_repo` 支持 ① 可配的 GitHub 镜像（`MEMEX_GIT_MIRROR`）②
**手动投喂**（agent 在开发机 clone 好，用 `request_repo_bundle` 的**反向**：
`upload_repo_bundle(file)` 送上去）。第二条其实**同时解决了 G4 的一个变体**
（开发机有凭据、服务器没有）。嵌入模型预置失败 → 明确报错并给
`MEMEX_EMBEDDER=http:<url>` 的降级指引，**不静默退到 hash**（D1 已禁止 hash 作默认）。

**阻塞度**：🟡。

---

## 第六部分 · 待实测的数字（现在拍必然错）

### 🔵 G23. 质量指标阈值 —— **已定：定成硬门禁，不定阈值**

**定案**：`code_mismatch == 0` 保持硬门禁；其余质量项做成**校验器规则门禁**，
不定分数阈值。五轴雷同新增 `axis_reuse` 问题码。

**理由**：`axis_completeness` / `evidence_coverage` 在提交路径上**结构上恒为 1.0**
（commit 只在 is_valid=True 时执行，校验器已拦住空轴与缺证据），没有可用的分布。
照它们定阈值 = 定出一个永远通过的空门禁。五仓实测三项质量指标零区分度，
唯一有变化的是 PTNexus `reusable_rate=0.889`。

**实测**：九类坏样本全部被拦（此前唯一漏网的是「五轴正文一字不差」），
基准 axum 报告仍放行。详见 `decisions.md` G23 条目。

### 🔵 G24. 聚类 threshold
文档只固定了 `min_repos=2`（语义过滤器），**相似度阈值本身没定**
（`0.62` 只活在 recall 旧代码里）。它**强依赖嵌入模型**——换模型必须重定。
**落地动作**：V3/V4 用真模型 + 人工抽检（Top-3 有多少真可借鉴）来定。

### 🔵 G25. rerank 选型
> 15 探针真模型实测：ms-marco-MiniLM-L-6-v2 `有害`（top-1 10/15 → 4/15），bge-reranker-base `有效`（top-1 10/15 → 13/15，MRR 0.776 → 0.910）。

### 🔵 G26. 远程会话清扫方式
`tech-design.md:230`「若实测积压，加低频后台线程」——**待实测**。
一并要测：`ThreadingHTTPServer` 在真实多客户端下的表现（是否够），
以及 §4.8「何时才值得换 Go/Rust」的判断依据（也是「先测，别猜」）。

---

## 第七部分 · 能力面（用不用）

### ✔ G27. MCP `resources` / `prompts` 能力 —— 已定案

> **已定（第 3 批）**：**不实现**，只实现 `tools`。`resources` 会把整份报告当资源读进上下文（正面踩中硬约束）；`prompts` 会与 server 的 `next_step` 形成两个真源。客户端发 `resources/*`、`prompts/*` → `-32601`。见 [`decisions.md`](decisions.md) G27。
**零命中**。recall 骨架里两者都回空。memex 未表态。
**判断**：`help(topic)` 已经覆盖「给 agent 读流程」；`resources` 对本项目**基本无用**
（内容都在工具里）；`prompts` 可能有点用（把「做一次分析」做成一个 prompt 模板，
部分客户端能一键唤起）。
**建议**：**明确写「不实现」**并记理由（保持工具面窄、客户端兼容性最大），
而不是留空——留空会让下一个实现者又纠结一遍。

### ✔ G28. 危险工具禁用清单 —— 已定案

> **已定（第 2 批）**：按 annotation 机械导出四类（read/write/network/destructive），`destructive` **默认关**；开关 `MEMEX_TOOLS`；被禁调用返回 `disabled` + `details.enabled`。见 [`decisions.md`](decisions.md) G28 + [`mcp-tools.md`](mcp-tools.md) §3。
E16 说「危险工具默认禁用」，**但没列清单**，也没说**怎么禁用**（env 开关？按 token 配？）。
**建议**：`fetch_repo`（出网）/ `request_repo_bundle` / `commit_report`（写库）/
`forget_*`（破坏性，见 G8）默认**分级**——
`read`（默认开）/ `write`（默认开）/ `network`（默认开）/ `destructive`（**默认关**）；
用 `MEMEX_TOOLS=read,write,network` 收口，并与 G2 的 annotations 一一对应。

---

## 附一 · 建议的决策与落地顺序

| 批次 | 内容 | 理由 | 进度 |
|---|---|---|---|
| **第 1 批（现在必须定）** | G1 工具签名 · G2 元数据与错误模型 · G3 契约 schema · G13 语言 · G19 归因/幂等键 | 都是「改表结构 / 定接口」的性质，**越晚越贵**；G1/G3 是 V1 前置 | **✔ 5/5 已定** |
| **第 2 批（实现前定）** | G4/G5/G6/G7 安全护栏 · G8 删除 · G9 迁移 · G11 混模型 · G15 monorepo · G16 边界 · G17 重试 · G20 竞态 · G21 batch 语义 · G28 禁用清单 | 都是「写代码时必须知道」的语义 | **✔ 13/13 已定** |
| **第 3 批（可晚定）** | G10 仓库身份 · G12 回填 · G14 pattern chunk · G18 会话 · G22 镜像 · G27 resources/prompts | 不影响主链路 | **✔ 6/6 已定** |
 | **第 4 批（待实测）** | G23 质量阈值 · G24 聚类阈值 · G25 rerank · G26 清扫 | 现在拍数字必然错，等 V1~V4 | ✔ 4/4（G23~G26 全部已定） |

> 第 1、2 批的定案结果见 [`decisions.md`](decisions.md) **G 组**：第 1 批落为
> [`mcp-tools.md`](mcp-tools.md) / [`report.schema.json`](report.schema.json) / [`report-contract.md`](report-contract.md)，
> 第 2 批的**可操作细节**落为 [`operations.md`](operations.md)（`forget_*` 签名落在 `mcp-tools.md` T15/T16）。

## 附二 · 这次核对**没有**发现空缺的部分（已完备）

为免以后重复怀疑，明确记下**已经够了**的：

- 需求拆解、生态调研、路线对比、B1/B2 取舍 —— `solution-analysis.md` 一~四节
- 架构、分工边界、会话状态机、写入路径校验 —— 五节
- 语言选型、存储与数据模型（13 表）、依赖分层、分发 —— `tech-design.md` 一~三章
- 远程部署：运输层（含两个坑）、认证两档、存储分离、systemd、何时才换语言 —— 第四章
- 质量优先原则 + 成本偏见复审 + 21 条早期决策 + **G 组共 29 条已定**（第 1–3 批全定 + 第 4 批 G23 / G24 / G26 / G29） —— `decisions.md`
- 环境变量 / 忽略规则 / 子命令 / 启动检查 / 危险操作 / 故障处置 —— `operations.md`

---

*本清单与 `decisions.md` 互补：`decisions.md` 记「已定 + 理由」，本文记「未定 + 建议」。
第 1 批（G1/G2/G3/G13/G19）、第 2 批（G4–G9/G11/G15–G17/G20/G21/G28）与
第 3 批（G10/G12/G14/G18/G22/G27）已定案并追加为 `decisions.md` 的 **G 组**（合计 29 条）；
第 4 批的 `G23 / G24 / G25 / G26 全部已定`（质量定成硬门禁；聚类 0.60 + complete-linkage；rerank 选 BAAI/bge-reranker-base 且默认 off；清扫双路 + 并发 n=200，见 `decisions.md`），**本清单 29 项空缺至此全部定案。**

