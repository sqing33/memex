# 未定项决策记录

> 记录方案里每一条「还没定下来」的选择，以及定下来的理由。
> 状态：`✔ 已定` / `◻ 待定`。阻塞级问题答完才动代码。
>
> **A–E 五组共 21 条（架构与取舍）+ G 组 29 条（G1–G26 及 G27/G28/G29，分四批定案）= 已定 50 条。**
> `gaps.md` 里**仅剩 1 项**：**G25 rerank 选型**。
> G23 质量项（定成硬门禁而非分数阈值）、G24 聚类阈值（0.60 + complete-linkage）、
> G26 会话清扫与并发形态（双路清扫 + n=200 全通）、G29 检索权重（vector = keyword，substr 兜底）
> 均已用 V1–V4 的真实数据实测定值——**都留了「为什么是这个数」的可复核记录**。

## 设计与取舍原则（用户已明确）

> **质量优先。实现难度、工期、运行成本都不构成降级设计的理由。**

推论（写下来是为了防止以后自己反悔）：

1. **不以「难做」为由选次优方案。** 若最优设计成本更高，就选最优设计。
2. **不以「省钱」为由牺牲质量。** 需要真模型的地方就上真模型；离线降级路径只用于
   「本机跑不起来时的冒烟测试」，**不是推荐形态**。
3. **唯一的硬约束是 agent 的上下文窗口**——它是物理限制、不是成本。
   所以「省上下文」的取舍依然成立（如 A2），但必须**与省钱无关**地论证。
4. 因此对已定项做了一次**成本偏见复审**，见文末 D 组。

## A 组 —— 阻塞级（不定则无法开工）

### ✔ A1. 卡片代码必须是连续行区间吗

**决定：允许多段（segment list），单段是常见情形。**

数据形状从「一个连续区间」升级为**有序段列表**：

```jsonc
{
  "code_spans": [                      // 有序；单段时就是普通情况
    { "path": "src/retry.ts", "start_line": 13, "end_line": 17, "symbol": "backoffFor" },
    { "path": "src/retry.ts", "start_line": 19, "end_line": 37, "symbol": "runWithRetry" }
  ]
}
```

理由：

- 需求里「借鉴一段实现」的真实形态就是**离散的**——一个关键函数 + 一个常量定义 + 一处错误分支，
  三者往往不连续。强制单区间会逼 agent 把这些拆成多张卡，反而破坏「一张卡 = 一个可借鉴单元」。
- **可验证性不受损**：每一段都独立按 `path:start-end` 从真实文件重切、独立校验存在性，
  比单区间只是从「一次切片」变成「N 次切片」。
- **对借鉴者的诚实性**：渲染时不连续处插入省略标记（如 `// … (elided) …`），
  让 agent 一眼看出这段代码是拼装的、不是文件原样，避免它误以为中间没有东西。
- 单段是 `code_spans.length == 1`，天然向后兼容，不必为常见情形加分支。

**被否掉的方案：**

- *强制单连续区间* —— agent 会把「逻辑上一个功能」硬拆成多卡，卡片粒度被工具约束扭曲。
- *「单区间 + 可选逐行列表」两套机制* —— 两套语义要做同一件事，校验器要接受两种输入形状，
  使用者还要判断该用哪种；本质上多段列表已经覆盖逐行列表能表达的一切。

### ✔ A2. 证据包给多少

**决定：`get_evidence_pack` 只给「目录树 + 入口点 + 符号清单」，不给代码切片。**

- agent 手上有本地克隆路径，能用自己的 `Read`/`Grep` 按需读，**不必让 MCP 当文件系统代理**。
- 少给 = 少占 agent 上下文，这是本方案最大风险（agent 上下文被分析任务吃光）的直接对策。
- **此取舍的理由是「上下文窗口」这一物理约束，不是省成本**（见设计与取舍原则第 3 条）：
  无论多便宜，把整仓塞进 agent 窗口都会挤掉它干正事的余地。
- 代价（agent 要多读几轮才摸清结构）可接受：读文件是它本来就擅长、且可并行的操作。

### ✔ A3. 会话状态机形态

**决定：有会话 + TTL 自动清理。**

- 需要会话才能：给 `next_step` 引导、统计每仓实际的 turn/token 消耗（V1 关键数据）。
- 脏会话（agent 开了不提交就跑）靠**超时回收**，不要求 agent 显式 close——
  它一定会忘，把收尾责任放在 server 侧才可靠。

### ✔ A4. 报告里出现 schema 未定义的字段

**决定：拒绝未知字段。**

- 契约严格 = 跨仓报告真正可比 = **聚类才有意义**（这是整个方案能成立的前提之一）。
- 代价：agent 偶尔被打回重写。可接受——`validate_report` 会提前告诉它，且错误信息给全量问题列表。
- 若日后确有「想存的额外信息」，正确的做法是**改契约加显式字段**，而不是允许任意透传。

### ✔ A5. `validate_report` 是否独立存在

**决定：保留为独立、只读、幂等的工具。**

- 唯一目的是「提交前自查、省往返」；它不乱写库，代价极低。
- 与 A4 配合：拒绝未知字段后，agent 更需要一次便宜的预检来一次改到位，否则
  `commit_report` 会反复失败、每次都要重拼整份报告。

---

## B 组 —— 重要

### ✔ B6. 五种 kind 的判定标准是否给锚定例子

**决定：契约里给每个 kind 的「定义 + 正例 + 反例」。**

- `kind` 是跨会话、跨 agent 都要一致的分类；只给定义必然漂移，而漂移会**直接污染向量聚类**
  （同一模式被分到不同 kind，簇就散了）。
- 锚定例写在 schema 契约与 `help(report-contract)` 里：正例让它知道该长什么样，**反例是关键**——
  边界混淆几乎都发生在「这个算 snippet 还是 skeleton」这类相邻类别上。
- 代价（契约文本变长）可接受：契约只在 `begin_analysis` 和 `help` 时读一次，非每轮开销。

### ✔ B7. 多 agent 并发分析同一仓库

**决定：允许多会话，靠 commit 幂等键 (repo_id, commit_sha, contract_version) 收口。**

> 键的构成后被 **G19** 修订：原为 `(repo_id, commit_sha, model)`，改以 `contract_version`
> 替代 `model`（agent 驱动下 server 拿不到模型名；真正影响可比性的是契约版本）。

- 会话之间互不干扰，本来无害的并行（同时开两个 agent 试分析）不该被挡。
- 该键已存在于 `analyses` 表；commit 时：**已存在且内容一致 → no-op 返回既有 analysis_id**；
  **已存在但内容不同 → 拒绝，要求显式 `force`**（避免两个 agent 静默互相覆盖）。
- 不用悲观加锁：锁会打断并行，而真正需要串行化的只是「同一仓同 commit 的最终写入」这一瞬间。

### ✔ B8. 仓库更新后如何增量分析

**决定：记录远端最新 commit + 标注 `stale` + 需要时全量重析。**

- **不做 git diff 增量**：feature 是**逻辑单元**、与文件并非一一对应，
  「改了某文件 → 该重析哪几个 feature」没有可靠映射，做 diff 增量必然漏析。
- 改为：`fetch_repo` 时对比远端最新 commit 与库内 commit；
  不一致则把该仓的 analyses 标为 `stale`，并在 `search_implementations` 结果里**显式提示**
  「这条来自 X 版本，已有更新」——让 agent 知道自己借的是旧版，而不是静默用旧数据。
- 重析是 agent 的自愿动作（`begin_analysis` 不再因同 commit 而跳过，因为 commit 已变）。

### ✔ B9. 质量指标的基准

**决定：`code_mismatch` 硬性为 0（不达标不落库）；其余指标先收集分布，暂不设阈。**

- `code_mismatch` 直接量化「分析者是否在编造代码」——这是本方案信任模型的地基，
  **不能是「趋势指标」，必须是门禁**。它的定义已经天然是「重切后与提案不符的卡片数」，
  为 0 才说明 agent 给的行号是真的。
- 其余指标（功能数、轴完整度、证据覆盖率、会话 turn/token）目前**没有任何实测依据**，
  先收集 V1/V2 的分布，再据实定阈；此时拍数字必然错。

### ✔ B10. `help(topic)` 的 topic 集与形态

**决定：固定 topic 集 + 结构化返回。**

topic 集：`analyze-flow`（建库全流程）/ `report-contract`（报告 schema + kind 锚定例）/
`card-kinds`（五类卡片判定）/ `search-usage`（怎么召回最有效）/ `patterns`（模式簇怎么读）。

- 与分三层策略一致：`instructions` 只放一句话触发；结构化流程放 `help`（**任何 MCP 客户端都能读**）；
  skill 只当可选的长篇方法论。
- **不做「一次返回全部」**：整篇文档很长，agent 读完就吃掉一块上下文，
  反而降低它以后还用 `help` 的意愿——按需取 topic 才符合「省上下文」这条主线。

## C 组 —— 可晚定

### ✔ C11. 静态导出站点的内容

**决定：站点只做「已收录仓库」的清单展示——仓储目录（catalog），不做卡片浏览器。**

页面内容：

| 字段 | 来源 |
|---|---|
| 仓库全名 / URL / 描述 | `repos` |
| 语言 / stars / license | `repos`（fetch 时抓取） |
| 已分析 commit / 分析时间 / 模型 | `analyses` |
| 功能数 / 卡片数 / 证据数 | `analyses` 的 counts |
| 质量指标（`code_mismatch` 等） | `analyses` 的 quality |
| 是否 `stale`（远端有新 commit） | 见 B8 |
| 知识库总览（仓数 / 卡数 / 模式数） | `stats` |

