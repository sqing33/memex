# MCP 工具签名（v1 契约）

> **这是什么**：`tech-design.md` §3.1 定义 `mcp/` 目录 = 「协议层 + 17 个工具的 handler」，
> 但 §5 表里一直写着「签名细节见后续《MCP 工具签名》」。**本文就是那份文档**——
> 逐个给出 17 个工具的入参/出参 JSON Schema，以及必须先统一的全局约定。
>
> **状态**：✅ 已定案（`decisions.md` **G1 / G2**；T15–T16 于 **G8** 补入；T17 于 **G22** 补入）。本文即 V1 的接口规格。
>
> **配套**：报告契约的机器可读 schema → [`report.schema.json`](report.schema.json)；
> 散文契约 + 卡片类型锚定例 → [`report-contract.md`](report-contract.md)。

---

## 0. 总览

17 个工具分四类，正好对应「机械活留 server / 生成判断留 agent」这条第一原则
（`solution-analysis.md` §5.2）：

| # | 工具 | 类别 | 一句话 |
|---|---|---|---|
| T1 | `fetch_repo` | 机械 | clone 一个公开仓，登记并返回 `repo_id` / commit / 克隆路径 |
| T2 | `get_evidence_pack` | 机械 | 目录树 + 入口点 + 符号清单（**不调模型、不给代码切片**） |
| T3 | `read_file_slice` | 机械 | 按需取真实字节（**别整文件塞**） |
| T4 | `request_repo_bundle` | 机械 | **仅远程形态**：把克隆打成 `git bundle` 单文件下发 |
| T5 | `begin_analysis` | 写入 | 开会话，返回 `session_id` + 证据包 + 契约 + 清单 + `next_step` |
| T6 | `validate_report` | 写入 | **纯校验、只读、幂等** —— agent 提交前自查 |
| T7 | `commit_report` | 写入 | **唯一落库口**，全量校验，失败返回**全部**问题 |
| T8 | `search_implementations` | 召回 | **主入口**：按功能意图跨仓召回 |
| T9 | `get_card` | 召回 | 取单张卡片全文（含真实代码切片与证据链） |
| T10 | `list_patterns` | 召回 | 跨仓模式（≥2 个仓库收敛的簇） |
| T11 | `get_report` | 召回 | 取某仓某次分析的报告（JSON / Markdown） |
| T12 | `list_repos` | 召回 | 已收录仓库目录（含 stale 标记与质量摘要） |
| T13 | `recall_stats` | 召回 | 知识库总览与质量指标（按 producer 分组） |
| T14 | `help` | 元 | 5 个固定 topic 的结构化说明，**任何 MCP 客户端都能读** |
| T15 | `forget_analysis` | 破坏 | **默认禁用**：硬删一次分析 + 级联（G8） |
| T16 | `forget_repo` | 破坏 | **默认禁用**：硬删一个仓库 + 其上全部分析（G8） |
| T17 | `upload_repo_bundle` | 机械 | **仅远程/离线**：`git bundle` 上传登记（G22，兼解私有凭据变体） |

**未纳入本版**（别误以为漏了）：

| 工具 | 归属 | 为什么不在 V1 |
|---|---|---|
| `analyze_repo`（服务端 `batch` 直跑） | `gaps.md` G21 | 与 agent 驱动共用同一套校验，排在 V5 |
| `export_site` | — | 是 CLI 子命令（`memex export-site`），不是 MCP 工具 |

---

## 1. 全局约定（14 条，先读这节）

> 这一节解决的就是 `gaps.md` G1 里列的那几个「命名不统一」问题。
> **所有工具都必须遵守，没有例外。**

### 1.1 命名

1. **仓库一律用 `repo_id`**，格式 `<host>__<owner>__<name>`（与 `$MEMEX_HOME/repos/` 的目录名同源）：
   例 `github.com__sqing33__vibecraft`。`fetch_repo` 产出它，其余工具只消费它。
   **不出现 `repo_url` 入参、不出现 `repo` 缩写。** 展示名另用返回字段 `repo_full_name`。
2. **行号一律 1-based、闭区间**：`[start_line, end_line]`。单行时 `end_line == start_line`。
   `end_line` 省略 = 单行。**不出现 `start` / `end` 这种缩写**（recall 的旧写法作废）。
3. **所有证据引用一律用对象形式** `{path, start_line?, end_line?, symbol?, note?}`；
   recall 的紧凑字符串形式 `"a/b.py:12-30"` **仅作为展示用字符串出现在出参里，不作为入参**。
4. **布尔入参一律 `is_*` 前缀**（`is_stale_only` 而非 `stale_only`），避免与出参同名字段混淆。

### 1.2 出参形状

5. **统一信封**：任何工具的 `structuredContent` 都是 `{"ok": <bool>, ...}`。
   - 成功：`{"ok": true, <payload>}`
   - 失败：`{"ok": false, "error": {"code": "...", "message": "...", "details": {...}}}`
6. **列表一律信封**：`{"count": <本次返回条数>, "total": <可选，总数>, "items": [...], "next_cursor": <可选>}`。
   **不返回裸数组。**
7. **分页一律游标式**：入参 `limit`（默认 50，最大 500）+ `cursor`（不透明字符串，`next_cursor` 原样回传）。
   **不用 offset**——知识库会边查边变，offset 会漏项/重项。
8. **`detail` 一律三档** `brief | normal | full`（默认 `normal`）。recall 的
   `compact / semi / full` 作废。约定：`brief` 只给标识与标题；`normal` 加摘要与证据引用；
   `full` 加真实代码切片与完整 `mechanism_desc`。
9. **`next_step` 是结构化对象，不是字符串**：
   `{"action": "<工具名>", "args": {...}, "hint": "<一句人话>"}`。
   流程走完或无需下一步时**字段省略**（不是 `null`、不是空串）。
10. **时间一律 UTC、RFC 3339**（`2026-02-14T08:31:00Z`）；时长字段带单位后缀（`ttl_seconds`）。
11. **大小/行数一律整数字节或行**，不用「约」「~」。

