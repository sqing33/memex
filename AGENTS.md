# AGENTS.md — memex 项目约定

> 本文件是给所有在本仓库工作的 agent / 协作者的**约定入口**。动手前先读这里，
> 再按「文档地图」找到对应章节。**文档是契约的真源，代码是实现。**

---

## 一、项目是什么

**memex** —— agent 驱动的跨仓库「实现借鉴」知识库。

填一个 GitHub 地址 → 由**调用 MCP 的 agent** 分析整仓实现 → 落成结构化知识
（功能 → 五原理轴 → 卡片）→ 后续写功能时按**语义**召回「别的仓库怎么实现过类似能力」。

三条不可动摇的设计支点：

1. **知识粒度定在「功能」级**（不是文件、不是函数）。
2. **对「描述」建向量，不对「代码」建向量** —— 这是跨语言借鉴成立的前提。
3. **证据链强制校验**：`code_mismatch` 必须为 **0**，不达标不落库（门禁，不是趋势指标）。

**第一原则：机械活留在 server，判断力交给 agent。**
抓取 / 克隆 / 证据包 / 切片 / 校验 / 落库 / 召回 → server；**只有分析、判断、生成 → agent**。

**唯一硬约束是 agent 的上下文窗口**（物理限制）。不因此降级设计，但要因此
避免让 server 代理文件读取（`get_evidence_pack` 只给目录树 + 入口点 + 符号清单）。

---

## 二、当前阶段（重要）

**方案与契约已定稿；V1 垂直切片已落地。**

- 代码走「**完整契约 + 垂直切片**」：设计保持完整形态，先实现 V1 —— 一条端到端闭环
  （`fetch → evidence → begin → validate → commit → search`），17 个 MCP 工具全部可用，
  用一个真实仓库 + 真实 agent 验证「agent 驱动到底可不可行」。`src/` 见 [`src/README.md`](src/README.md)。
- 28 项空缺已定 24 项，只剩 **G23–G26 四项全部待实测**（质量阈值 / 聚类阈值 /
  rerank 选型 / 远程会话清扫）——**现在拍数字必然是错的**，必须等真实分布与真模型。
- 因此：**改动契约前先改文档**。`docs/` 与代码不一致时，以 docs 为准并修代码。
- 校验顺序 V1–V5 是**验证切片**（不是降级阶梯）：V1 单仓闭环 → V2 3–5 跨语言仓库 →
  V3 跨语言召回 top-k → V4 聚类质量 → V5 规模化 / 去重 / 站点 / 批量。

---

## 三、文档地图（真源）

| 文件 | 管什么 |
|---|---|
| `docs/solution-analysis.md` | 需求拆解、生态调研、方案取舍与理由（为什么是 agent 驱动） |
| `docs/tech-design.md` | 技术设计：模块、**13 张表**、检索与聚类、部署形态、VibeCraft 对照 |
| `docs/report.schema.json` | 报告契约的机器可读 schema（`x-contract-id = memex/report/1`） |
| `docs/report-contract.md` | 报告契约散文版：4 个固定 H2、五原理轴、五类卡片定义 + 正/反例 |
| `docs/mcp-tools.md` | **17 个 MCP 工具**的入参/出参、全局约定、错误模型、注解表 |
| `docs/operations.md` | 运维：环境变量、忽略规则、子命令、启动检查、故障处置 |
| `docs/decisions.md` | 已定项 + 理由（A–E 五组 21 条 + G 组 24 条） |
| `docs/gaps.md` | 空缺清单（仅剩第 4 批 4 项待实测） |

**原则：`decisions.md` 记「已定 + 理由」，`gaps.md` 记「未定 + 建议」——两份互补。**

---

## 四、Git 规范

- 提交信息使用**中文**
- 格式：`<type>(<scope>): <描述>`
- type：feat / fix / refactor / docs / test / chore / style / perf
- **禁止** `git reset --hard`、`git push --force`、`git rebase`、`git branch -D`、`git checkout -- .`
- 撤销用 `git revert`
- **复杂改动默认自动提交**（任务收尾时主动 `git commit`，不要等用户催；不要默认 `git push`）
  - 触发：多文件/跨模块、主路径功能与修复、重构、DB/API/调度/Bot/Agent 行为变更、
    影响可复现性的配置
  - 粒度：按可独立回滚的逻辑块提交；长任务中途可阶段提交，避免全部堆在工作区
  - 信息：遵循上述中文 conventional 格式，写清「为什么」
- **简单改动可以不提交**：纯文案/样式微调、本地调试日志、单处小修（约 < 20 行、单文件）
  且用户未要求落库时，完成即可
- 用户明确说「不要提交 / 先别 commit」时以用户为准；用户要求提交时立即提交