- 定位是**「我收过哪些仓、它们长什么样」的一眼总览**，不是知识库的完整前端。
- 报告与卡片的具体内容不在此页——需要时点进仓库，或直接用 MCP 召回（那才是主入口）。
- **不做客户端搜索框**：仓库数量天然有限（几十个量级），一页列完即可，无需检索。
- 仍是**静态 HTML**：不引入常驻服务、不改「单文件 stdio 进程」的形态。
- **远程形态下的暴露面**（E 组补充）：站点本身不带鉴权，若经反向代理对外等于公开整个目录页。
  要么与 MCP 放同一认证后，要么只在服务器本机/内网可见（见 `tech-design.md` §4.4）。

### ✔ C12. 「描述向量能否跨语言」的验证方法

**决定：V3 做对照实验量化。**

- 构造若干对「同能力、不同语言」的实现（Python ↔ TypeScript，正对应现有 fixture 的思路），
  量化**跨语言召回 top-k 命中率**，得出能不能跨语言的**结论**，而不是假设它成立。
- 理由：跨语言是这个方案**最核心的卖点**，也是**最没底气**的假设。
  若到接真模型之后才顺带发现不成立，整个卖点就塌了——必须早验证、且用数字说话。
- 已知的底：离线 hash embedder 无语义能力，**未接真语义模型前这项必然不达标**，属预期；
  该实验只在**接真模型后**才有判定意义。

### ✔ C13. 项目命名

**决定：`memex`。**

- 取自 Vannevar Bush 1945 年 *As We May Think* 的「个人知识库 + 关联检索」构想——
  其核心机制正是**从一份文档沿关联轨迹跳到另一份**，直接对应本项目的跨仓模式发现。
- 短、好拼，无重名冲突。目录、README、git 已一致。

---

## D 组 —— 成本偏见复审（因「时间/成本不设限」而升级的地方）

> 上一轮定项时，有三处我不自觉地按「省事/省钱」降级了设计。按新原则**全部上调**。

### ✔ D1. 向量检索的默认形态：真语义模型，不是 hash

**原表述**：「离线 hash embedding 打底（零依赖可跑），可选升级 sentence-transformers / HTTP embedding」。

**升级为**：**真语义模型是默认目标**（sentence-transformers 本地模型，或 HTTP embedding 服务）；
hash embedder **仅保留为「目标机器装不上任何依赖」时的冒烟测试路径**，不算推荐形态。

理由：**hash embedder 根本没有语义能力，方案的核心卖点「跨语言匹配」在它上面等于不存在。**
把它当「打底」，等于把方案最关键的假设默认关掉——这是成本偏见最严重的一处。

### ✔ D2. 检索加一道 rerank

**新增**：三通道 RRF 融合后，加一道**可选 rerank**（cross-encoder，或让调用方 agent 自己精排）。

理由：RRF 只按排名投票，能压掉单路侥幸命中，但**排不出「哪条真的更可借鉴」**——
而这恰恰是本方案最该做对的一步（难点 2：可借鉴性 ≠ 语义相似）。
质量优先下，值得多一道精排；它按配置启用，不装模型时退化回 RRF。

### ✔ D3. 校验从「行存在」加深到「切片可解析」

**原表述**：校验 = 路径真实存在 + 代码按行号重切。

**加深为**：**在行存在之上，再校验切片在语法上可解析**——
Python 用标准库 `ast`；其他语言若装了 tree-sitter 则用之，否则退回行存在校验。

理由：行号真实**不代表切出来的代码是一段合法的东西**。agent 可能给出「起点落在注释中间、
终点截断在字符串里」的区间——行都存在，代码却是残的。加一道语法校验能挡住这类
「形式上合规、实质不可用」的卡片，而它正是会误导借鉴者的一类。

### 复审结论

其余 10 项（A1–A5、B6–B10、C11–C13）**不受成本偏见影响**，无需修改：
它们要么本身就是在选最优（A1 多段、A4 拒绝未知字段、B9 `code_mismatch` 硬性为 0），
要么取舍依据是「上下文窗口」这一物理约束而非成本（A2、A3、A5、B10）。

---

## E 组 —— 远程常驻部署（用户提出：服务器常驻 + 开发机经 MCP 连接）

> 用户 (m00785) 明确：**不做本地，放服务器上常驻，开发机用 MCP 连远程。**
> 这改变了部署形态，连带引出四组新的选择。详见 `tech-design.md` 第四章。

### ✔ E14. 后端语言：**不换，仍是 Python**

**确认**：远程化带来的五项新要求（常驻服务 / 多客户端并发 / 认证 / 内存敏感 / 启动延迟）
**没有一项指向 Go 或 Rust**。

理由：常驻后启动延迟只付一次（Python 唯一的短板被消掉）；并发是 IO 密集
（等 clone、等 embedding），标准库线程模型足够；认证与语言无关；内存瓶颈在嵌入模型而非语言。
**被否**：为「单二进制分发」换 Go/Rust——那要重造分析/校验/ML 生态，是拿最强项换最弱项。

### ✔ E15. 运输层：Streamable HTTP，且**只实现 `application/json` 分支**

**确认**：本地 `stdio` 保留；远程用规范定义的 **Streamable HTTP**（单一 `/mcp` endpoint 同时支持 POST/GET）。

**只回 JSON、不做 SSE**：本项目工具全是请求-响应语义（`search` / `commit_report`），
没有 server→client 推送需求。将来要做「长时批量分析进度推送」再加 SSE 分支。

**硬性必做**（规范 MUST）：校验 `Origin` 头（防 DNS rebinding）。**易错点**：
勿把运输层的 `Mcp-Session-Id`（可 404 重建）与业务层 `begin_analysis` 会话（SQLite + TTL）混为一谈。

### ✔ E16. 认证：自用也必须有；起步用**静态 Bearer Token**

**确认**：服务暴露公网后必须认证——否则知识库与 clone 能力裸奔。
规范推荐 OAuth 2.1，但那是给多用户第三方服务设计的；单人/小团队用**静态 Bearer Token**
（反向代理或应用层校验）即可，Claude Code 一行 `--header "Authorization: Bearer $TOKEN"` 接入。
OAuth 2.1 留作「要对陌生用户开放时」的升级路径。

**附带必做**：`Origin` 白名单、HTTPS 强制（代理终结 TLS）、
**每 token 限流 + 仓库大小上限**（`fetch_repo` 是可 clone 任意 URL 的滥用面）、危险工具默认禁用。

### ✔ E17. 远程形态：**索引与真源分库**

**确认**：本地形态保持单文件 `memex.db`；**远程推荐拆成** `memex.db`（真源，千金难买、频繁备份）
+ `index.db`（chunk / 向量 / FTS，体积大、可 `reindex`、可不进备份）。同一套 DDL，做成开关。

理由：远程时数据库从「个人缓存」变成「服务器资产」，备份成为运维责任；
把可重建的大件隔离出去，能让备份只落真源层。

### ✔ E18. `fetch_repo` 的本地路径在远程不可达 → **`git bundle` 下发**

**确认**：这是远程化唯一真实的设计变化。方案最有效的对策是「返回本地克隆路径让 agent 直读」，
远程下 server 路径对 agent 不可见。**新增 `request_repo_bundle(repo_id)`**：
server 把克隆打成 `git bundle` 单文件（含 `sha256`），agent 拉回本地后用自己的 `Read`/`Grep`。

理由：保住「机械活留 server」原则；一次性传输而非 per-file 往返；git 原生格式免自定义协议；
sha256 顺带校验完整性。**降级路径**：退 `read_file_slice`，但提醒批量读、证据包给足。

> 因此工具面从 13 个 → **14 个**（后经 G8 再加两个破坏性工具、G22 再加一个 `upload_repo_bundle`，**现为 17 个**，见下）。

---

## G 组 —— 空缺定案（第 1 批：G1 / G2 / G3 / G13 / G19）

前置：`gaps.md` 逐条列了 28 项空缺。**第 1 批**是「改表结构 / 定接口」性质的五项——
越晚定越贵，其中 G1/G3 是 V1 的前置。本组定案落为三份新文档：
[`mcp-tools.md`](mcp-tools.md)（工具签名）、[`report.schema.json`](report.schema.json)（机器可读契约）、
[`report-contract.md`](report-contract.md)（散文契约 + 语言规则）。

### ✔ G1. 工具签名 —— 当时 14 个工具的完整入参/出参（后 G8 增至 16、G22 增至 17）

**决定：单独一份 [`mcp-tools.md`](mcp-tools.md)，逐工具给完整 JSON Schema；并统一全局约定。**

要点：

- **仓库一律用 `repo_id`**（`<host>__<owner>__<name>`，如 `github.com__sqing33__vibecraft`）；
  入参不用 `repo` / `repo_url`，展示名走返回字段 `repo_full_name`。