### 1.3 工具元数据（G2）

12. **每个工具都必须声明 `annotations`**（MCP `ToolAnnotations`，四档提示位），
    取值见 §3 的表。E16「危险工具默认禁用」与 `gaps.md` G28 的禁用清单**直接由这张表机械导出**。
13. **每个工具都必须声明 `outputSchema`**，并同时返回 `structuredContent`。
    **不允许把 JSON 序列化成散文塞进 `content[].text`**——
    `text` 只放一句人话摘要（给纯文本客户端兜底）。

### 1.4 错误模型（G2 的核心结论）

14. **协议错误（JSON-RPC `error`）只用于「调用方把工具用错了」**：
    未知工具名（`-32602`）、参数不是合法 JSON、`args` 结构无法解析。
    **一切业务失败都走 `{"ok": false, "error": {...}}` 的正常返回**，`isError` 保持 `false`。

    ```
    ✅ 校验不过      → {"ok": false, "error": {"code": "invalid_report", ...}}   （正常返回）
    ✅ 仓库不存在    → {"ok": false, "error": {"code": "not_found", ...}}        （正常返回）
    ❌ 未知工具名    → JSON-RPC error -32602                                     （协议错误）
    ```

    **理由**：业务失败是**可预期的正常结果**（agent 要靠它自我修正），
    而协议错误意味着「这个请求本身没被理解」。混在一起会让 agent 分不清
    「我写错了」和「我的报告不合格」——后者是要它改的，前者是要它换个调用法。

**错误码枚举**（`error.code`，全局封闭集，新增需改本文）：

| code | 含义 | 典型触发 |
|---|---|---|
| `invalid_argument` | 入参不合法（缺必填、枚举越界、行号越界） | 传给 `read_file_slice` 的 `end_line` < `start_line` |
| `not_found` | 目标不存在 | 未知 `repo_id` / `card_id` / `session_id` |
| `invalid_report` | 报告未通过契约校验（**带 `details.problems[]`**） | `commit_report` / `validate_report` |
| `conflict` | 幂等键冲突且需显式决定 | 同 `(repo_id, commit_sha, contract_version)` 已有内容不同的分析 |
| `stale_repo` | 仓库在会话期间被更新 | `commit_report` 时克隆目录的 commit 变了 |
| `fetch_failed` | 抓取/网络失败 | clone 超时、远端 404、超过大小上限 |
| `unsupported` | 形态不支持 | 远程调用 `local:` 路径；stdio 下调 `request_repo_bundle` 也允许（无害） |
| `rate_limited` | 触发限流/配额 | 每 token 的请求数或仓库大小超限 |
| `disabled` | 该工具被服务端禁用 | `MEMEX_TOOLS` 未放行该类别（G28） |
| `internal` | 服务端异常（**带 `details.trace_id`**） | 未预期异常 |

### 1.5 共享类型（17 个工具的 schema 都 `$ref` 到这里）

```json
{
  "$defs": {
    "RepoId": {
      "type": "string",
      "pattern": "^[a-z0-9.-]+__[A-Za-z0-9._-]+__[A-Za-z0-9._-]+$",
      "description": "host__owner__name，host 全小写、owner/name 原样"
    },
    "Detail": {
      "type": "string",
      "enum": ["brief", "normal", "full"],
      "default": "normal"
    },
    "EvidenceRef": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path"],
      "properties": {
        "path":       { "type": "string", "description": "仓库内相对路径（posix 分隔符）" },
        "start_line": { "type": "integer", "minimum": 1 },
        "end_line":   { "type": "integer", "minimum": 1 },
        "symbol":     { "type": "string" },
        "note":       { "type": "string" }
      }
    },
    "CodeSpan": { "$ref": "#/$defs/EvidenceRef" },
    "RepoSummary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["repo_id", "repo_full_name"],
      "properties": {
        "repo_id":        { "$ref": "#/$defs/RepoId" },
        "repo_full_name": { "type": "string" },
        "url":            { "type": "string" },
        "host":           { "type": "string" },
        "language":       { "type": "string" },
        "stars":          { "type": "integer" },
        "license":        { "type": "string" },
        "head_sha":       { "type": "string" },
        "analyzed_sha":   { "type": "string" },
        "is_stale":       { "type": "boolean" },
        "is_fork":        { "type": "boolean", "description": "G10" },
        "fork_of":        { "type": "string",  "description": "G10：上游全名" },
        "subpath":        { "type": "string",  "description": "G15：monorepo 子目录范围" },
        "source":         { "enum": ["clone", "tar", "upload", "local"], "description": "G22：'upload' = 经 T17 投喂" },
        "cloned_at":      { "type": "string" }
      }
    },
    "NextStep": {
      "type": "object",
      "additionalProperties": false,
      "required": ["action", "hint"],
      "properties": {
        "action": { "type": "string", "description": "下一个要调用的工具名" },
        "args":   { "type": "object", "description": "建议直接透传的入参" },
        "hint":   { "type": "string" }
      }
    },
    "ToolError": {
      "type": "object",
      "additionalProperties": false,
      "required": ["ok", "error"],
      "properties": {
        "ok": { "const": false },
        "error": {
          "type": "object",
          "additionalProperties": false,
          "required": ["code", "message"],
          "properties": {
            "code": {
              "enum": ["invalid_argument", "not_found", "invalid_report", "conflict",
                       "stale_repo", "fetch_failed", "unsupported", "rate_limited",
                       "disabled", "internal"]
            },
            "message": { "type": "string" },
            "details": { "type": "object" }
          }
        }
      }
    }
  }
}
```

