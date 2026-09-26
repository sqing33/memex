# 运维与配置（operations）

> 承接 [`decisions.md`](decisions.md) **G 组第 2 批**（G4–G9 / G11 / G15–G17 / G20 / G21 / G28）
> 与**第 3 批**（G10 / G12 / G14 / G18 / G22 / G27）。
> 那些决定定的是**语义**，本文落**可操作的配置项、命令与数字**。
> 相关：部署形态见 [`tech-design.md`](tech-design.md) 第四章；工具签名见 [`mcp-tools.md`](mcp-tools.md)。

---

## 1. 环境变量总表

### 1.1 基础

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_HOME` | `~/.memex`（远程：`/var/lib/memex`） | 数据库、克隆缓存、站点产物的根目录 |
| `MEMEX_TOOLS` | `read,write,network` | 工具类别放行集（G28）；`destructive` 从不默认开 |
| `MEMEX_LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |

### 1.2 抓取与宿主（G4 / G6）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_GIT_HOSTS` | `github.com,gitlab.com,gitee.com` | 宿主白名单；不在其中 → `unsupported` |
| `MEMEX_GIT_TOKEN__<host>` | 空 | 私有仓凭据，host 里的 `.` 换成 `_`。例：`MEMEX_GIT_TOKEN__github_com=ghp_…` |
| `MEMEX_HOST_META` | `on` | 抓取时是否打宿主元数据 API（取 `identity_key` / `fork_of` / `stars` / `license` / `description`）。**开 = 身份可校验**（G10 写侧）；关 = 只按 `owner/name` 判身份并记 warning，fork 字段留空（G6 零假成功：不猜）|
| `MEMEX_GIT_MIRROR` | 空 | 镜像前缀，如 `https://git-mirror.corp/`；抓取时把 `github.com/<o>/<n>` 重写为 `<mirror><o>/<n>`（G22）。**探测** `git ls-remote <mirror> HEAD`：失败则记 `warnings` 并按原 host **回落直连**。凭据仍按**原始 host** 取（`MEMEX_GIT_TOKEN__github_com`），不按镜像 host |
| `MEMEX_ALLOW_LOCAL_PATHS` | stdio 下 `true`，`serve-http` 下强制 `false` | 是否允许 `local:` / 裸目录路径（G6） |
| `MEMEX_CLONE_TIMEOUT` | `300` | 秒（G17） |
| `MEMEX_CLONE_CONCURRENCY` | `3` | 全局并发 clone 数（G7） |

**凭据用法**（关键：**不落盘**）：

```bash
# 正确：token 只在本次 git 调用的 header 里
git -c http.extraheader="Authorization: Bearer $TOKEN" clone --depth 1 https://github.com/me/private.git

# 错误：会写进 .git/config 或 remote URL，等于把 token 存到磁盘上
git clone https://$TOKEN@github.com/me/private.git
```

`~/.memex/credentials.json`（0600）作为环境变量的替代：

```json
{ "git_tokens": { "github.com": "ghp_…", "gitlab.com": "glpat-…" } }
```

### 1.3 护栏（G5 / G7 / G16）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_MAX_FILE_BYTES` | `1048576`（1 MiB） | 单文件上限；超过则证据包跳过、`read_file_slice` 拒绝 |
| `MEMEX_MAX_REPO_BYTES` | `2147483648`（2 GiB） | 仓库总字节上限 |
| `MEMEX_MAX_BUNDLE_BYTES` | `536870912`（512 MiB） | `git bundle` 上限；**同时管 T4 下发与 T17 上传**（G22） |
| `MEMEX_HTTP_QPS_PER_TOKEN` | `30` | 每 token 的请求 QPS |
| `MEMEX_EMBED_CONCURRENCY` | `1` | 并发 embedding 批次数 |
| `MEMEX_PATTERN_CHUNK_MAX_UNITS` | `60` | pattern chunk 文本上限（信息单元，G14） |

**`depth` 三档**（G5，不是环境变量而是工具入参，此处列出便于对照）：

| `depth` | `max_files` | `max_readme`（字符） | `min_features` | 符号上限 |
|---|---|---|---|---|
| `fast` | 400 | 3 000 | 3 | 500 |
| `standard`（默认） | 2 000 | 6 000 | 3 | 2 000 |
| `deep` | 4 000 | 12 000 | 5 | 8 000 |

### 1.4 嵌入（G11）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_EMBEDDER` | `sentence-transformers` 多语种模型名 | 可设 `hash:512`（仅冒烟）/ `http:<url>` |
| `MEMEX_RERANK` | `off` | 可设 cross-encoder 模型名，或 on 用默认模型。**默认 off 是实测结论不是省事**：ms-marco-MiniLM-L-6-v2 让 top-1 掉 6 个，BAAI/bge-reranker-base 抬 3 个（G25）|
| `MEMEX_RERANK_LOAD_TIMEOUT` | `120` | cross-encoder 联网加载的墙钟上限（秒）。
  独立于嵌入器的 20s：`BAAI/bge-reranker-base` 约 1.1GB，用 20s 必然超时（G25）|