- **行号 1-based 闭区间** `[start_line, end_line]`，不用 `start`/`end` 缩写。
- **证据引用一律对象** `{path, start_line?, end_line?, symbol?, note?}`；紧凑串只作展示。
- **统一信封** `{"ok": bool, …}`；失败 `{"ok":false,"error":{code,message,details}}`。
- **列表信封** `{count, total?, items, next_cursor?}`，不返回裸数组；**游标分页，不用 offset**。
- **`detail` 三档 `brief|normal|full`**——recall 旧的 `compact/semi/full` 作废。
- **`next_step` 是结构化对象** `{action, args?, hint}`，无下一步则省略。
- 每工具**必须**声明 `annotations` 与 `outputSchema`，并同时返回 `structuredContent`；
  **不把 JSON 塞进 `content[].text`**（text 只放一句人话摘要）。

理由：工具面是 agent 唯一的操作界面，签名不精确 = agent 靠猜 = 浪费 turn 且易错。
把这些约定一次性定死，比以后逐个打补丁便宜得多。

### ✔ G2. MCP 元数据与错误模型

**决定：**

1. **注解**：每工具声明 `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`
   ——它是 G28「危险工具禁用清单」的**机械来源**（用 `MEMEX_TOOLS=read,write,network` 收口）。
2. **`outputSchema` + `structuredContent`**：机器可读，客户端可校验。
3. **错误模型（核心）**：
   - **协议错误**（JSON-RPC `error`，如 `-32602`）**只用于「调用方把工具用错了」**
     ——未知工具名、参数非法 JSON。
   - **一切业务失败走 `{ok:false, error:{code, message, details}}` 的正常返回**，
     `isError` 保持 `false`。
   - `error.code` 是**封闭集**：`invalid_argument` / `not_found` / `invalid_report` /
     `conflict` / `stale_repo` / `fetch_failed` / `unsupported` / `rate_limited` /
     `disabled` / `internal`。

理由：把「调用错误」和「业务失败」混在一层，会让 agent 分不清「我该改调用」还是
「我该改报告」——这是最常见的 agent 卡死原因。

### ✔ G3. 报告契约的 JSON Schema

**决定：单独一份 [`report.schema.json`](report.schema.json)**（JSON Schema draft 2020-12），
`x-contract-id = "memex/report/1"`、`x-contract-version = "1"`。

- 顶层 `additionalProperties:false`（**A4：拒绝未知字段**）。
- `features` `minItems:3`；五轴 `Principles` **全必填**，各 `minLength:30`（实际按 G13 的 units 计）。
- `Card` 的 `code_spans` 用 `allOf/if-then-else` 强制：
  `kind ∈ {snippet, skeleton}` 时**必须非空**，其余 kind **出现即报错**。
- 内嵌 `x-counting`（长度单位）、`x-card-kind-anchors`（B6 五类定义 + 正例 + 反例）、
  `x-notes`（语言、拒绝未知字段、证据、code 重切）。

理由：散文契约会漂移，机器可读契约不会。三处（schema / 散文 / `validate_report`）同源。

### ✔ G13. 报告与机制描述的语言

**决定：中英分层——建向量的文本用英文，给人读的正文用用户母语。**

| 字段 | 语言 |
|---|---|
| `mechanism_desc`（★建向量） | **必须英文** |
| `intent`（意图探针） | **必须英文** |
| `summary` / 五轴段落 / `title` | **用户母语** |
| `tags` | 小写英文标识 |

**并把 `min_principle_chars=40` 改为按「信息单元数」**：

```
units = (CJK / 仮名 / ハングル 字符数) + (拉丁字母 / 数字 连续串数)
```

理由：跨语言聚类要求「同一机制的描述跨语言长得像」——只让建索引的那一句保持
语言中立即可，不必让整篇报告一致。全英文牺牲母语阅读体验；全中文则多语种模型会把
中英描述分得比同语言的不同机制更远，`min_repos >= 2` **永远不满足**。
长度阈值同理：中文 40 字与英文 40 词信息量差近一倍，按字符算对两种语言不等价。

### ✔ G19. 分析者归因与幂等键

**决定：`analyses` 加 `analyst`（自由文本）与 `producer` 列；
幂等键从 `(repo_id, commit_sha, model)` 改为 `(repo_id, commit_sha, contract_version)`。**

- **真正决定「报告是否可比」的是契约版本，不是模型名**——同一个契约下不同模型产出的报告
  结构一致、可以互相覆盖；契约一变，旧报告就不可比，必须重析。
- `analyst`：如 `claude-code/opus-4.5` 或 `batch/st-embed`，**仅用于质量归因，不参与幂等**。
- `producer`：`agent | batch`（G21），质量指标**按此分组统计**——否则两条路的质量数据混在一起
  看不出谁好谁坏。
- agent 驱动架构下 server **拿不到** agent 的模型名，所以 `model` 本来也填不出来——这决定了
  它不能当幂等键。

理由：🔴 级。它同时是**幂等键**与**可观测性**的地基，且**改表结构**，越晚越贵。

---

## G 组 第 2 批 —— 安全护栏 / 生命周期 / 运行语义（G4–G9 / G11 / G15–G17 / G20 / G21 / G28）

> 13 条。都是「写代码时必须知道」的语义。具体配置项、数字与命令落为
> [`operations.md`](operations.md)；工具签名增补（`forget_analysis` / `forget_repo`）落为
> [`mcp-tools.md`](mcp-tools.md) T15/T16 与 §3 注解表。

### ✔ G4. 私有仓库与凭据

**决定：server 侧持凭据，per-host 映射；凭据绝不流经 MCP 参数。**

- 凭据来源：`MEMEX_GIT_TOKEN__<host_underscored>`（如 `MEMEX_GIT_TOKEN__github_com`），
  由 systemd `EnvironmentFile=` 注入；也支持 `~/.memex/credentials.json`（0600）。
- clone 用法：`git -c http.extraheader="Authorization: Bearer <token>"` —— **不落盘**、
  不写进 `.git/config`、不进 `remote.origin.url`。
- **agent 绝不能传 token**（会进对话历史与日志）——`fetch_repo` 入参**没有** token 字段。
- 失败语义：**显式区分** `401/403 无权限` 与 `404 不存在`，返回不同 `error.message`；
  不再像 recall 那样先回落 tarball 再报一个误导性的 404。
- 粒度：**全库级**（一个 host 一个 token）。每仓级授权留待有真实需求再谈。

理由：用户最想分析的常是自己的私有仓；而远程形态下 token 只能在 server 侧。

### ✔ G5. 仓库护栏的具体数字

**决定：把 `DEPTH_BUDGET` 迁进 memex 并重新定义语义为「证据包详略 + 校验严格度」；
超限一律截断 + 显式标记，不拒绝。**

| 档 `depth` | `max_files` | `max_readme` | `min_features` | 证据包符号上限 |
|---|---|---|---|---|
| `fast` | 400 | 3 000 字符 | 3 | 500 |
| `standard`（默认） | 2 000 | 6 000 字符 | 3 | 2 000 |
| `deep` | 4 000 | 12 000 字符 | 5 | 8 000 |

另有全局硬上限（与档位无关）：单文件 `<= 1 MiB`、`request_repo_bundle` 产物 `<= 512 MiB`、
仓库总字节 `<= 2 GiB`。

- `depth` **不再**控制「喂给模型多少」（没有 server 模型了），改为控制
  **证据包的详略**（树深度、符号上限、README 截断长度）**与校验的严格度**
  （`fast` 允许 `min_features=3`，`deep` 要求 5）。
- 超限行为：**截断 + 在 `stats.truncated` / `warnings[]` / 报告里显式标注**。
  绝不静默——否则 agent 以为分析是全量的。
- 单文件超 `1 MiB`：`read_file_slice` 返回 `invalid_argument` 并给文件大小。

### ✔ G6. 任意 git URL / 非 GitHub 宿主 / SSRF

**决定：白名单宿主 + 强制 https + 解析后地址段黑名单；`local:` 仅在本地形态可用。**

- **宿主白名单**：`github.com` / `gitlab.com` / `gitee.com`（可配置 `MEMEX_GIT_HOSTS`）。
  不在白名单 → `unsupported`。