> **outputSchema 的统一写法**：MCP 官方 SDK 要求 `outputSchema` 的**根必须是
> `{"type":"object"}`**（否则客户端 zod 校验报 `expected "object"`，见 `gaps.md` G2 实测项）。
> 因此每个工具的 `outputSchema` 统一为：
>
> ```json
> {
>   "type": "object",
>   "properties": {
>     "ok": { "type": "boolean" },
>     "error": { "type": "object", "required": ["code", "message"] }
>   },
>   "required": ["ok"],
>   "additionalProperties": true,
>   "oneOf": [{"$ref": "#/$defs/ToolError"}, {"<下列 payload，其 ok 为 const true>"}]
> }
> ```
>
> 即：根 `type:"object"`（SDK 硬性要求）+ 顶层 `properties` 列出 `ok`/`error`（便于校验器
> 推断）+ `oneOf` 精确区分「错误信封 / payload」两分支。下文只给 **payload**
> （`ok:true` 分支），不重复 `ToolError`。

---

## 2. 工具签名

> 格式：**用途 → annotations → 入参 → 出参 payload → 要点**。
> 所有出参都可以是 `ToolError`，下文不再重复声明。

### T1 · `fetch_repo` — 登记一个仓库

**用途**：clone 公开仓库到 `$MEMEX_HOME/repos/<repo_id>/`，写 `repos` 表，返回定位信息。
**annotations**：`{"title":"Fetch repository","readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_url"],
  "properties": {
    "repo_url": { "type": "string", "description": "http(s) 或 git 形式的公开仓地址" },
    "ref":      { "type": "string", "description": "分支/tag/commit；省略则用默认分支" },
    "subpath":  { "type": "string", "description": "monorepo 子目录（G15）；省略 = 全仓" },
    "refresh":  { "type": "boolean", "default": false, "description": "已收录时是否重新比对远端 head_sha" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "repo"],
  "properties": {
    "ok": { "const": true },
    "repo": { "$ref": "#/$defs/RepoSummary" },
    "repo_path": { "type": "string", "description": "本地克隆绝对路径；仅 stdio 本地形态可用" },
    "is_new": { "type": "boolean" },
    "warnings": { "type": "array", "items": { "type": "string" } },
    "next_step": { "$ref": "#/$defs/NextStep" }
  }
}
```

**要点**
- **`repo_path` 只在本地形态有意义**。远程形态该字段省略，`next_step` 指向 `request_repo_bundle`。
- 幂等：同 `(repo_url, ref)` 重复调用返回同一 `repo_id`；`refresh:true` 会重比 `head_sha`，
  不一致则更新 `head_sha` 并给 `is_stale`（B8），**但绝不自动重析**。
- `subpath` 记在 `repos.subpath`，后续 `get_evidence_pack` 以它为边界（G15）。

### T2 · `get_evidence_pack` — 静态证据包

**用途**：给 agent 一张「仓库地图」：目录树 + 入口点 + 符号清单。**纯计算，不调模型，不给代码切片**
（切片按需用 T3，或直接读本地克隆）。
**annotations**：`{"title":"Get evidence pack","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id"],
  "properties": {
    "repo_id": { "$ref": "#/$defs/RepoId" },
    "depth":   { "type": "string", "enum": ["fast", "standard", "deep"], "default": "standard",
                 "description": "证据包详略 + 后续校验严格度；具体阈值见 gaps.md G5" },
    "detail":  { "$ref": "#/$defs/Detail" },
    "subpath": { "type": "string", "description": "只看某个子目录；省略则用 repos.subpath" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "repo", "commit_sha", "tree", "entry_points", "symbols", "stats"],
  "properties": {
    "ok": { "const": true },
    "repo": { "$ref": "#/$defs/RepoSummary" },
    "commit_sha": { "type": "string" },
    "tree": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "type"],
      "properties": {
        "path":     { "type": "string" },
        "type":     { "enum": ["dir", "file"] },
        "size":     { "type": "integer" },
        "lang":     { "type": "string" },
        "children": { "type": "array", "items": { "type": "object",
                      "description": "同一结构递归；C 实现里用 $defs.TreeNode 表达" } }
      }
    },
    "entry_points": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "role"],
        "properties": {
          "path": { "type": "string" },
          "role": { "type": "string" },
          "kind": { "enum": ["main", "cli", "server", "worker", "test", "config", "build", "docs"] }
        }
      }
    },
    "symbols": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "kind", "path"],
        "properties": {
          "name":       { "type": "string" },
          "kind":       { "enum": ["class", "function", "method", "const", "type", "interface"] },
          "path":       { "type": "string" },
          "start_line": { "type": "integer" },
          "end_line":   { "type": "integer" },
          "exported":   { "type": "boolean" },
          "lang":       { "type": "string" },
          "signature":  { "type": "string", "description": "detail=full 时给出" }
        }
      }
    },
    "groups": {
      "type": "array",
      "description": "按顶层目录聚合，便于 agent 先看骨架",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "files"],
        "properties": {
          "path":       { "type": "string" },
          "files":      { "type": "integer" },
          "entry_like": { "type": "boolean" }
        }
      }
    },
    "stats": {
      "type": "object",
      "additionalProperties": false,
      "required": ["files", "dirs", "symbols_total", "symbols_returned", "truncated"],
      "properties": {
        "files":            { "type": "integer" },
        "dirs":             { "type": "integer" },
        "bytes":            { "type": "integer" },
        "symbols_total":    { "type": "integer" },
        "symbols_returned": { "type": "integer" },
        "truncated":        { "type": "boolean" },
        "truncation_reason":{ "type": "string", "description": "如 max_files=2000；截断必须显式并进报告" }
      }
    },
    "next_step": { "$ref": "#/$defs/NextStep" },
    "warnings":  { "type": "array", "items": { "type": "string" } }
  }
}
```

**要点**
- **绝不给代码切片**（A2）。给了就等于把 agent 的上下文预算烧在这里。
- 超大仓**截断 + 标记**（`stats.truncated` + `truncation_reason`），**不拒绝**（G5）。
- `symbols` 有上限（`depth` 决定），超了截断并如实报 `symbols_total`。

### T3 · `read_file_slice` — 按需取真实字节