> **混模型库会被拒绝**：写入与检索两端都断言 `chunk_vectors.embedder == meta.embedder`，
> 不一致直接报 `conflict` 并提示跑 `memex reindex --embedder X`（G11）。

### 1.5 会话（G18 / G20）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_SESSION_TTL_SECONDS` | `7200` | 会话 TTL；超时 → `abandoned`（值待 V1 实测，G18） |
| `MEMEX_SESSION_RETENTION_SECONDS` | `604800`（7 天） | `abandoned` 会话的**留痕保留期**；过期后物理删行，但 `session_stats` **不删**（G18） |

> **回收三步（顺序不可换）**：① 归档统计进 `session_stats`（turn / 工具调用数 / token 估算 / 耗时）
> → ② 置 `state='abandoned'` + `abandoned_at`，**行保留** → ③ 过保留期物理删行。
> 第 ①　步是 V1 判「agent 驱动可不可行」的唯一数据来源，**任何情况下先落统计再回收**。
> 清扫时机：**每次工具调用时顺带执行**（本地 stdio 与远程 `serve-http` 同一口径）；
> `serve-http` 另加**低频后台线程（每 60s）**兜底
> （`tech-design.md` §2.6 的「若实测积压」）。
> 远程常驻时纯靠「下一次调用」不够——没人调用就永远不清扫，
> 故后台线程是必需的，不是可选优化。

---

### 1.1 并发形态（G26 实测）

`serve-http` 是每请求一线程、共用一个进程与一个 SQLite 连接。
修复合享连接的线程亲和性 + 放大 accept backlog 后，实测 **n=200 并发客户端全通**
（单发基线正常，n=8/16/32/64/128/200 均 200/200）。两条都是前提：

1. SQLite 必须以 serialized 模式编译（`sqlite3.threadsafety >= 3`），
否则 `connect()` 直接报错拒绝服务，不静默随机崩。
2. `request_queue_size = 128`（默认 5 会让 n>=64 起被内核重置连接）。

排障时先看 `details.reason`：出现 `SQLite objects created in a thread
说明是连接亲和性，出现 `ConnectionResetError` 说明是 backlog 不够。


## 2. 忽略规则（G16）

分析时跳过的路径 = **固定黑名单 ∪ `.gitignore`**（尊重但不信：agent 仍可用
`read_file_slice` 显式取被忽略的文件）。

```
固定黑名单：
  .git/  .hg/  .svn/
  node_modules/  vendor/  third_party/  deps/
  dist/  build/  target/  out/  .next/  .nuxt/
  __pycache__/  .venv/  venv/  .tox/  .mypy_cache/
  .idea/  .vscode/  coverage/  .cache/
  二进制扩展名：.png .jpg .jpeg .gif .webp .pdf .zip .tar .gz
                .so .dylib .dll .exe .wasm .class .pyc .o .a
  压缩/锁文件：*.min.js  *.min.css  *.lock  package-lock.json  poetry.lock  yarn.lock