- **协议**：只允许 `https://`。禁 `file://` / `git://` / `http://` / `ssh://`。
- **SSRF 防护**：DNS 解析后**拒绝**环回 / 私网 / 链路本地 / 云元数据段
  （`127.0.0.0/8`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`、`::1`、`fc00::/7`）。
  这挡的是「让 server 去 clone 内网地址」。
- **`local:` 前缀与裸目录路径只在 stdio 本地形态解析**；`serve-http` 下一律 `unsupported`
  （否则等于让远端 agent 读服务器任意目录）。
- **禁**自定义 `GIT_SSH_COMMAND` / `GIT_CONFIG_*` 环境注入。
- 非 GitHub 宿主的元数据降级为「git 能拿到的」（无 `stars`），并在 `RepoSummary` 里如实留空。

### ✔ G7. 限流与并发配额

**决定：三类闸分开；数值先给保守默认，V1 实测后调。**

| 闸 | 维度 | 默认 |
|---|---|---|
| 请求 | 每 token 的 HTTP QPS | 30 |
| **clone** | 全局并发 clone 数 | **3** |
| embed | 全局并发 embedding 批次数 | 1（按 CPU 可调） |

- clone 是最重的操作（磁盘 + 带宽 + 时间），**必须独立成闸**——否则一个 agent 连开 5 个 clone 就拖垮服务器。
- 超限返回 `rate_limited`，**带 `details.retry_after_seconds`**，让 agent 知道该等而不是重试。

### ✔ G8. 删除与淘汰语义

**决定：新增两个破坏性工具，硬删 + 级联 + 重聚类；必须显式 `confirm`。**

```
forget_analysis(analysis_id, confirm: true)   # 级联删 features/cards/evidence/chunks/向量
forget_repo(repo_id, confirm: true)           # 级联删其上全部 analysis + repos 行 + 克隆目录
```

- **硬删**，不做软删（本地/自用场景无审计需求；远程共享库若日后要留痕，再加 `archived` 标记）。
- 两者 `annotations.destructiveHint = true`，归入 `destructive` 类别，**默认禁用**（见 G28）。
- 删除后**自动重跑聚类**（`discover` 本身是幂等重算，recall 已验证）。
- **不做删除撤销**；但删除前必须能被 `get_report` / `recall_stats` 查到，让用户确认删的是哪个。
- **质量门禁未通过时的草稿不落库**——这是一贯行为，不是缺口；agent 自己持有草稿，
  失败时它拿 `problems[]` 改完再提交。**不引入草稿暂存表**。

> 工具面 14 → **16**（后 G22 再加 `upload_repo_bundle`，现 **17**）。

### ✔ G9. `schema_version` 迁移策略

**决定：显式迁移 + 拒绝静默降级；真源优先「导出→重建」，索引一律 `reindex`。**

- 启动时比对 `meta.schema_version` 与本版代码常量：
  - **更高**（旧代码开新库）→ **拒绝启动**（`unsupported`）；不允许降级读。
  - **更低** → 若存在 `migrate_<from>_<to>()` 则自动迁移，否则拒绝并提示跑 `memex migrate`。
- **真源层** `repos` / `analyses` / `cards` / `evidence` 迁移写显式函数；
  大改形状时走**「导出真源 → 建新库 → 重灌」**（真源就是报告 JSON + 卡片 + 证据，天然可重灌）。
- **索引层** 不迁移，直接 `reindex`。
- `cli` 加 `memex migrate [--to X] [--dry-run]`；`--dry-run` 打印将执行的动作。

### ✔ G11. 嵌入模型迁移与「混模型库」

**决定：`meta.embedder` 是唯一当前索引模型；写入与检索两端都断言，
把「不做多模型并存」从意图变成机制。**

- `chunk_vectors` **加 `embedder` 列**（每行记自己被哪个模型编码）+ 保留 `dim`。
- **写入断言**：写向量前核对 `embedder == meta.embedder`，不一致 → `conflict`，拒绝写入。
- **检索断言**：检索入口先确认 `chunk_vectors` 中不存在 `embedder <> meta.embedder` 的行；
  存在 → 拒绝检索，返回「跑 `memex reindex --embedder X`」的指引。
- 维度变化（512→768）时 `chunk_vectors.dim` 是**每行独立**（不是固定列），
  但**所有行必须与 `meta.dim` 一致**——跨维度共存同样被上面的断言拦下。
- `reindex` 一次性重算全部 chunk 向量并更新 `meta.embedder` / `meta.dim`（事务内原子切）。

理由：混模型库**不会报错**，只会静默毁掉召回质量——必须用断言挡住。

### ✔ G15. monorepo / 子目录范围

**决定：`fetch_repo` 支持 `subpath`；`get_evidence_pack` 按 workspace 分组并限深；
一个 monorepo 只算一个 repo。**

- `fetch_repo(repo_url, subpath?)`：`subpath` 存 `repos.subpath`；省略 = 全仓。
- **workspace 自动识别**（仅用于「分组展示」与「限深」，**不改分析范围**）：
  `package.json#workspaces` / `go.work` / `pyproject.toml [tool.uv.workspace]` /
  `Cargo.toml [workspace]` / `lerna.json`。识别结果进证据包的 `groups[]`。
- `get_evidence_pack` 在 monorepo 下：**按 workspace 边界分组**，每组**限深**，
  先给顶层骨架，agent 可按需对某组再取（`subpath` 递归）。
- **一个 monorepo = 一个 repo**：否则 `min_repos >= 2` 会被同一个仓的多个包满足，**污染模式**。
  聚类去重时按 `repo_id` 计票，不按目录计票。

### ✔ G16. 边界输入

**决定：固定忽略规则；能分析就分析，但把 `warnings[]` 明确返回，不静默失败。**

| 输入 | 行为 |
|---|---|
| 空仓 / 只有 README / 无代码 | `fetch_repo` 成功但 `get_evidence_pack` 给 `warnings`；`begin_analysis` **允许**，由 agent 判断是否值得（契约仍要求 ≥3 features，多半会失败 → 让它自己放弃） |
| 非代码仓（数据/文档站） | 同上；`warnings` 说明「未识别到代码文件」 |
| 单文件 > `1 MiB` | 证据包跳过并在 `warnings` 列出；`read_file_slice` 对该文件返回 `invalid_argument` |
| 二进制 / 生成文件 | **固定黑名单**：`.git/`、`node_modules/`、`vendor/`、`dist/`、`build/`、`target/`、`__pycache__/`、`*.min.js`、`*.lock`、二进制扩展名（`.png/.jpg/.pdf/.zip/.so/.dll/.wasm/.pyc`…） |
| 巨型仓（kernel 级） | 按 `depth` 截断 + `truncated` 标记（G5） |
| 无 license / 非标准 license | `repos.license` 留空；召回结果里**显式**给出 `license: null`，让 agent 自己判断可用性 |
| `.gitignore` | **尊重但不信**：忽略规则 = 黑名单 ∪ `.gitignore`；但 agent 仍可用 `read_file_slice` 显式取被忽略的文件 |

### ✔ G17. 失败与重试语义

**决定：重试策略显式化；重活「先临时后原子切」；错误对象带 `retryable`。**

| 操作 | 超时 | 重试 |
|---|---|---|
| `git clone` | 300 s | **3 次**，指数退避（1s / 2s / 4s），仅对 5xx / 网络错误；404/403 **不重试** |
| tarball 兜底 | 120 s | 1 次 |
| embedding 单批 | 60 s | 2 次 |
| `reindex` | 无上限（长任务） | 失败可整批重跑 |
| `request_repo_bundle` | 180 s | 1 次 |

- `error.details.retryable: bool` —— agent 据此决定「等一下再来」还是「改输入」。
- **原子性**：`reindex` 写临时表后原子切换；`fetch_repo` 用「clone 到临时目录 → 成功后
  rename」避免半成品目录；失败的 `fetch_repo` 不写 `repos` 行，**重试不会产生重复行**。
- `fetch_repo` 幂等：同 `(repo_url, ref)` 返回同一 `repo_id`（不新增行）。

### ✔ G20. 会话期间仓库被更新（切片竞态）

**决定：会话绑定 commit；提交前核对；不一致报 `stale_repo` 且保留会话。**

- `begin_analysis` 记录并返回 `commit_sha`（会话绑定的版本）。
- `commit_report` 前核对克隆目录仍是该 commit：
  - 一致 → 正常校验落库。
  - 不一致 → `stale_repo`，`details` 给 `{session_commit, current_commit}`，
    **会话保留**，agent 可选：重开会话（分析新版本）/ 用当前版本重切再看差异。
- **绝不让 agent 看到一堆假的 `code_mismatch`**——那会把「仓库变了」误报成「你在编造代码」。

### ✔ G21. batch 模式与 agent 模式共库的语义

**决定：`producer ∈ {agent, batch}`；质量指标按 producer 分组；batch 排在 V5。**

- 两条路**共用同一个库、同一套校验**（同一 `validate_report`、同一 `code_mismatch` 门禁）。
- `search_implementations` 结果里带 `producer`，让用户知道「这条是机器批量的」。
- `recall_stats.quality.by_producer` **分组统计**——否则两条路的质量混在一起，
  谁好谁坏看不出来。
- `analyst` 对 batch 记 `batch/<模型名>`（如 `batch/gpt-4o-mini`）。
- **实施顺序：V1–V4 只用 agent 驱动；batch 排 V5**（与「规模」目标同批，避免过早分心）。

### ✔ G28. 危险工具禁用清单

**决定：按 G2 的 annotation 机械导出四类，`destructive` 默认关；用 `MEMEX_TOOLS` 收口。**

| 类别 | 判定 | 工具 | 默认 |
|---|---|---|---|
| `read` | `readOnlyHint:true` | `get_evidence_pack` · `read_file_slice` · `validate_report` · `search_implementations` · `get_card` · `list_patterns` · `get_report` · `list_repos` · `recall_stats` · `help` | **开** |
| `write` | 写本地状态，非破坏 | `begin_analysis` · `commit_report` | **开** |
| `network` | `openWorldHint:true` | `fetch_repo` · `request_repo_bundle` | **开**（本地自用）／**建议远程按需关** |
| `destructive` | `destructiveHint:true` | `forget_analysis` · `forget_repo` | **关** |

- 开关：`MEMEX_TOOLS=read,write` 即只放行前两类；被禁工具调用返回 `disabled`，
  `details.enabled` 给当前放行集。
- 远程形态建议默认 `read,write,network` 但在文档里明示：
  **不想让 server 主动出网就写成 `read,write`**。

---

## G 组 第 3 批 —— 身份 / 回填 / 内容与能力面（G10 / G12 / G14 / G18 / G22 / G27）

### ✔ G10. 仓库身份（改名 / 转移 / fork）

**决定：`repo_id` 创建后**不可变**，是稳定的对外句柄；真身份锚是逐宿主的 `identity_key`（GitHub 用数字 `id`），`full_name` 只作展示并在每次 fetch 时校正；改名不算新仓；fork 记 `fork_of` 但**不**去重，聚类投票按「同源组」合并。**