**用途**：取真实文件的一段（含起止行、`file_sha`）。**别整文件塞**。
**annotations**：`{"title":"Read file slice","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id", "path"],
  "properties": {
    "repo_id":    { "$ref": "#/$defs/RepoId" },
    "path":       { "type": "string" },
    "start_line": { "type": "integer", "minimum": 1, "default": 1 },
    "end_line":   { "type": "integer", "minimum": 1 },
    "max_lines":  { "type": "integer", "default": 400, "maximum": 2000 }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "path", "start_line", "end_line", "text", "file_sha"],
  "properties": {
    "ok":         { "const": true },
    "path":       { "type": "string" },
    "start_line": { "type": "integer" },
    "end_line":   { "type": "integer" },
    "total_lines":{ "type": "integer" },
    "text":       { "type": "string" },
    "file_sha":   { "type": "string", "description": "该文件在该 commit 下的 sha256" },
    "truncated":  { "type": "boolean" }
  }
}
```

**要点**：路径穿越防护（`..`、绝对路径、符号链接逃逸一律 `invalid_argument`）。
批量读请本地克隆（T4）后用客户端自带 `Read`/`Grep`——**不要 per-file 往返 MCP**。

### T4 · `request_repo_bundle` — `git bundle` 下发（远程必需）

**用途**：服务端把克隆打成单个 `git bundle` 文件交给 agent 拉回本地，换取**零 MCP 文件往返**。
**annotations**：`{"title":"Request repo bundle","readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id"],
  "properties": {
    "repo_id": { "$ref": "#/$defs/RepoId" },
    "ref":     { "type": "string" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "url", "sha256", "commit_sha"],
  "properties": {
    "ok":         { "const": true },
    "url":        { "type": "string", "description": "一次性下载地址（带短时签名）" },
    "sha256":     { "type": "string" },
    "bytes":      { "type": "integer" },
    "commit_sha": { "type": "string" },
    "expires_at": { "type": "string" },
    "usage":      { "type": "string", "description": "git clone <url> <dir>；或 curl + git bundle unbundle" }
  }
}
```

**要点**：这是 E18 的落地点。**降级路径**是批量 `read_file_slice`（提醒 agent 批量读）。
stdio 本地形态下调用它也允许（返回本地文件 `file://` 路径或直接告知用 `repo_path`），无害。

### T5 · `begin_analysis` — 开会话

**用途**：开会话，一次性下发 agent 需要的全部东西：证据包 + 契约 + 步骤清单 + `next_step`。
**annotations**：`{"title":"Begin analysis","readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id"],
  "properties": {
    "repo_id":      { "$ref": "#/$defs/RepoId" },
    "ref":          { "type": "string" },
    "depth":        { "type": "string", "enum": ["fast", "standard", "deep"], "default": "standard" },
    "analyst":      { "type": "string", "description": "谁在做这次分析（G19）。省略则由服务端用 MCP initialize 的 clientInfo 预填" },
    "include_pack": { "type": "boolean", "default": true, "description": "false 则只返回会话，稍后用 T2 单独取" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "session_id", "state", "contract", "checklist", "expires_at", "next_step"],
  "properties": {
    "ok":         { "const": true },
    "session_id": { "type": "string" },
    "state":      { "enum": ["begun", "evidence_taken", "drafting", "validated", "committed", "abandoned"] },
    "repo":       { "$ref": "#/$defs/RepoSummary" },
    "commit_sha": { "type": "string", "description": "会话绑定的 commit；commit 变了会拒绝提交（G20）" },
    "evidence_pack": { "description": "同 T2 出参 payload；include_pack=false 时省略" },
    "contract": {
      "type": "object",
      "additionalProperties": false,
      "required": ["contract_id", "schema_url", "schema"],
      "properties": {
        "contract_id": { "const": "memex/report/1" },
        "schema_url":  { "type": "string" },
        "schema":      { "type": "object", "description": "内联的报告 JSON Schema" },
        "anchors":     { "type": "object", "description": "每种卡片 kind 的定义 + 正例 + 反例（B6）" }
      }
    },
    "checklist":  { "type": "array", "items": { "type": "string" } },
    "expires_at": { "type": "string" },
    "analyst":    { "type": "string" },
    "next_step":  { "$ref": "#/$defs/NextStep" }
  }
}
```

**要点**
- 会话**存 SQLite**（`tech-design.md` §2.6），默认 TTL 2 小时，任意工具调用顺带惰性清扫。
- `contract` 与 `report.schema.json` **同源**——三处（`help` 散文 / `validate_report` 结构校验 /
  这里下发）不会漂移。
- **会话绑定 `commit_sha`**（G20）：提交前服务端核对克隆目录仍是该 commit，不是则报 `stale_repo`。

### T6 · `validate_report` — 纯校验（只读、幂等）

**用途**：agent 提交前自查。**不落库、不改状态、可无限次调用**。
**annotations**：`{"title":"Validate report","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["report"],
  "properties": {
    "report":  { "type": "object", "description": "memex/report/1 契约对象" },
    "repo_id": { "$ref": "#/$defs/RepoId", "description": "给了才能校验证据路径是否真实存在" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "is_valid", "problems", "warnings", "counts"],
  "properties": {
    "ok":       { "const": true },
    "is_valid": { "type": "boolean" },
    "problems": { "type": "array", "items": { "$ref": "#/$defs/Problem" } },
    "warnings": { "type": "array", "items": { "$ref": "#/$defs/Problem" } },
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "features":         { "type": "integer" },
        "cards":            { "type": "integer" },
        "code_spans":       { "type": "integer" },
        "evidence_refs":    { "type": "integer" },
        "principle_units":  { "type": "object" }
      }
    }
  }
}
```

**要点**：**除 `problems[].code` 外还必须给 `where`（JSON Pointer，如 `/features/2/cards/0/code`）**，
让 agent 能精确定位（见下方 `Problem` 定义）。`is_valid=false` 时 `ok` 仍为 `true`——
**「校验器正常工作了」和「报告通过了」是两件事**，别混进 `ok:false`。