**分支**：默认分支 `main`（`init.defaultBranch` 已设为 `main`）。

---

## 五、语言规则（契约强制，不是偏好）

| 内容 | 语言 |
|---|---|
| 文档、注释、commit message、与用户交流 | **中文** |
| 卡片 `mechanism_desc`、`intent` | **强制英文**（要建向量 / 做意图探针，必须语言中立） |
| 报告 `summary`、五原理轴正文、卡片 `title` | 用户母语（中文） |

理由：跨语言聚类要求「同一机制的描述跨语言长得像」，只需**建索引那一句**语言中立；
全英文牺牲母语体验，全中文则多语种模型分不开中英描述，`min_repos >= 2` 永不满足。

**报告里禁止任何占位符**：`N/A` / `NA` / `无` / `未知` / `待补充` / `TODO` 一律视为空，
校验器会判 `blank_principle` 拒绝。

---

## 六、代码约定

- **语言**：Python（3.11+）。远程常驻不需要换 Go/Rust —— 常驻让启动延迟只付一次，
  瓶颈在嵌入模型而非语言（详见 `tech-design.md` §4.8 的换语言判据）。
- **目录**（见 `docs/tech-design.md` §3.1）：
  ```
  src/memex/
    __init__.py · __main__.py · core.py · cli.py · constants.py · fsutil.py ·
    limits.py · embeddings.py
    cli.py           子命令：init / serve-mcp / serve-http / reindex / migrate /
                     export-site / stats / forget-repo / import-vibecraft
    store/           db.py(DDL+迁移) · analysis.py · index.py · search.py(RRF + kind 先验 + rerank)
    fetch/           抓取、镜像重写、仓库身份解析、bundle 下发与上传
    evidence/        证据包、切片、符号提取
    contract/        报告 schema + 校验器 + 契约下发
    analyze/         提交路径：render / rows / commit
    patterns/        跨仓聚类 + pattern chunk 合成
    session/         会话状态机 + session_stats 归档
    mcp/             17 个工具的 handler + stdio/HTTP 服务端 + help（能力声明只含 tools）
    batch/           服务端 LLM 批量分析（V5）
    import_/         VibeCraft 回填（目录名带下划线避开关键字）
    site/            export-site 的模板与渲染
  ```
- **零假成功**：抓不到、切不出、模型不可用，一律**显式报错**，不静默降级。
  嵌入模型不可用 → `serve-mcp`/`serve-http` **拒绝启动**，给三条出路
  （预置模型 / `MEMEX_EMBEDDER=http:<url>` / 显式 `hash:512`）；只有显式 hash 才降级，
  且响应带 `degraded:true`。
- **错误模型**：协议错误（JSON-RPC `-32602` 等）只用于「调用方用错工具」；
  一切业务失败走 `{ok:false, error:{code,...}}` 正常返回，`isError:false`。
  错误码是**封闭集 10 个**，不新增同义码。
- **幂等**：分析提交的幂等键是 `(repo_id, commit_sha, contract_version)`，
  **不含模型名**（agent 驱动下 server 拿不到模型名）。
- **可重建性分层**：索引层可重建（`reindex` 掉数据不心疼）；真源层不可重建，迁移必须稳。
  `session_stats` 是例外 —— 既非真源也非索引，**不可复得，回收会话时永不删**。
- **破坏性操作**默认禁用（`MEMEX_TOOLS` 不含 `destructive`），需 `confirm: true` 才执行，
  **不做撤销**。

---

## 七、改代码前自查

1. 这个改动**属于哪一层**？（真源 / 索引 / 派生）—— 索引层随便重建，真源层慎动。
2. 它**改了契约吗**？改了就先改 `docs/report.schema.json` + `report-contract.md`，
   并考虑**递增 `x-contract-version`**（契约版本决定报告可比性，是幂等键的一部分）。
3. 它**让 server 做了判断**吗？若做了，停下来 —— 判断属于 agent。
4. 它**会不会占掉 agent 的上下文**？会，就想别的办法。
5. 证据链还成立吗？`code_mismatch` 必须为 0。

---

## 八、命令速查

```bash
python3 -m memex init                     # 初始化本地库
python3 -m memex serve-mcp                # stdio MCP server（本地开发机首选）
python3 -m memex serve-http --port 8931   # Streamable HTTP（远程常驻）
python3 -m memex reindex [--embedder X]   # 重建索引（换嵌入模型后必做）
python3 -m memex migrate --dry-run        # 迁移预演
python3 -m memex stats                    # 库总览
python3 -m memex export-site              # 静态站点：已收录仓库 catalog
python3 -m memex import-vibecraft <path> --dry-run
```

环境变量与故障处置见 `docs/operations.md`。
