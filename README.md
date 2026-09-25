# memex

跨仓库「实现借鉴」知识库 —— **以 MCP 服务形式提供给 coding agent**。

分析、判断、写入全部由调用 MCP 的 agent 完成；本项目只负责**暴露接口、存取内容、强制校验**，
外加一个可选的 Web 页面展示已有数据。

---

## 为什么叫 memex

Vannevar Bush 1945 年在 *As We May Think* 里描述的 Memex，是「个人知识库 + 关联检索」——
其核心机制正是**从一份文档沿关联轨迹跳到另一份**。这正是本项目要做的事：把已分析仓库里的
实现，沿「功能相似」的轨迹关联起来，供后续任务借鉴。

名字短、好拼（`memex serve-mcp`），无重名冲突。

---

## 目录

```
memex/
  docs/          方案与技术设计（md）
  src/           代码（V1 垂直切片已落地，见 src/README.md）
```

| 文件 | 内容 |
|---|---|
| `docs/solution-analysis.md` | 需求拆解、生态调研、路线对比、agent 驱动的推荐方案、风险与最终完整形态 |
| `docs/decisions.md` | 未定项定案（A–E 五组共 21 条 + **G 组第 1–3 批 24 条**）+ 设计与取舍原则（质量优先） |
| `docs/tech-design.md` | 技术设计：语言选型、存储与数据模型、依赖分层、基建与分发、**远程常驻部署** |
| `docs/gaps.md` | **空缺清单**：28 项「还没定 / 没写 / 没提过」的条目，逐条给证据、方案与建议顺序 |
| `docs/mcp-tools.md` | **工具契约**：17 个工具的完整入参/出参 JSON Schema + 全局约定（G1/G2/G8/G22 已定） |
| `docs/report.schema.json` | **报告契约（机器可读）**：JSON Schema draft 2020-12，`memex/report/1`（G3 已定） |
| `docs/report-contract.md` | **报告契约（散文）**：4 个 H2 骨架、五原理轴、卡片五类正反例、中英分层语言规则（G13 已定） |
| `docs/operations.md` | **运维与配置**：环境变量、忽略规则、子命令、启动检查、危险操作、故障处置（G 组第 2–3 批） |
| `src/README.md` | 模块划分、`recall/` 复用关系与测试命令（V1 垂直切片已落地） |

> `decisions.md` 记「已定 + 理由」，`gaps.md` 记「未定 + 建议」——两份互补。
> **28 项空缺已定 24 项**：第 1 批（G1/G2/G3/G13/G19）、第 2 批（G4–G9/G11/G15–G17/G20/G21/G28）、
> 第 3 批（G10/G12/G14/G18/G22/G27），落为 `mcp-tools.md` / `report.schema.json` /
> `report-contract.md` / `operations.md`；**只剩第 4 批 4 项待实测**（质量阈值 / 聚类阈值 /
> rerank 选型 / 远程会话清扫），跑起来拿到真实分布后定。

---

## 方案一句话

现有工具（4 万★ 级的 codebase-memory-mcp、serena 等）都在做「读懂一个仓库」；
本项目做的是没人做好的一环：**写新功能时，按功能意图召回已分析仓库里的类似实现，并让 agent 自动借鉴**。

三个设计支点：

1. **知识粒度定在「功能级」**，不是仓库级也不是文件级 —— 只有它能对上「某个功能」这个需求单元
2. **对语言中立的「机制描述」建向量**，而非对代码建向量 —— 这是跨语言匹配的前提
3. **证据链强制校验** —— 落库前路径必须真实存在、代码必须从真实文件重切，否则 agent 会借鉴幻觉

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

## 工具面

| 类别 | 工具 |
|---|---|
| 机械 | `fetch_repo` · `get_evidence_pack` · `read_file_slice` · `request_repo_bundle`（远程） · `upload_repo_bundle`（离线/私有） |
| 写入（带校验） | `begin_analysis` · `validate_report` · `commit_report` |
| 召回 | `search_implementations` · `get_card` · `list_patterns` · `get_report` · `list_repos` · `recall_stats` |
| 元 | `help` |
| 破坏（**默认禁用**） | `forget_analysis` · `forget_repo` |

> `request_repo_bundle` 只在远程形态下需要：服务器把克隆打成 `git bundle` 单文件，
> agent 拉回本地后用自己的 `Read`/`Grep` 直读（见 `docs/tech-design.md` §4.2）。
> `upload_repo_bundle` 是它的**反向**：开发机 `git bundle` 好送进 server，用于无外网/私有仓
> （见 `docs/decisions.md` G22）。`resources` / `prompts` 不实现（G27）。
> 破坏类工具需显式列入 `MEMEX_TOOLS` 才放行（见 `docs/operations.md` §1.1）。

逐个工具的完整签名（入参/出参 JSON Schema、注解、错误码）见 `docs/mcp-tools.md`。

---

## 部署形态

两种，同一套代码：

| | 本地 stdio | 远程常驻（推荐） |
|---|---|---|
| 运输 | `stdio` | **Streamable HTTP** |
| 接入 | `claude mcp add memex -- uvx memex serve-mcp` | `claude mcp add --transport http memex https://.../mcp --header "Authorization: Bearer $TOKEN"` |
| 数据库 | `~/.memex/memex.db` 单文件 | `/var/lib/memex/`，索引与真源分库 |
| 认证 | 不需要 | **必须有**（静态 Bearer Token 起步） |

远程形态下最需要处理的一处：agent 在开发机上**读不到服务器的克隆目录**，
所以 `fetch_repo` 之后要用 `request_repo_bundle` 把仓库以 `git bundle` 单文件下发。
细节（运输层两个坑、认证两档、systemd、嵌入模型预置、何时才值得换 Go/Rust）
见 `docs/tech-design.md` 第四章。

---

## 最大风险

**agent 会烧掉自己的上下文窗口。** 把「读仓库、理解仓库」整个交给 agent，分析一个 5 万行仓库
可能填满它 80% 的窗口，导致它没余量做用户真正交代的任务。

对策就是上面的分工边界：机械活留在 server；并让 agent 能用自己的 `Read`/`Grep` 直读，
而不是把 MCP 当文件系统代理 —— 本地形态下 `fetch_repo` 直接返回克隆路径，
远程形态下改用 `request_repo_bundle` 把仓库下发到开发机（见上）。

这个风险只能用真 agent 实测（V1），见 `docs/solution-analysis.md` 第六、七节。