- 身份三级（写入 `repos`）：
  - `identity_key` **UQ** —— 逐宿主最稳的锚。GitHub 取 API `GET /repos/{owner}/{name}` 的 `id`
    （改名/转移不变），形如 `github.com#12345678`；无 API 时退化为 `github.com#<owner>/<name>` 小写化
    并记 `warnings`，此时**不**做自动合并（保守）。
  - `full_name` —— 展示名，每次 fetch 用最新值覆盖；
  - `aliases_json` —— 历史 `full_name` 列表（改名轨迹，供人查）。
- **改名/转移的合并**：`fetch_repo` 首次见到某名时先按名算候选 `repo_id`，再查 `identity_key`；
  命中既有行 → **沿用既有 `repo_id`**（目录名、chunks、cards 全部不动），只更新 `full_name`/`aliases_json`，
  返回 `merged_into: <既有 repo_id>`。**历史分析一律保留**（不删、不重挂）。
- **fork**：API `fork:true` + `parent.full_name` → 写 `repos.fork_of`（上游全名）与 `repos.is_fork`。
  fork 与上游是**不同 `repo_id`**（各自可独立分析、独立召回）；但聚类时用 **`source_group`** 判定同源：
  ```
  source_group = COALESCE(repos.fork_of, repos.identity_key)
  ```
  `patterns.repo_count` 与 `min_repos=2` 一律**按 distinct `source_group` 计数**，不按 `repo_id`。
- **为什么不在 `full_name` 上做唯一键**：GitHub 改名后 `clone` 301 重定向到新仓，`full_name` 会变，
  唯一键会漏出重复行；而 `repo_id` 从名字派生，若随名字改则要级联全库（chunks/cards/目录名）——
  所以把「稳定句柄」与「最新名字」拆开。
- **可观测**：`list_repos` 对 `is_stale`/`is_fork`/`merged_into` 各给标记；`fetch_repo` 的 `warnings`
  在「无 API、只按名判定」时明确写「身份未校验」。

理由核心：改名这条路正是最早想解决的「同一个仓的两次分析凑成 `min_repos=2` 从而污染 pattern」
——`identity_key` + `source_group` 把改名与 fork 两种凑票方式一并堵掉。

---

### ✔ G12. 从 VibeCraft 回填

**决定：`memex import-vibecraft <path>` 做**最佳努力**结构搬运，强制过同一 `validate_report` 与「按真实文件重切」；过不了的卡片**丢弃并计入报告**，不静默降级；产物标 `producer=batch` / `analyst=vibecraft-import`。失败是常态，故 `--dry-run` 必做。**

**输入源（已实地核对 VibeCraft 源码）**：

| 内容 | 位置 |
|---|---|
| 主业务库 | `<repoLibraryDir>/*.db`：`repo_analysis_results`（原名 `repo_analysis_runs`，migrate 时改名/重建）、`repo_knowledge_cards`、`repo_knowledge_evidence`、`repo_sources`、`repo_snapshots` |
| 报告文本 | `<storage_path>/report.md`，其中 `storage_path = <repoLibraryDir>/repositories/<repo_key>/analyses/<analysis_id>` |
| 检索索引 | `<repoDir>/search/search.db`（`kb_chunks`/`kb_chunks_fts`/`kb_chunk_vec`）——**纯派生，不搬**，回填后由 memex 自己 `reindex` |

**映射表（VibeCraft → memex）**：

| VibeCraft | memex |
|---|---|
| `card_type='feature_pattern'` | `kind='snippet'`（能从真实文件重切出代码段）／否则 `kind='mechanism'` |
| `card_type='integration_note'` | `kind='decision'`（「为集成而做的取舍」）／语义是「踩坑」则 `kind='gotcha'` |
| `card_type='project_characteristic'` | **不进** `cards`，进报告级 `characteristics[]`（它本是仓级特征，不是功能卡片） |
| `evidence(path, line, snippet)` | `evidence(path, start_line, end_line, excerpt)` —— **必须重切**：拿 `path`+`line` 去真实文件取区间；切不出/对不上 → 该卡片判 `code_mismatch` → **丢弃** |
| 报告 4 个 H2 | 一一对应 memex 的 4 个固定 H2（同名，直接搬） |
| 5 个原理轴 | 同名直接搬（`PRINCIPLE_KEYS` 与 memex 一致） |
| （无）`contract_version` | 回填按 memex 当前 `x-contract-version` 写入 |
| `mechanism` 字段 | `mechanism_desc` 的**首选来源**（见下） |

**两处硬约束（决定成败）**：

1. **证据链必须过。** VibeCraft 的证据只有 `path + line`，**没有区间、没有语法校验、没有
   `code_mismatch` 概念**。回填必须**从真实文件重切**；因此前置是**该仓的文件在本地**
   （先 `fetch_repo`，或用 `MEMEX_ALLOW_LOCAL_PATHS` 指到 VibeCraft 留下的旧克隆）。
2. **`mechanism_desc` 必须语言中立（英文）。** 这是 G13/D1 定的跨语言前提。VibeCraft 的 `mechanism`
   若已是英文/语言中立 → 直接取用；若是中文 → 该卡片**仍入库，但 `reindex_state='pending'` 并排除出
   检索/聚类集**（`recall_stats` 单列 `pending_mechanism` 计数），等 agent 补写英文描述后再 `reindex`。
   **不把中文塞进 `mechanism_desc` 假装成功**（那会让「跨语言」卖点静默失效）。

**输出报告**：
```json
{"ok": true, "dry_run": false, "source": "<path>",
 "repos_seen": 0, "analyses_seen": 0,
 "cards_seen": 0, "cards_imported": 0, "cards_dropped": 0,
 "drop_reasons": {"code_mismatch": 0, "evidence_missing": 0, "no_code_span": 0, "schema_invalid": 0},
 "evidence_hit_rate": 0.0, "pending_mechanism": 0,
 "next_step": {"action": "reindex"}}
```
成功判据：`cards_imported > 0` 且全部通过 `validate_report`（**不要求全量**——旧卡片体系与 memex
契约的落差本来就大，能救几张是几张）。

**不做**：不搬 VibeCraft 的向量（文本与模型都不同，搬过来是错的）；不搬检索索引；
**不写** VibeCraft 的库（只读）。

---

### ✔ G14. `chunks.kind` 里的 `pattern`

**决定：建。每个 pattern 恰好一个 summary chunk（`kind='pattern'`，key = `pat:<pattern_key>`）；文本 = 成员卡片 `mechanism_desc` 合成；`repo_id` 允许为 NULL；重聚类时按 key 整批覆盖；融合后施加 kind 先验，pattern 的基础权重**低于**具体卡片。**

- `chunks` 两处放宽：`ref_id` 存 `pattern_key`；`repo_id` **允许 NULL**（pattern 跨仓，无单一归属）。
  `search_implementations` 对这类命中返回 `repo_id: null` + `repos: [<全名>…]`。
- **文本合成规则**（可复现、可测）：取该 pattern 成员的 `mechanism_desc`，去重后按
  「成员数 desc, 卡 id asc」排序，`; ` 连接，截断到 `MEMEX_PATTERN_CHUNK_MAX_UNITS`
  （默认 60 信息单元，按 G13 的「信息单元」口径）。
- **权重**：RRF 本身**不加权**（保持三通道等权、无量纲相加）；kind 先验在**融合之后**施于融合分：
  ```
  final = fused_score * kind_prior[kind]
  kind_prior = { card: 1.0, feature: 0.9, pattern: 0.6, report_section: 0.4 }   ← 初值，V4 调
  ```
  这与 VibeCraft 的加权求和不同：那里是把 keyword / vector 两个**不同量纲**的分**先**相加；
  这里的融合是纯 RRF，权重只作用在**同一量纲的最终分**上。
- **派生 + 易变**：pattern chunk 不参与 `min_repos` 计数（它是产物不是证据）；
  `pattern_intents`（V4 非对称分解的意图探针）是**另一张表**，与 pattern chunk 分开；
  重聚类 = 按 `pat:<key>` upsert（消失的 pattern 连同 chunk 与向量一起删）。
- **为什么建**：不建的话「有没有人做过 X 这类模式」只能命中成员卡片，看不到「这是一个被 3 个仓
  独立收敛出来的模式」——而这恰是本项目最想交付的知识形态。

---

### ✔ G18. 会话 `abandoned` 与 TTL

**决定：`abandoned` 一律**超时自动**判定（不新增工具）；TTL 默认 `MEMEX_SESSION_TTL_SECONDS=7200`；回收分「归档统计 → 留痕 → 过期物理删」三步；`begin_analysis` 对同一 `(repo_id, commit_sha, contract_version)` 的未完会话**返回既有会话**而非新建。**

- **判定**：任何超 TTL（默认 2h）仍处 `begun|evidence_taken|drafting|validated` 的会话，在下一次
  任意工具调用（本地 stdio）或远程低频后台线程（`serve-http`，每 60s 一趟）时置 `state='abandoned'`
  并写 `abandoned_at`。
- **三步回收**（顺序不可换，先落统计再回收）：
  1. **归档统计**：把会话 meta 里的 turn 数、工具调用次数、token 估算、耗时写入 `session_stats`
     （新增表）。**这是 V1 判「agent 驱动可不可行」的唯一数据来源，绝不能被回收动作吞掉。**
  2. **留痕**：`state='abandoned'` + `abandoned_at`，**行保留**（可查，让 `begin_analysis` 知道
     「这里有过一次未完成」）。
  3. **物理删除**：超过保留期 `MEMEX_SESSION_RETENTION_SECONDS`（默认 7 天）后删行
     （`session_stats` **不删**）。