```

---

## 3. 子命令

| 命令 | 作用 |
|---|---|
| `memex init` | 建库、写 `meta`、初始化目录 |
| `memex serve-mcp` | 本地 stdio 形态 |
| `memex serve-http --host 127.0.0.1 --port 8931` | 远程 Streamable HTTP 形态 |
| `memex reindex [--embedder X] [--repo REPO_ID ...]` | 重算 chunk 向量并更新 `meta`（换模型后用，G11）；`--repo` 可重复，只重建指定仓（`§5.3`）；全量重建才清空三张派生表 |
| `memex migrate [--to X] [--dry-run]` | 真源层迁移（G9）；`--dry-run` 只打印计划 |
| `memex export-site [--out DIR]` | 静态目录页 |
| `memex stats` | 知识库总览（含按 producer 分组的质量，G21） |
| `memex forget-repo <repo_id> --yes` | CLI 版删除（G8）；MCP 侧是 `forget_repo` |
| `memex import-vibecraft <path> [--dry-run]` | 从 VibeCraft 回填（G12）；**只读** VibeCraft 库；强制过 `validate_report` + 从真实文件重切证据；过不了的卡片**丢弃并计数**；**回填时同一次调用就把索引建好**（`indexed_analyses` 计数），不必再手动 `reindex`；`--dry-run` 只出报告不落库 |

---

## 4. 启动检查清单（`memex serve-*` 启动时）

按顺序执行，任一失败给出**可操作**的提示而不是栈：

1. `MEMEX_HOME` 可读写；不存在则提示 `memex init`。
2. `meta.schema_version` 与本版常量比对（G9）：
   - 更高 → 拒绝启动，提示升级 memex；
   - 更低 → 有迁移函数则跑，否则提示 `memex migrate`。
3. `meta.embedder` / `meta.dim` 存在；`chunk_vectors` 中无异构 `embedder` 行
   （若有 → 警告并提示 `memex reindex`，**不阻止启动**，但检索会被拒）。
4. 嵌入模型：启动检查**不同步加载**真模型——同步加载会阻塞 MCP `initialize` 响应，
   且离线环境下的联网回源可能挂几分钟（客户端 60s 超时即断连）。检查阶段只做**快速校验**：
   - `spec` 为 ST 裸名 / `sentence-transformers:<名>` 且**本地 HF 缓存缺失** → 打警告
     （提示预置模型文件），**不阻止启动**；
   - 真模型改为**后台线程预热**（进程起来后 load 进内存），`initialize` 立即返回。
   预热或首次用到嵌入器时加载失败 → **不静默降级**，在**首次需要嵌入的工具调用**上
   显式返回错误（`internal`），并给三条出路：预置模型文件 / `MEMEX_EMBEDDER=http:<url>` /
   **显式** `MEMEX_EMBEDDER=hash:512`。联网加载设**墙钟上限**（默认 20s，
   `MEMEX_EMBEDDER_LOAD_TIMEOUT` 可调），超时即报错而非无限挂起；**失败结果被缓存**，
   后续调用立即报同一错误（不重复等待）。
   cross-encoder（rerank）走**同一套缓存与失败缓存**逻辑，但墙钟上限独立：
   `MEMEX_RERANK_LOAD_TIMEOUT`，默认 **120s**——`bge-reranker-base` 约 1.1GB，
   沿用嵌入器的 20s 会**必然超时**（实测首次联网加载就撞了 20s）。重排失败同样**显式报错**，
   不静默退回 RRF 假装重排成功。
   **只有显式写 `hash:512` 才降级**，且启动横幅与 `recall_stats` 必须带
   `degraded:true, embedder:"hash"`——**默认路径绝不静默退 hash**（D1：hash 无语义 = 跨语言卖点消失）。
5. `MEMEX_TOOLS` 解析成功；打印放行集。
6. 远程形态：`MEMEX_TOKEN` 非空，否则**拒绝启动**（E16）。

---

## 5. 危险操作的确认（G8）

- MCP 侧：`forget_analysis` / `forget_repo` 要求 `confirm: true`；缺失 → `invalid_argument`。
- CLI 侧：同上，用 `--yes`。
- 两者都**先在 stdout / 返回值里回显将删除的对象摘要**（repo 全名、analysis 数、卡片数），
  让调用者/用户确认删的不是别的。

---

### 5.1 reindex 的覆盖范围（别只盯 committed）

`reindex` 会先清空 `chunks` / `chunk_vectors` / `chunk_fts` 三张派生表，再逐条重建——所以它的筛选条件就是**真源层会不会被抹掉**的分界线。

两条落库路径**统一写 `committed`**（来源由 `producer` 字段区分：agent 路径 `agent`、VibeCraft 回填 `batch`）。`reindex` 仍按 `committed` 与 `ready` 两个值筛选——`ready` 是历史遗留，老库里可能还有。

| 现象 | 根因 | 处置 |
|---|---|---|
| 跑完 `reindex` 后回填仓检索不到任何东西，且 `memex stats` 的 `chunks` 归零 | 筛选漏了 `ready` | 修好筛选后重跑；真源层（`analyses/features/cards/evidence`）还在，可完整重建 |

排查任何 `reindex` 后的计数异常，先查 `SELECT status, COUNT(*) FROM analyses GROUP BY status`——看它有没有覆盖你库里的全部状态值。

### 5.2 站点 / list_repos 上语言是空的

`repos.language` 由**克隆目录的文件后缀统计**得出（见 tech-design §4.6.1），所以它不会为空，除非那个仓库确实没有可识别的源文件（空仓、纯文档仓、深度跳过 vendored 目录）。

| 现象 | 排查 |
|---|---|
| `list_repos` 的 `language` 是 `NULL` | 该仓源文件全部落在跳过目录里，或仓库是空仓 |
| `stars` / `license` / `description` 是 `NULL` | **不是 bug**：V1 不打元数据 API，这三项一律如实留空 |

手工核对某个仓的真实主语言：

``bash
MEMEX_HOME=... .venv/bin/python -c "
import sys; sys.path.insert(0,'src');
from memex.fetch.detect import detect_language;
print(detect_language('/root/.memex/repos/github.com__tokio-rs__axum'))
"
``

### 5.3 reindex 分仓与增量（P1-4）

`reindex` 此前只有一个模式：**清空三张派生表全表重建**。这在「换嵌入模型」时是对的
（全库向量都要重算，分仓没有意义），但换模型之外的两类场景被它误伤了：

| 场景 | 全量 reindex 的问题 |
|---|---|
| 某仓的分析刚落库，向量算错了要单独重算 | 把全库 O(全部) 重算一遍 |
| 只想确认一个仓的索引是否健康 | 同上，且**期间该仓完全不可检索**（派生表先被清空） |

**决定：加 `--repo <repo_id>`（可重复），分仓重建时**只清空该仓的块**。**

两条实现约束，都是被「派生表可重建」这条分层逼出来的：

1. **分仓不能靠 `DELETE FROM chunks WHERE repo_id = ?` 就完事**——`chunks.repo_id` 由
   `set_chunk_repo` 事后回填，可能为 NULL（`index_analysis` 写块时不带 repo_id）。
   所以分仓重建要先按 `ref_id -> analyses.repo_id` 反查，**把所有可能属于该仓的块**捞出来删，
   宁可多删同仓的（该仓自己的）也不漏。
2. **pattern 块是跨仓的**（`kind='pattern'`，`ref_id=pattern_id`）。某仓换模型时
   pattern 向量必须一并重算，否则新算的 card 向量和旧 pattern 向量不同源，
   相似度失真。故分仓 reindex 的动作是：**清该仓的 feature/card/report_section 块 +
   重建该仓分析 + 重跑 recluster**（recluster 只读现存向量，重算自己的 pattern 块）。

3. **只按 ref 反查还不够：孤儿块要靠 `chunks.repo_id` 再扫一遍。**
   ref 已不存在的块（卡从报告里删了、导入中断）既不在 `features` 表里，也不在 `cards` 表里，
   任何 ref 反查都枚举不到，它会带着旧向量**永远留在检索集里**——
   症状是「召回一条早已不存在的卡片」，同样零报错。所以两条路子是互补的，缺一条就漏。
   删掉几条按哪条路枚举的会报在返回值的 `dropped` 里（`[(路数, 条数)]`），
   全量重建走的是清空三表、不经过这一步，故 `dropped` 为空。

**不加的东西**（说清楚免得下一个人又加一遍）：
「按 `--since <时间>` 只重建最近变更」这类**基于时间的增量是错的**——
索引层的失效原因不止「分析变了」：换模型、`quality_json` 被补、卡被改、聚类阈值调整，
都与时间无关。按时间筛会漏掉它们，而漏掉的症状是**静默的坏检索**。
真正正确的增量判据是**块的身份**（哪些分析有块、块该长什么样），
那需要给块加内容指纹，属于另一件事，不在这里假装已有。

**`--repo` 与换模型的互斥**：同时给 `--embedder X` 和 `--repo` **允许**（换模型只重算一仓，
其余仓的向量留在库里但 embedder 标识不一致）——所以**分仓重建必须在收尾时报出**
`embedder_mismatch_repos`（其余 embedder 与 meta 不一致的仓），否则撞上 G11 的
「混模型库直接报 conflict」，用户得自己猜是哪几个仓。

## 6. 常见故障与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `unsupported`：宿主不在白名单 | 仓源不在 `MEMEX_GIT_HOSTS` | 加白名单或换源（G6） |
| `fetch_failed`：403 | 私有仓且无凭据 | 配 `MEMEX_GIT_TOKEN__<host>`（G4） |
| `fetch_failed`：404 且 `retryable:false` | 仓不存在/无权限 | 核对 URL；若确为私有仓，看是否有 token（G4） |
| `fetch_failed`：clone/tarball 全断，但网络正常 | 内网/镜像环境（G22） | 设 `MEMEX_GIT_MIRROR`；或开发机 `git bundle` 后用 `upload_repo_bundle`（T17）投喂 |
| 启动报「嵌入模型不可用」 | 预置模型缺失/下载失败（G22） | 预置模型，或 `MEMEX_EMBEDDER=http:<url>`；**别**用 `hash` 当长期形态 |
| 回填报 `cards_imported: 0` | VibeCraft 证据链与本地文件对不上（G12） | 确认目标仓文件在本地（先 `fetch_repo` 或指到旧克隆）；`--dry-run` 看 `drop_reasons` |
| 检索报 `conflict`（混模型） | `chunk_vectors` 有异构 embedder | `memex reindex --embedder <当前>`（G11） |
| `rate_limited` | 触到 QPS / clone / embed 闸 | 按 `details.retry_after_seconds` 等待（G7） |
| `stale_repo` | 会话期间仓被更新 | 重开会话或让 agent 基于新版本重切（G20） |
| 启动报 schema 版本不符 | 库比代码新 | 拒绝启动是正确的；升级 memex 或从备份恢复（G9） |
| 证据包 `truncated: true` | 超 `depth` 上限 | 换更大的 `depth`，或接受截断（已显式标注，G5） |