```json
{
  "$defs": {
    "Problem": {
      "type": "object",
      "additionalProperties": false,
      "required": ["code", "where", "message"],
      "properties": {
        "code": { "enum": ["unknown_field", "missing_field", "too_few_features", "duplicate_feature_key",
                           "blank_principle", "principle_too_short", "bad_evidence_path",
                           "line_out_of_range", "code_span_mismatch", "code_required",
                           "code_forbidden", "bad_enum", "unicode_language_mismatch"] },
        "where":   { "type": "string", "description": "JSON Pointer 到出错位置" },
        "message": { "type": "string" }
      }
    }
  }
}
```

### T7 · `commit_report` — 唯一落库口

**用途**：全量校验通过才落库。**失败返回全部问题**（不是一次报一个）。
**annotations**：`{"title":"Commit report","readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["session_id", "report"],
  "properties": {
    "session_id": { "type": "string" },
    "report":     { "type": "object" },
    "analyst":    { "type": "string", "description": "覆盖开会话时的值（G19）" },
    "force":      { "type": "boolean", "default": false,
                    "description": "幂等键冲突且内容不同时，显式覆盖（B7/G19）" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "is_committed"],
  "properties": {
    "ok":           { "const": true },
    "is_committed": { "type": "boolean" },
    "analysis_id":  { "type": "string" },
    "repo":         { "$ref": "#/$defs/RepoSummary" },
    "commit_sha":   { "type": "string" },
    "contract_id":  { "type": "string" },
    "analyst":      { "type": "string" },
    "producer":     { "enum": ["agent", "batch"] },
    "problems":     { "type": "array", "items": { "$ref": "#/$defs/Problem" } },
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "features":   { "type": "integer" },
        "cards":      { "type": "integer" },
        "reusable_cards": { "type": "integer" },
        "evidence":   { "type": "integer" },
        "chunks_indexed": { "type": "integer" }
      }
    },
    "quality": {
      "type": "object",
      "additionalProperties": false,
      "required": ["code_mismatch"],
      "properties": {
        "code_mismatch":      { "type": "integer", "description": "硬门禁：不为 0 则不落库" },
        "axis_completeness":  { "type": "number" },
        "evidence_coverage":  { "type": "number" },
        "principle_units_min":{ "type": "integer" }
      }
    },
    "next_step": { "$ref": "#/$defs/NextStep" }
  }
}
```

**要点**
- **三条硬规则**：① `code_mismatch != 0` → 拒绝落库（B9）；
  ② 幂等键 `(repo_id, commit_sha, contract_version)` 已存在且内容一致 → `is_committed:false`（no-op，不报错）；
  ③ 已存在但内容不同 → `conflict`，需显式 `force:true`（B7/G19）。
- 卡片代码由服务端**从真实文件重切覆盖**，agent 给的 `code` 只用于比对（这就是 `code_mismatch` 的来源）。
- 落库后自动建 chunk、算向量、跑聚类（`min_repos=2`）。

### T8 · `search_implementations` — 召回主入口

**用途**：给一句功能意图，跨仓返回最贴近的**已分析实现**。
**annotations**：`{"title":"Search implementations","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["query"],
  "properties": {
    "query":      { "type": "string", "minLength": 1, "description": "自然语言功能意图" },
    "limit":      { "type": "integer", "default": 8, "minimum": 1, "maximum": 50 },
    "repo_id":    { "$ref": "#/$defs/RepoId", "description": "限定单仓" },
    "language":   { "type": "string", "description": "按实现语言过滤（如 go / python / typescript）" },
    "kind":       { "enum": ["feature", "card", "pattern", "report_section"], "description": "限定 chunk 类型" },
    "detail":     { "$ref": "#/$defs/Detail" },
    "rerank":     { "type": "boolean", "default": false, "description": "叠加 cross-encoder 重排（$MEMEX_RERANK）" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "query", "detail", "channels", "results"],
  "properties": {
    "ok":     { "const": true },
    "query":  { "type": "string" },
    "detail": { "$ref": "#/$defs/Detail" },
    "channels": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "vector":  { "type": "integer", "description": "该通道的候选数" },
        "keyword": { "type": "integer" },
        "substr":  { "type": "integer" }
      }
    },
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["score", "matched_by", "chunk_kind"],
        "properties": {
          "score":      { "type": "number", "description": "RRF 融合分；再乘 kind_prior（G14）后排序" },
          "matched_by": { "type": "array", "items": { "enum": ["vector", "keyword", "substr"] } },
          "chunk_kind": { "enum": ["feature", "card", "pattern", "report_section"] },
          "repo":       { "$ref": "#/$defs/RepoSummary", "description": "chunk_kind=pattern 时省略（跨仓，无单一归属，G14）" },
          "repos":      { "type": "array", "items": { "type": "string" }, "description": "chunk_kind=pattern 时的成员仓全名（G14）" },
          "pattern_key": { "type": "string", "description": "chunk_kind=pattern 时给出，可直接喂 T10（G14）" },
          "title":      { "type": "string" },
          "heading":    { "type": "string" },
          "tags":       { "type": "array", "items": { "type": "string" } },
          "language":   { "type": "string" },
          "mechanism_desc": { "type": "string", "description": "detail>=normal；建向量用的英文机制描述" },
          "excerpt":    { "type": "string" },
          "card_id":    { "type": "string", "description": "chunk_kind=card 时给出，可直接喂 T9" },
          "source":     { "$ref": "#/$defs/EvidenceRef" },
          "evidence":   { "type": "array", "items": { "$ref": "#/$defs/EvidenceRef" } },
          "code":       { "type": "array", "items": { "type": "string" }, "description": "仅 detail=full" }
        }
      }
    },
    "notes": { "type": "array", "items": { "type": "string" }, "description": "如「库中无匹配」或「结果已截断」" }
  }
}
```

**要点**
- 三通道 **RRF(k=60)** 融合（vector / keyword / substr），`score` 是融合分不是余弦；
  融合后乘 `kind_prior`（`card 1.0 / feature 0.9 / pattern 0.6 / report_section 0.4`，初值，G14）。