- 新表见 `tech-design.md` §2.2。
- **`begin_analysis` 幂等**：同 `(repo_id, commit_sha, contract_version)` 已有未提交会话 →
  返回既有 `session_id` + `resumed: true`，**不新建**（避免 agent 重试时堆会话）；
  已有**已提交**分析 → 返回既有 `analysis_id`（B7 幂等键）+ `already_analyzed: true`。
- **值待实测 🔵**：TTL=7200s 是初值；V1 实测「一次真实分析（含 agent 往返）的墙钟耗时」，
  若 P95 超过 TTL 一半就上调。此值同时决定远程后台清扫线程是否有必要（`tech-design.md`
  §2.6 的「若实测积压」）。
- **不新增「显式放弃」工具**：agent 很少会正确调用一个「我放弃了」的工具；超时自动判定即可，
  且工具面已经够宽。

---

### ✔ G22. 网络受限环境（镜像 / 离线 / 嵌入不可用）

**决定：抓取侧三管齐下 —— ① `MEMEX_GIT_MIRROR` 前缀重写；② 新增 MCP 工具 `upload_repo_bundle`（反向投喂，工具面 **16 → 17**，同时解 G4 变体）；③ 嵌入模型不可用时**明确报错 + 指引**，绝不静默退 hash。**

**① 镜像**
- `MEMEX_GIT_MIRROR=https://git-mirror.corp/` → 抓 `github.com/<owner>/<name>` 时重写为
  `<mirror><owner>/<name>`（尾斜杠归一）。
- **可用性探测**：`git ls-remote <mirror_url> HEAD` 成功才用镜像，否则记 `warnings` 并**回落直连**
  （镜像不可用不该让整次抓取失败）。
- **凭据按原始 host 取**：`MEMEX_GIT_TOKEN__github_com`，不按镜像 host——镜像只是运输层。
- 镜像只覆盖 clone/tarball 路径；GitHub API（元数据/身份）仍直连，失败则软降级（G10 已说明
  此时不自动合并）。

**② 反向投喂 `upload_repo_bundle`（新增 T17）**
- 场景：服务器无外网／服务器无私有仓凭据／仓在内网 GitLab。
- 流程：开发机 `git bundle create repo.bundle --all` → 经该工具上传 → server `git bundle verify`
  → `git clone <bundle> <repos_dir>/<repo_id>` → 登记 repo（`source='upload'`）。
- 上限 `MEMEX_MAX_BUNDLE_BYTES`（默认 512MB，见 `operations.md` §1.3）；超限 `invalid_argument`。
- annotations：`{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`；
  类别归 **`write`**（不出网）；`MEMEX_TOOLS` 不含 `write` 时不可用。
- **副产品**：把「开发机有凭据、服务器没有」这条 G4 变体一并解决——私有仓先在开发机 clone 再投喂，
  服务器全程不接触 token。这个用法要写进 `help('analyze-flow')`。
- **T4 的对偶**：T4 `request_repo_bundle` 是 server → agent（下发），T17 是 agent → server（上传）。

**③ 嵌入模型不可用**
- 启动检查（`operations.md` §4）第 3 步：模型加载失败 → **`serve-mcp` / `serve-http` 拒启**，
  stderr 明确报「嵌入模型不可用」+ 三条出路（预置模型 / `MEMEX_EMBEDDER=http:<url>` /
  显式 `MEMEX_EMBEDDER=hash:512`）。
- **只有显式写 `hash:512` 才降级**，且 `recall_stats` 与启动横幅必须带
  `degraded: true, embedder: "hash"`。**默认路径绝不静默退 hash**（D1 已定：hash 无语义 =
  跨语言卖点凭空消失）。

---

### ✔ G27. MCP `resources` / `prompts`

**决定：明确**不实现** `resources` 与 `prompts`，只实现 `tools`。理由入档，不留空。（此前零命中。）**

- **`resources` 不实现**：memex 的一切内容都必须**按意图检索**（三通道 RRF + rerank + kind 先验）
  才能命中；`resources` 的模型是「列清单 → 客户端引用」，会把整份报告当资源读进上下文——
  **正面踩中本项目唯一的硬约束（上下文窗口）**。列表本身也是负资产（越全越诱人读）。
- **`prompts` 不实现**：把「做一次分析」固化成客户端侧模板，等于把流程**复制**到客户端；
  而流程要随 `contract_version` 演进，**唯一真源必须是 server 返回的 `next_step`**
  （`mcp-tools.md` §1.9）。两个真源必然漂移。
- **能力声明**：`initialize` 的 `capabilities` 只含 `tools`（`{listChanged:false}`）；
  客户端发 `resources/list`、`resources/read`、`prompts/list`、`prompts/get` → 一律 JSON-RPC
  `-32601`（method not found），与「未实现」语义一致。
- **万一将来要加**：加 `prompts` 之前先问「`help(topic)` 为什么不够」；加 `resources` 之前先问
  「为什么不能用一次 `search_implementations` 代替」。两个问题答不上来就不加。

---

### ✔ G24. 聚类相似度阈值与簇判据

**定案：`DEFAULT_SIM_THRESHOLD = 0.60`，簇判据用贪心 complete-linkage。**

**为什么不是 0.75。** V1 用真模型（`paraphrase-multilingual-MiniLM-L12-v2`）在
Doraemon + PTNexus 两仓 27 张可复用卡上实测，两组独立测量给出同一个分离带：

| 测量方式 | 真同机制下界 | 噪声上界 |
| --- | --- | --- |
| 语料内配对（跨仓 top16 边逐条人工判定） | 0.611 | 0.584 |
| 标注探针（5 正 / 4 负，换措辞换语言） | 0.611 | 0.461 |

跨仓相似度整体分布 `mean=0.280 sd=0.115 p97=0.503 max=0.584`；同仓 `mean=0.305 max=0.775`。
逐阈值表现：`0.75` 命中正样本 1/5；`0.65` 命中 3/5；**`0.60` 命中 5/5 且负样本 0/4 误纳**；
`0.45` 起开始误纳。故取 0.60——落在分离带内、偏召回侧，代价是最多引入边界噪声而非漏掉真模式。

**为什么必须换掉并查集。** 原实现取无向图连通分量，成员判据是「与簇内任一成员相似」。
这等价于传递闭包：`A~B=0.866`、`B~C=0.866` 达标时，即使 `cos(A,C)=0.5`，
C 也会被拖进 A 的簇。27 张卡实测簇规模对比：

| 阈值 | 并查集 | complete-linkage |
| --- | --- | --- |
| 0.60 | 3, 3, 2, 2, 2 | 2, 2, 2, 2, 2 |
| 0.55 | **5**, 4, 3, 2 | 3, 3, 2, 2, 2 |
| 0.50 | **11**, 6, 2 | 3, 3, 3, 2, 2, 2 |
| 0.45 | **22**, 2, 1 | 3, 3, 2, 2, 2, 2, 2, 2 |
| 0.40 | **25**, 1, 1 | 4, 3, 3, 3, 3, 2, 2, 2 |

并查集在 0.45 以下把 22/27 张不相关的卡吸进一个「模式」——那不是聚类，是把噪声
固化成知识，直接违反「零假成功」契约。回归测试
`test_cluster_cards_no_transitive_absorption` 锁定该行为（60° 几何，A~B=B~C=0.866、
A~C=0.5、阈值 0.8，旧实现给 `[3]`，新实现给 `[1, 2]`）。

**代价。** complete-linkage 是 O(n²·k)，比连通分量贵；且贪心依赖输入顺序，
所以 `recluster` 必须按 `card_id` 排序传入以保确定性（`test_cluster_cards_is_deterministic` 锁定）。
V1 语料规模（27 卡）下开销可忽略。

**V1 语料的诚实结论（已被 V2 推翻，见下）。** 阈值定案后重跑两仓 `recluster` 仍得
`patterns=0`，`clusters_seen=22`。当时判为正确输出：Doraemon 与 PTNexus 技术栈无交集
（Node.js+Express+MySQL vs Go+Vue+多方言 SQL），语料里确实没有可成对的跨仓机制。

**V2 五语种实测：0.60 确实出真模式，但精度不够（G24 第一次修正）。**
语料扩到 5 个仓、78 张可复用卡（JS/Vue+Express `Doraemon`、Go/Vue `PTNexus`、
Python `urllib3`、Java+Kotlin `okhttp`、Rust `axum`），`recluster` 得
`patterns=5, clusters_seen=54, cards_considered=78`。5 个簇**全部跨语言**且都落在真实机制上：

| pattern_key | 标题 | 配对 |
| --- | --- | --- |
| `cluster-d082ca95d8` | 重试与后继请求收敛到同一循环，恢复判定集中在一处 | urllib3 + okhttp（3 卡） |
| `cluster-06d962ae50` | 服务端指示优先于本地退避，且指示值本身也被钳制 | urllib3 + okhttp |
| `cluster-7e5026593a` | 退避时长由「连续错误段」而非「累计次数」决定 | urllib3 + okhttp |
| `cluster-c8049ad61d` | 逐层剥包装再对成因下转 | PTNexus + axum |
| `cluster-1f29bc3d44` | 远端预检查与本地统计的双层回退门 | PTNexus + urllib3 |