- `chunk_kind='pattern'` 时**无 `repo`**，改用 `repos: [全名…]` + `pattern_key`（G14）。
- **结果必须带 `repo_id` 与版本**（`repo.analyzed_sha` + `repo.is_stale`）——
  借鉴到过时实现在所难免，但要**显式可见**。
- **库为空或全不匹配时不报错**：`results: []` + `notes`。

### T9 · `get_card` — 取单张卡片

**annotations**：`{"title":"Get card","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["card_id"],
  "properties": {
    "card_id": { "type": "string" },
    "detail":  { "$ref": "#/$defs/Detail" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "card"],
  "properties": {
    "ok": { "const": true },
    "card": {
      "type": "object",
      "additionalProperties": false,
      "required": ["card_id", "kind", "reusable", "title"],
      "properties": {
        "card_id":       { "type": "string" },
        "kind":          { "enum": ["mechanism", "snippet", "skeleton", "gotcha", "decision"] },
        "reusable":      { "type": "boolean" },
        "title":         { "type": "string" },
        "summary":       { "type": "string" },
        "mechanism_desc":{ "type": "string" },
        "language":      { "type": "string" },
        "symbol":        { "type": "string" },
        "tags":          { "type": "array", "items": { "type": "string" } },
        "repo":          { "$ref": "#/$defs/RepoSummary" },
        "feature":       { "type": "object", "properties": { "key": { "type": "string" }, "title": { "type": "string" } } },
        "evidence":      { "type": "array", "items": { "$ref": "#/$defs/EvidenceRef" } },
        "code": {
          "type": "array",
          "description": "与 code_spans 一一对齐的真实切片（服务端重切，非 agent 提交的）",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["span", "text"],
            "properties": { "span": { "$ref": "#/$defs/CodeSpan" }, "text": { "type": "string" } }
          }
        },
        "quality": { "type": "object" },
        "patterns": { "type": "array", "items": { "type": "string" }, "description": "该卡所属的 pattern key" }
      }
    }
  }
}
```

### T10 · `list_patterns` — 跨仓模式

**annotations**：`{"title":"List patterns","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "query":  { "type": "string", "description": "对模式标题/tags/意图探针做模糊过滤" },
    "limit":  { "type": "integer", "default": 50, "minimum": 1, "maximum": 500 },
    "cursor": { "type": "string" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "count", "items"],
  "properties": {
    "ok":    { "const": true },
    "count": { "type": "integer" },
    "total": { "type": "integer" },
    "next_cursor": { "type": "string" },
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["pattern_id", "key", "title", "repo_count", "card_count"],
        "properties": {
          "pattern_id": { "type": "string" },
          "key":        { "type": "string" },
          "title":      { "type": "string" },
          "tags":       { "type": "array", "items": { "type": "string" } },
          "card_count": { "type": "integer" },
          "repo_count": { "type": "integer" },
          "languages":  { "type": "array", "items": { "type": "string" } },
          "repos":      { "type": "array", "items": { "$ref": "#/$defs/RepoSummary" } },
          "intents":    { "type": "array", "items": { "type": "string" }, "description": "pattern_intents 的探针文本" }
        }
      }
    }
  }
}
```

**要点**：`repo_count >= 2` 恒成立（`min_repos=2` 是语义过滤器，不是可调项——见 §5.7）。

### T11 · `get_report` — 取报告

**annotations**：`{"title":"Get report","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id"],
  "properties": {
    "repo_id":    { "$ref": "#/$defs/RepoId" },
    "commit_sha": { "type": "string", "description": "省略 = 该仓最新一次分析" },
    "format":     { "enum": ["json", "markdown", "both"], "default": "json" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "repo", "analysis", "report"],
  "properties": {
    "ok":     { "const": true },
    "repo":   { "$ref": "#/$defs/RepoSummary" },
    "analysis": {
      "type": "object",
      "additionalProperties": false,
      "required": ["analysis_id", "commit_sha", "contract_id", "status"],
      "properties": {
        "analysis_id": { "type": "string" },
        "commit_sha":  { "type": "string" },
        "contract_id": { "type": "string" },
        "contract_version": { "type": "string" },
        "analyst":     { "type": "string" },
        "producer":    { "enum": ["agent", "batch"] },
        "depth":       { "type": "string" },
        "status":      { "enum": ["drafting", "ready", "failed", "stale"] },
        "created_at":  { "type": "string" },
        "finished_at": { "type": "string" },
        "counts":      { "type": "object" },
        "quality":     { "type": "object" }
      }
    },
    "report":    { "type": "object", "description": "memex/report/1 契约对象" },
    "report_md": { "type": "string", "description": "format 含 markdown 时给出；4 个固定 H2" }
  }
}
```

### T12 · `list_repos` — 仓库目录

**annotations**：`{"title":"List repos","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "query":        { "type": "string" },
    "language":     { "type": "string" },
    "is_stale_only":{ "type": "boolean", "default": false },
    "limit":        { "type": "integer", "default": 50, "minimum": 1, "maximum": 500 },
    "cursor":       { "type": "string" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "count", "items"],
  "properties": {
    "ok":    { "const": true },
    "count": { "type": "integer" },
    "total": { "type": "integer" },
    "next_cursor": { "type": "string" },
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["repo"],
        "properties": {
          "repo":         { "$ref": "#/$defs/RepoSummary" },
          "features":     { "type": "integer" },
          "cards":        { "type": "integer" },
          "quality":      { "type": "object" },
          "last_analysis_at": { "type": "string" },
          "subpath":      { "type": "string" }
        }
      }
    }
  }
}
```

### T13 · `recall_stats` — 知识库总览