**但 5 簇中 4 簇的成员对并非同一机制。** 逐簇算成员两两余弦（`reports/diag_sim.py`），
全部落在 0.770–0.785 的窄带里，远高于 0.60 阈值；可人工核对成员语义，只有 1 簇
（`cluster-d082ca95d8`）三张卡确实在讲同一件事。其余如「服务端指示优先于本地退避」
被并上了 okhttp 的「读阶段已发出的请求不重试」——两者都在讲重试，但**不是同一个机制**。

**根因是 `pattern_members.score` 曾被写死为 `1.0`。** 该列是聚类质量唯一的对外信号
（`list_patterns` / `get_report` 都靠它判断簇质量），写死 1.0 等于抹掉全部信息：
读者无法区分「三卡互相 0.78」与「三卡互相 0.99」。已修正为**该成员与簇内所有其他成员的
最小余弦**——正是 complete-linkage 决定它能否留在簇里的那个最紧的一环，可直接与阈值对照。
回归测试 `test_recluster_records_real_member_similarity` 锁定（seed 两段**不同**机制文本，
断言 score 与真实余弦一致且不等于 1.0）。修正后实测 5 簇的成员 score 全部落在
0.770–0.785 区间——**分数暴露了「都过阈值但语义不同」这一事实**，这正是它该干的事。

**因此 0.60 维持不变，但 G24 的结论要修正为「召回可用、精度不足」。** 现状是
`pattern` 的价值在**候选发现**（告诉你这两家都在处理重试，值得去看），不在**直接当结论用**。
下一步（V4）要么给 `list_patterns` 加 score 门槛、要么引入 rerank（G25）提升同义判别力；
在此之前 `list_patterns` 的簇标题必须被当作「线索」而非「已证实的机制等价」。

**残余风险。** 阈值强依赖嵌入模型，换模型必须重测（`decision` 里「recall 旧代码的 0.62 已作废」
同理）。`DEFAULT_SIM_THRESHOLD` 是模块常量，未开放环境变量：V1 只有一个模型，
过度可配会让人随手调低造出假知识；V4 接多模型时再按模型分档。

**V2 追加的残余风险：0.77–0.785 的成员相似度带过窄，说明该 embedding 在
「同一大主题下的不同机制」上判别力不足。** 阈值 0.60 只能挡住跨主题噪声（0.50 上下），
挡不住同主题内的近义噪声（0.78 上下）。这不是调阈值能解决的——把阈值提到 0.78 以上会丢掉
`cluster-c8049ad61d` 这类真跨语言配对（`PTNexus`「空标题用哨兵错误而非空结果」
与 `axum`「逐层剥包装再对成因下转」实测 0.770，主题不同但机制同构）。
只有换更强的 rerank（G25）或引入机制维度的额外特征（如下游询问「并发时怎么处理」）才能分开。

## V4 实测：精度 25.0%，且不存在「零误报」阈值（G24 第二次修正，本轮定案）

V4 是专门为「聚类质量」设的验证切片。做法：先把 `recluster` 的真正决策面圈出来——
`min_repos>=2` 只约束跨源仓，落到具体就是**跨仓卡片对且余弦 >= 0.60**，共 **20 对**；
再逐对人工判定（口径：判「同机制」当且仅当**换仓库的读者能照着这条描述改自己的实现**，
而不只是共享大主题——「都在讲重试」不算），标注落 `reports/v4_labels.json`。
**20 对里只有 5 对判同机制，精度 25.0%**（`reports/v4_label.py`）。

### 先纠一个上文的错

上文说 5 簇成员余弦全落在 0.770–0.785，那是 **bug4 修复前**的 `pattern_members.score`
——那时该列写死 1.0，那批数字来自另一个诊断脚本对成员两两余弦的直接计算，
与修复后的实测量口径不同。修复后重跑 `recluster`，11 个成员 score 落在
**0.6033–0.6735**（mean 0.6361），与上文的 0.77 无关。以本段数字为准，
上文 0.770–0.785 那句按此作废。

### 阈值扫描证明不存在分离阈值

`reports/v4_sweep.py` 实测：

| 阈值 | 选中 | TP | FP | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| 0.650 | 6 | 0 | 6 | 0.0% | 0.0% | 0.000 |
| 0.630 | 10 | 1 | 9 | 10.0% | 20.0% | 0.133 |
| 0.620 | 13 | 2 | 11 | 15.4% | 40.0% | 0.222 |
| 0.610 | 18 | 4 | 14 | 22.2% | 80.0% | 0.348 |
| 0.600 | 20 | 5 | 15 | **25.0%** | 100% | **0.400** |

最高 TP 余弦 0.6367，**不低于它的 FP 有 8 个**；TP 降序 0.6367 / 0.6229 / 0.6157 /
0.6132 / 0.6033，FP 降序 0.6738 / 0.6735 / 0.6647 / 0.6547 / 0.6539 / 0.6528 / 0.6483 /
0.6375…。**同机制对与同主题对在余弦空间里完全交错**——调阈值只是在「多漏真模式」和
「少收误报」之间滑动，不存在一个既保召回又零误报的位置。

误报结构：**15 个 FP 里同主题 6 个、跨主题 9 个**（`reports/v4_why.py`）。
跨主题 FP 是主要来源，病因不是单一的一类。

### 已排除的三个「显然的」解释

都做了实验，结论都是「不是原因」。

**一、不是「卡片向量建自整段块文本、把中文标题/路径混进来稀释了机制语义」。**

这是真实的文档与实现不一致：`cluster.py` 的 docstring 声称「向量建立在语言无关的
mechanism_desc 上」，而 `_load_cards` 取的向量块文本来自 `build_card_text` =
mechanism_desc + summary + title + tags + evidence 路径/符号 + symbol（实测样例：
572 字符里有 84 个非 ASCII）。用同一嵌入器把 78 张卡的**纯 mechanism_desc**
重建向量，对同样 20 对重算（`reports/v4_emb.py`），结果**更差**：
F1 0.333 vs 0.400，最高 TP 之上仍有 5 个 FP。混合文本里的路径与标题是给 FTS5
和人看的，对向量是噪声，但**去掉它们并不能把真模式救回来**。
重建的 `cos_full` 与库中现存向量在 20 对里 17 对逐位相同，确认实验忠实。

**二、不是粒度太粗。** 改用功能级（feature）向量聚类对照（`reports/v4_feat.py`）：
18 个功能向量的跨仓对最高只到 0.6191，且前几名全是错配（退避 × 拒斥 0.6191、
原子领取 × 重试循环 0.6064）。功能级更粗，描述被整个 feature 的多张卡稀释，
**不能替代卡片级**。

**三、不是阈值没调好。** 见上表。

### 本轮定案

**这个 embedding 在「判别同一机制」这件事上就是判别力不足，而余弦阈值法没有别的旋钮可拧。**
因此定案是**三条并列**，而不是继续调参：

1. **0.60 维持不变**——它是当前语料下 F1 最优的位置（0.400），也是唯一不丢真模式的
   阈值。提到 0.65 精度反而归零，降到 0.60 以下纯增误报。
2. **`list_patterns` 必须把 `min_score` 如实暴露给调用方**（本轮实现）。
   既然 server 分不出松紧，就**不要假装分得出**：把成员的最紧余弦交出去，让调用
   agent 自己按阈值筛，而不是由 server 静默给一个「看起来很确定」的簇。
   零假成功的另一面是**零假确定**。
3. **精度的真正解法不在聚类层，在召回层与读法**——`pattern` 定位为**候选发现**
   （告诉你这两家都在处理重试，值得去看），不当结论用；G25 rerank 负责把真模式在
   检索层顶上来（**实测中**）；判断同构与否最终仍由读卡片的 agent 自己做。

### 真实落库结果与残余风险

**已落库的 5 个 pattern 里，只有 1 个含人工判定的真同机制对**
（`cluster-c8049ad61d`：`PTNexus`「空标题用哨兵错误而非空结果」
× `axum`「逐层剥包装再对成因下转」，0.6033）。其余 4 个簇是纯误报
（`reports/v4_clusters.py`）。**如实记下：当前语料下 `patterns=5` 这个数字里，
真模式含量是 1/5。**

残余风险（本轮新增）：

- **样本量小。** 20 对的 25.0% 精度有较宽的置信区间，本轮只用于**定性**
  （交错、无分离阈值），不当作精确的量化结论。
- **TP 的机制类型有偏。** 5 个 TP 全是「剥掉判断、包装、权威值覆盖」这一类
  **错误/状态传递型**机制，没有一个是控制流型（如「退避时长怎么算」）。
  小样本下这可能只是巧合，**不能据此断言该 embedding「只擅长这类」**。
- **换更强的 embedding 是否能解决，属未测。** 本地只有
  `paraphrase-multilingual-MiniLM-L12-v2` 一个模型（455M 缓存），
  本轮不对「换模型能否解决」下任何结论。
- **`min_score` 暴露后，调用方若忽略它直接引用簇标题，误报会重新变成假知识。**
  文档与 `help` 已写明「簇标题是线索」。


---

### `G29. 三通道 RRF 的投票权重（substr 改条件投票）`

**已定（第 4 批，实测）**：`vector = keyword = 1.0`；`substr` **仅在 keyword 返空时以 1.0 兜底投票**。
原来的「三通道等权」被 V3 十五探针实测推翻。