**annotations**：`{"title":"Recall stats","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{ "type": "object", "additionalProperties": false, "properties": {} }
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "schema_version", "contract_id", "embedder", "counts"],
  "properties": {
    "ok":             { "const": true },
    "schema_version": { "type": "string" },
    "contract_id":    { "type": "string" },
    "embedder": {
      "type": "object",
      "additionalProperties": false,
      "required": ["model", "dim"],
      "properties": { "model": { "type": "string" }, "dim": { "type": "integer" } }
    },
    "counts": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "repos":    { "type": "integer" },
        "analyses": { "type": "integer" },
        "features": { "type": "integer" },
        "cards":    { "type": "integer" },
        "patterns": { "type": "integer" },
        "chunks":   { "type": "integer" }
      }
    },
    "quality": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "by_producer": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "agent": { "type": "object" },
            "batch": { "type": "object" }
          }
        },
        "code_mismatch": {
          "type": "object",
          "additionalProperties": false,
          "properties": { "total": { "type": "integer" }, "non_zero_analyses": { "type": "integer" } }
        }
      }
    },
    "stale": { "type": "object", "properties": { "repos": { "type": "integer" } } },
    "disk":  { "type": "object", "properties": { "home": { "type": "string" }, "db_bytes": { "type": "integer" } } }
  }
}
```

### T14 · `help` — 结构化说明

**用途**：任何 MCP 客户端都能读到「怎么做一次分析 / 契约长什么样 / 五类卡片怎么区分」。
**annotations**：`{"title":"Help","readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["topic"],
  "properties": {
    "topic": { "enum": ["analyze-flow", "report-contract", "card-kinds", "search-usage", "patterns"] }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "topic", "title", "markdown"],
  "properties": {
    "ok":            { "const": true },
    "topic":         { "type": "string" },
    "title":         { "type": "string" },
    "markdown":      { "type": "string" },
    "related_topics":{ "type": "array", "items": { "type": "string" } }
  }
}
```

**5 个 topic 的固定内容**（B10）：不在此展开，实现在 `src/memex/mcp/help.py` 的常量表里。
未知 topic → `invalid_argument`，`details.topics` 回全部合法值（**帮 agent 自我修正**）。

---

### T15 · `forget_analysis` — 删除一次分析（破坏性）

**用途**：硬删一次分析及其全部派生数据（G8）。
**annotations**：`{"title":"Forget analysis","readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}`
**类别**：`destructive`（**默认禁用**，见 G28）。

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["analysis_id", "confirm"],
  "properties": {
    "analysis_id": { "type": "string" },
    "confirm":     { "const": true, "description": "必须显式为 true，否则 invalid_argument" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "deleted", "counts", "reclustered"],
  "properties": {
    "ok":         { "const": true },
    "deleted":    { "const": "analysis" },
    "counts":     { "type": "object", "additionalProperties": { "type": "integer" } },
    "reclustered":{ "type": "boolean" }
  }
}
```

**级联删除**：`features` / `cards` / `evidence` / `chunks` / 对应向量行；
删后**自动重跑聚类**（`discover` 幂等重算）。
`confirm != true` → `invalid_argument`；`analysis_id` 不存在 → `not_found`。**不做撤销**。

---

### T16 · `forget_repo` — 删除一个仓库（破坏性）

**用途**：硬删一个仓库及其上全部分析、克隆目录（G8）。
**annotations**：`{"title":"Forget repo","readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}`
**类别**：`destructive`（**默认禁用**，见 G28）。

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["repo_id", "confirm"],
  "properties": {
    "repo_id": { "type": "string" },
    "confirm": { "const": true }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "deleted", "summary", "reclustered"],
  "properties": {
    "ok":         { "const": true },
    "deleted":    { "const": "repo" },
    "summary":    { "type": "object", "additionalProperties": true,
                    "description": "回显将删除的对象摘要：repo 全名 / analysis 数 / 卡片数 / 证据数" },
    "reclustered":{ "type": "boolean" }
  }
}
```

**级联删除**：其上全部 analysis + `repos` 行 + 本地克隆目录。
`summary` 在删除**前**填好回显，让调用者确认删的不是别的。

---

### T17 · `upload_repo_bundle` — `git bundle` 上传（离线 / 私有仓 / 内网）

**用途**：agent 在**有网络/有凭据的一端**（通常是开发机）`git bundle` 好，经此工具把整个仓送进
server 登记，**server 因此不必出网、也不必持私有仓凭据**（G22，兼解 G4 变体）。
**annotations**：`{"title":"Upload repo bundle","readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}`

> **`openWorldHint:false`**：这个工具**不访问外部世界**——字节从调用方来。
> 类别因此归 `write`（不是 `network`），`MEMEX_TOOLS` 不含 `write` 时不可用。

**入参**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["bundle_path", "repo_url"],
  "properties": {
    "bundle_path": { "type": "string", "description": "调用方给出的本地 bundle 文件路径（stdio 形态）；HTTP 形态走上传体" },
    "repo_url":    { "type": "string", "description": "该 bundle 声称的原始仓地址，用于登记 repo_id 与 full_name" },
    "ref":         { "type": "string", "description": "希望 checkout 的 ref；省略用 bundle 的 HEAD" },
    "subpath":     { "type": "string", "description": "monorepo 子目录（G15）" },
    "sha256":      { "type": "string", "description": "调用方计算的校验和；给了就核对，不符拒绝" }
  }
}
```

**出参 payload**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["ok", "repo", "commit_sha"],
  "properties": {
    "ok":         { "const": true },
    "repo":       { "$ref": "#/$defs/RepoSummary" },
    "commit_sha": { "type": "string" },
    "bytes":      { "type": "integer" },
    "repo_path":  { "type": "string", "description": "server 侧克隆路径" },
    "is_new":     { "type": "boolean" },
    "warnings":   { "type": "array", "items": { "type": "string" } },
    "next_step":  { "$ref": "#/$defs/NextStep" }
  }
}
```

**要点**
- 流程：`git bundle verify` → `git clone <bundle> <repos_dir>/<repo_id>` → 登记 `repos`
  （`source='upload'`）→ 后续与 `fetch_repo` 完全同路（`get_evidence_pack` / `begin_analysis` …）。