**为什么**：trigram 降级路径上 keyword 与 substr 是**同一个测量**（13/15 探针
返回完全相同的集合，平均 Jaccard 0.932；整串层级也都要求「整串连续出现」）。
两者各占 1/3 等于把**字面信号加权两次**，稳压只在向量通道命中的语义最佳块——
探针 4（目标卡对查询 trigram 命中 0/27）从 rank=1 掉出 top-10。指标从
top-1 9/15、top-10 14/15、MRR 0.722 变为 **10/15、15/15、0.776**。
留一法（14 探针子集）确认 kw=1.0 / sub=0 落在最优平台正中（均值 0.773–0.776、
最差 0.757–0.760），不是尖峰。

**为什么保留 keyword 而非 substr**：bm25 有长度归一化（探针 3 把 12392 字符的
目录块排第 1），substr 的裸命中计数没有（探针 14 上 substr 返 80 条、keyword 只返 20 条）。

**兜底不是装饰**：强制 keyword 返空（模拟 FTS5 失效）时，substr 独立救回探针 11
（MISS → rank 3），top-10 从 14/15 回到 15/15。符合「零假成功」：单点失效检索不哑掉，
降级写进 `notes` 不静默。

**没做的**：给文本通道加 kind 过滤（滤 `report_section` 目录块）——实测让指标
**变差**（MRR 0.722 → 0.689），假设被数据否掉。

实现见 `src/memex/store/search.py`（`search()` 融合段）；
回归测试 `tests/test_search_weights.py`。

---

### G23. 质量项定成硬门禁，不定成阈值

**结论**：`code_mismatch == 0` 保持硬门禁（已是 B9）；
**其余质量项定成「校验器规则门禁」，不定分数阈值。**
五轴雷同新增 `axis_reuse` 问题码；`axis_completeness` / `evidence_coverage`
在提交路径上**恒为 1.0**，没有可用的分布，定阈值只会造出永远通过的空门禁。

**为什么不是阈值**。V2 五仓实测（`quality_json` 全字段）里，
三项质量指标零区分度：`axis_completeness=1.0`、`evidence_coverage=1.0`、
`code_mismatch=0`，五个仓一模一样。规模类（features 3–5 / cards 11–22）
与仓库体量强相关，不是质量。
更深一层：`commit` 只在 `is_valid=True` 时执行，校验器已拦住空轴、占位符、
缺证据，所以「非空轴占比」「有证据卡占比」**结构上不可能不是 1**——
它们不是「分布好」，是**没有分辨力**。
（顺带修掉写死这两个值的 bug：`commit.py` 曾把两个 1.0 硬编码进
`quality_json`，从不读校验器。现在改为真算，并新增两个真有区分度的
`axis_diversity` / `reusable_rate`。PTNexus 实测 `reusable_rate=0.889`
是五仓唯一有变化的真质量项。）

**能拒的是规则，不是分数**。九类坏样本实测（`reports/g23_gates.py`）：
八类当场被拦，唯一漏网的「五轴正文一字不差」已补 `axis_reuse` 门禁。
现在九类全部拦住，基准（未经改动的 axum 报告）仍放行：

| 坏样本 | problem code |
|---|---|
| 占位符 TBD / 无 | `blank_principle` |
| 五轴正文完全相同 | `axis_reuse` |
| snippet 缺 code | `code_required` |
| mechanism 带 code_spans | `code_forbidden` |
| code 与仓库不符 | `code_span_mismatch` |
| 行号越界 | `line_out_of_range` |
| mechanism_desc 写成中文 | `principle_too_short` + `unicode_language_mismatch` |
| 所有卡 reusable=0 | `bad_enum` |

所以「质量阈值」这个空缺的正确答案是：**质量由结构化规则保证，不由分数保证**。
若将来某指标真出现分布跨度，再单独定阈——那时才是有数据支撑的。

实现见 `src/memex/contract/validator.py`（`_axis_signature` / `_counts`）
与 `src/memex/analyze/commit.py`；测试 `tests/test_validator_axes.py`、
`tests/test_quality_metrics.py`。

---

## G 组（第 4 批收尾）

第 1–3 批（G1–G3、G4–G9、G10–G22、G27、G28）与第 4 批的 G24 / G29 **已定**（见上）。
仅剩第 4 批 2 项，**必须先用真实数据实测才能定**（现在拍必然错）：

- ~~**G23 质量阈值**~~ —— **已定**：质量定成硬门禁不定阈值，详见上方 G23 条目。
- **G25 rerank 选型** —— D2 已定「质量必需」；**V4 选具体 cross-encoder 并测 Top-3 提升，
  若无提升如实记录**。
- ~~**G26 远程会话清扫**~~ —— **已定**：清扫挂到「每次工具调用 + 60s 后台线程」双路，并发修掉两个 bug，实测 n=200 全通。详见下方 G26 条目。
- **G25 rerank 选型** —— 唯一待实测项。

逐条列在 [`gaps.md`](gaps.md)。**第 4 批全部定案后本文件即完结。**


### G26 远程会话清扫与并发形态（已定）

**结论：清扫挂「每次工具调用 + serve-http 60s 后台线程」双路；并发上把 serve-http 修到 n=200 客户端全通；暂不换 Go/Rust。**

**先说实测踩的坑——这次差点被三次「假通过」骗过去，值得记下来：**

1. **只压 `tools/list` 报 8/8 全过。** `tools/list` 不碰数据库，压根走不到出问题的路径。并发压测**必须打真正读库的工具**
（`list_repos`）。
2. **判成功看了 `result.error` 和 `result.ok`，两个字段都不存在。**真实结构是
   envelope（`mcp/envelope.py`）在 server 层包成 `result.structuredContent.ok`。
   第一版据此报「成功 0/16」，第二版据此报「16/16 全过」——**两个都是错的**。
3. **每客户端连发 3 次请求时 n>=32 冒出 `weird {}`，看起来像 SQLite 问题。**收敛成每客户端 1 次后 n=32 全通 —— 那个 `{}` 是压测客户端自身的伪影。压测工具自己也要收敛。

**Bug 1：清扫是死代码。** `grep -rn 'sweep(' src/memex/` 只命中 `session/manager.py:189` 的定义本身，**零调用点**。
而 `manager.py` 的 docstring 与 `docs/operations.md` 都承诺「每次工具调用时顺带执行」——**文档承诺了机制，代码里根本没有**。
真实库会话全是 committed（无积压），所以从未暴露。
修法：在 `mcp/handlers.py` 的 `dispatch()` 里每次调用前调 `_sweep_sessions(rt)`，用非阻塞锁保证多线程下
同一时刻只有一个清扫在跑（拿不到锁就跳过本轮，不把清扫排成调用延迟）；
清扫异常只写 stderr —— 清扫是维护性工作，失败不该把用户正常调用变成 error，
但也绝不能静默无痕。远程另加 60s 后台线程：没人调用就永远不清扫，故它是必需的、不是可选优化。

**Bug 2：serve-http 超过 2 个并发客户端就崩。** 所有请求线程共用同一个 `Server`（因而共用同一个懒加载 SQLite 连接），
而 `store/db.py` 的 `sqlite3.connect()` 没传 `check_same_thread=False`（默认 True）。实测：

| 并发客户端 | 结果 |
|---|---|
| 1 / 2 | 全成功 |
| 4 / 8 / 16 | 大量 `ok:false` + `internal`，details 为 `SQLite objects created in a thread can only be used in that same thread` |

修法：连接开 `check_same_thread=False`，**但以 serialized 编译为前提**——`sqlite3.threadsafety >= 3`，
不满足时 `connect()` **显式报错拒绝服务**。放开后在非 serialized 构建上会静默走向
随机数据竞争，那才是零假成功的真正反例。写路径由 SQLite 自身保证：WAL + `busy_timeout=30000`，
`isolation_level=None`（autocommit）下每条语句各自成事务，不会跨线程拼出半个事务。

**Bug 3（独立于 Bug 2）：accept backlog 只有 5。** `socketserver.TCPServer.request_queue_size` 默认 5，`ThreadingHTTPServer` 继承之。
修完 Bug 2 以为并发已解决，实测 n=32 全通、**n=64 起大批 `ConnectionResetError(104)`**（内核直接丢连接，与 SQLite 无关）。
修法：`class _Server(ThreadingHTTPServer): request_queue_size = 128`。
两个 bug 独立：只修连接不管 backlog，n>=64 仍会随机被重置。

**修复后实测（真 HTTP、每客户端 1 次请求）：n=8/16/32/64/128/200 全部全通。**

**§4.8「何时才值得换 Go/Rust」的判断依据，现在有了实测答案：不需要换。**
三个前提里「单机支撑几十个并发 agent」此前**根本不成立**（连 4 个都不到），现在 n=200 成立；
剩下的 GIL / 内存占用**实测不是瓶颈**（瓶颈仍是嵌入模型，而非语言）；即便将来真要换，按 §4.8 也只换边缘网关层。结论：**维持 Python。**

**残余风险**：① 「几十个并发」只验到 200 **并发读**，写路径（`commit_report` 等）在
autocommit + WAL 下靠 busy_timeout 兜底，未做写压测；② `request_queue_size = 128` 在**超过 128
的同时建连**时仍会溢出（属内核行为，可按需调大）；③ 多进程 / 多副本部署未验证，届时要重新确认
写冲突与清扫的幂等性。详见 `tech-design.md` §4.6.1 与 `operations.md` §1.1。