- **上限** `MEMEX_MAX_BUNDLE_BYTES`（默认 512MB，`operations.md` §1.3）→ 超限 `invalid_argument`。
- **与 T4 对偶**：T4 `request_repo_bundle` 是 server → agent（下发）；T17 是 agent → server（上传）。
- **`sha256` 给了就不姑息**：不符 `invalid_argument` 且不留半个登记行（先校验后落库）。
- **身份**：登记时仍走 G10 的 `identity_key` 解析；上传件若无 GitHub API 可达，
  `identity_key` 退化为 `host#owner/name` 并记 `warnings`（等价于 fetch 路径的保守分支）。

---

## 3. 注解表（G28 禁用清单的机械来源）

| 工具 | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` | 类别 |
|---|---|---|---|---|---|
| `fetch_repo` | false | false | true | **true** | `network` |
| `get_evidence_pack` | true | false | true | false | `read` |
| `read_file_slice` | true | false | true | false | `read` |
| `request_repo_bundle` | false | false | true | false | `network` |
| `begin_analysis` | false | false | false | false | `write` |
| `validate_report` | **true** | false | **true** | false | `read` |
| `commit_report` | false | false | **true** | false | `write` |
| `search_implementations` | true | false | true | false | `read` |
| `get_card` | true | false | true | false | `read` |
| `list_patterns` | true | false | true | false | `read` |
| `get_report` | true | false | true | false | `read` |
| `list_repos` | true | false | true | false | `read` |
| `recall_stats` | true | false | true | false | `read` |
| `help` | true | false | true | false | `read` |
| `forget_analysis` | false | **true** | true | false | `destructive` |
| `forget_repo` | false | **true** | true | false | `destructive` |
| `upload_repo_bundle` | false | false | true | **false** | `write` |

**用法**：`MEMEX_TOOLS=read,write,network`（默认）即放行前三类；
**`destructive` 类（`forget_analysis` / `forget_repo`）默认关闭**，需显式列入 `MEMEX_TOOLS`（G28）。
被禁工具的调用返回 `disabled`，`details.enabled` 给当前放行集。
**远程形态建议**：不想让 server 主动出网就写 `MEMEX_TOOLS=read,write`。

---

## 4. 两条主流程（`next_step` 的实际样子）

### 4.1 分析一个仓库（agent 驱动）

```
0  （离线/私有仓可选，替代第 1 步）upload_repo_bundle {bundle_path, repo_url}
                          → {repo, commit_sha, repo_path, next_step:{action:"get_evidence_pack"}}
1  fetch_repo            {repo_url}                  → {repo, repo_path?, next_step:{action:"get_evidence_pack"}}
2  get_evidence_pack     {repo_id, depth}           → {tree, entry_points, symbols, next_step:{action:"begin_analysis"}}
3  begin_analysis        {repo_id, depth}           → {session_id, evidence_pack, contract, checklist,
                                                        next_step:{action:"validate_report"}}
   ── agent 读克隆（本地）或 request_repo_bundle（远程），自己分析、写报告 ──
4  validate_report       {report, repo_id}          → {is_valid, problems[]}
   ↺ 若有 problems，agent 修 report 再来（只读幂等，可反复）
5  commit_report         {session_id, report}       → {is_committed, analysis_id, counts, quality}
```

**全程服务端掌握进度**：跳步（直接 `commit_report`）会被会话状态机拒绝
（`state` 必须是 `drafting`/`validated`）。
第 0 步是 G22 的离线/私有仓入口（server 不出网、不持凭据），与第 1 步殊途同归。
远程形态下第 1 步返回的 `repo_path` 不可达 → 改用 `request_repo_bundle`（T4）。

### 4.2 借鉴一个实现（写新功能前）

```
1  search_implementations {query:"带退避与截止时间的重试", limit:5, detail:"normal"}
                           → results[]，每条带 repo / analyzed_sha / is_stale / card_id
2  get_card               {card_id, detail:"full"}   → {card.code: [真实切片], evidence:[…]}
3  （可选）list_patterns   {query:"retry"}            → 跨仓模式与意图探针
```

**这就是整个项目存在的理由**：`search_implementations` → `get_card` 两步，
拿到的不只是「像」，而是**经过证据链校验、且被 ≥2 个独立仓库印证过**的实现。

---

## 5. 与 recall 旧实现的差异（改造清单）

`recall/recall/mcp_server.py` 已有可用的协议层（纯标准库 JSON-RPC over stdio），
**协议层保留**，工具面按本文重写：

| 变化 | recall 旧 | memex |
|---|---|---|
| 出参信封 | 工具各自返回 dict | **统一 `{ok, …}` / `{ok:false, error}`** |
| 错误 | 抛异常转 `isError:true` 文本 | **业务失败走正常返回 + `code`** |
| 列表 | 裸数组 | `{count, items, next_cursor}` |
| 分页 | 无 | 游标式 |
| `detail` 取值 | `compact/semi/full` | `brief/normal/full` |
| 证据引用 | 紧凑字符串 `"a/b.py:12-30"` | 对象 `{path, start_line, end_line}` |
| 元数据 | 无 annotations / outputSchema | **两者必填** |
| 工具数 | 7 | **17** |
| 命名 | `repo` / `limit` 混用 | `repo_id` 统一、`limit` 统一 |

---

## 6. 后续（不阻塞 V1）

- `analyze_repo`（服务端 batch 直跑）→ `decisions.md` G21（排 V5）。
- `outputSchema` 是否被目标客户端强制校验 → 需实测（`gaps.md` G2 的实测项）。

**已定案、不再空缺**：`forget_analysis` / `forget_repo` 签名（本文 T15/T16，G8）；
`depth` 三档阈值（`operations.md` §1.3，G5）；限流维度与数值（`operations.md` §1.3，G7）；
危险工具禁用清单（本文 §3 + `operations.md` §1.1，G28）；`upload_repo_bundle` 签名（本文 T17，G22）；
`resources`/`prompts` 明确不实现（G27）。
