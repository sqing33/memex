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
| `MEMEX_ALLOW_LOCAL_PATHS` | stdio 下 `true`，`serve-http` 下**恒 `false`** | 是否允许 `local:` / 裸目录路径（G6）。**远程形态不接受用环境变量打开**：`serve-http` 入口无条件钉死为 `false`，设了也无效（deployment.md A3/D5） |
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

### 1.6 远程形态（`serve-http`，E15/E16/E18）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMEX_PUBLIC_BASE_URL` | 空 | **远程必填**。对外的绝对基址（如 `https://memex.corp.example`），服务端用它拼 T4 的 bundle 下载票据。**缺省则 `serve-http` 直接拒启**（`raise SystemExit(2)`）——否则票据只能退回 `file://`，远程 agent 拉不到（deployment.md B1）|
| `MEMEX_ALLOWED_ORIGINS` | 空 | 逗号分隔的 Origin 白名单（如 `https://app.corp.example`）。**无 `Origin` 头一律放行**（git/curl/MCP 客户端都不是浏览器）；带了 `Origin` 则必须命中白名单，否则 `403`（A4/B2）|
| `MEMEX_BUNDLE_TTL_SECONDS` | `3600` | T4 bundle 票据有效期（秒）。过期票据 `403`。**换 `MEMEX_TOKEN` 会使全部在途票据立即失效**（HMAC 密钥即 token，有意，见 §9）|

---

### 1.1 并发形态（G26 + A5 连接模型）

`serve-http` 是每请求一线程，**每线程一个 SQLite 连接**（线程局部，`handlers.Runtime.conn`）。
旧实现把一条 `sqlite3.Connection` 放在 `Runtime` 上供所有请求线程共用，实测 8 线程并发调
`list_repos` 即抛 `IndexError: tuple index out of range`：根因不是连接亲和性检查，而是
pysqlite 的**语句缓存**——并发线程复用同一个 `sqlite3_stmt`，游标被别的线程吃掉。
因此连接必须线程局部，`Runtime.close()` 逐个释放；这是硬约束，不是可选优化。

实测口径（本机，`tests/test_serve_http_concurrency.py` 同款：8 线程各 5 次请求，
`threading.Barrier` 同时起跑）：连接改线程局部后 **0 失败**，且 8 个线程确实各持一条连接。
`n=200` 一类的规模数字**不再写进契约**——旧文档的「n=200 全通」没有可复现的测量脚本，
只能作为部署后（`MEMEX_PUBLIC_BASE_URL` + 反代就位）的实测项，不作为承诺。
两条前提仍然成立：

1. SQLite 必须以 serialized 模式编译（`sqlite3.threadsafety >= 3`），
否则 `connect()` 直接报错拒绝服务，不静默随机崩。
2. `request_queue_size = 128`（默认 5 会让 n>=64 起被内核重置连接）。

排障：出现 `SQLite objects created in a thread` 或
`IndexError: tuple index out of range` 说明连接模型不对（应为每线程一条）；
出现 `ConnectionResetError` 说明 accept backlog 不够。


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
| `memex export-site [--out DIR] [--build]` | 导出站点数据并出静态页（`§5.4`）。`--out` 缺省 `$MEMEX_HOME/site`。**Python 只负责把库导成 `site-data.json`**，排版由 `web/` 的 React 预渲染做（`web/dist` 是 vite 编译中间目录，不在 Python 包内，Python 依赖仍为空）。加 `--build` 才顺带跑 `npm run build`（需先 `cd web && npm ci`）；构建失败**只报不抛**，命令仍返回 0 并在 `site.build_error` 里带原因——站点是展示层，**不得因为它拖垮已提交的分析** |
| `memex stats` | 知识库总览（含按 producer 分组的质量，G21） |
| `memex forget-repo <repo_id> --yes` | CLI 版删除（G8）；MCP 侧是 `forget_repo` |
| `memex import-vibecraft <path> [--dry-run]` | 从 VibeCraft 回填（G12）；**只读** VibeCraft 库；强制过 `validate_report` + 从真实文件重切证据；过不了的卡片**丢弃并计数**；**回填时同一次调用就把索引建好**（`indexed_analyses` 计数），不必再手动 `reindex`；`--dry-run` 只出报告不落库 |

---

## 4. 启动检查清单（`memex serve-*` 启动时）

按顺序执行，任一失败给出**可操作**的提示而不是栈：

1. `MEMEX_HOME` 可读写；不存在则提示 `memex init`。
2. `meta.schema_version` 与本版常量比对（G9）：
   - 更高 → 拒绝启动，提示升级 memex；
   - 更低 → **查迁移注册表 `"<库版本>-><代码版本>"` 并逐级自动执行**；
     缺任一级迁移函数才拒绝启动并提示 `memex migrate`（G9 承诺，本版起真兑现）；
   - 版本号对得上 ≠ 列齐全 → 再跑一次 `assert_expected_columns()`：
     缺列 → `conflict` 并列出 `missing_columns`，提示 `memex migrate`（G9 补记）。
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

`repos.language` 由**克隆目录的文件后缀统计**得出（见 tech-design §4.6.1），不是联网查的。所以它为 `NULL` 只有两种可能：这个仓确实没有可识别的源文件，**或者这一行是写侧落地之前入库的**——后者更常见，别一上来就怀疑前者。

| 现象 | 排查 |
|---|---|
| `list_repos` 的 `language` 是 `NULL` | 见下方三个成因——**先按成因 1 查，别一上来就怀疑源文件缺失** |
| `stars` / `license` / `description` 是 `NULL` | 同上，多半是**这行早于元数据写侧落地入库**；只有 `github.com` 会被填（`hostmeta.SUPPORTED_META_HOSTS`），其他宿主一律留空 |

**三个成因（按现实发生频率排序）**：

1. **该仓是 P2-1 写侧落地之前入库的**（最常见）。`repos.language` 的写侧是后补的，
   在那之前 fetch 进来的行这一列天然是 `NULL`，跟仓库内容毫无关系。
   判据：`git log -1 --format=%ad --date=short -- src/memex/fetch/detect.py` 看写侧落地日期，
   早于该仓 `analyses.created_at` 的就中招。
   **修法：对同一个仓 `fetch_repo(refresh=true)` 一次**——注意**必须带 `refresh`**，见下面那条坑。
   （只补列、不动分析：`_upsert_repo` 带 `language` 重跑一次即可，
   实测只有 `language` 与 `cloned_at` 两列变化，`analyses` / `cards` 计数不动。）
2. 该仓源文件全部落在跳过目录（`vendor` / `node_modules` / `target` …）里，或仓库是空仓 / 纯文档仓。
3. （罕见）仓库改名或转移后 `repo_id` 变了，新行还没 fetch 过。

**成因 1 与 2 的症状完全一样**（都是 `NULL`），光看库是分不出来的，
只能直接跑一次 `detect_language` 看它认不认得出来：认得出来就说明是成因 1。

> **坑（实测踩过）**：不带 `refresh` 重新 `fetch_repo` **补不上这一列**。
> 幂等命中走的是 `fetch/repo.py` 的缓存分支——见到 `head_sha` 已存且克隆目录还在就直接 `return`，
> **根本不进 `_upsert_repo`**，所以 upsert 里那条 `language=excluded.language` 压根没机会执行。
> 实测：重复 `fetch_repo("https://github.com/sqing33/PTNexus")` 后该行 `language` 仍是 `NULL`，
> 且整行**零列变化**（`analyses` / `cards` 计数也不变）。带 `refresh=true` 才会重克隆并走 upsert。
> 这个分支本身是合理的幂等设计（不该为了补一列就去触网重克隆），
> 但它意味着「重 fetch 就能修」这句话对老库是错的——文档不写清楚就等于骗人。

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

### 5.4 站点构建：数据归 Python，排版归 Node

```bash
memex export-site                  # 只导 site-data.json（纯 Python，零依赖）
memex export-site --build          # 导完顺带跑 npm run build，出 *.html
memex export-site --out /tmp/site  # 指定产物目录（默认 $MEMEX_HOME/site）
```

`export-site` 拆成两步是有意的：`site-data.json`（`schema_id: memex/site/1`）是 Python 与
React 之间**唯一**的接口，也是 CI 里可断言的契约。**构建失败不抛异常**——
命令仍返回 0，把原因放在返回值的 `site.build_error` 里。理由很直白：站点是展示层，
一个 CSS 编译错不该把已经校验通过、`code_mismatch=0` 的分析变成失败（G3 的零假成功是
对分析而言，不是对网页而言）。

`commit_report` 落库后会顺带重新 dump 一次（≈50ms，不碰模型），所以**数据**永远是新的；
但 HTML 是快照，**不会自己重排**——要么在部署流水线里跑 `--build`，要么手敲一次。

`.gitignore` 里有两条必须知道的坑：仓库根的 `build/` 与 `dist/` 是通配到所有子目录的，
会把 `web/build/`（预渲染脚本，**是源码**）和 `web/dist/`（vite 编译中间目录）一起吞掉，
所以文件末尾用 `!web/build/` 显式取反。

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
| 启动报 `conflict` + `missing_columns` | 库比代码旧，且没有对应的迁移函数（G9 补记） | `memex migrate`；先 `memex migrate --dry-run` 看它会补哪几列，再执行 |
| 启动报 `conflict` + `missing_columns`，但 `--dry-run` 显示 `steps: []` | 库里 `schema_version` 已是最新版、列却缺（版本号是人手工维护的） | 版本号是常量、列是代码事实，**以代码为准**：更新到含该列的 memex 再 `memex migrate --dry-run` 确认 |
| 检索报 `sqlite3.OperationalError: table chunks has no column named ...` | 既有库没跑过迁移 | `memex migrate`（新版本会把这个裸异常兜成可读的 `conflict`，G9 补记） |
| 启动报 schema 版本不符 | 库比代码新 | 拒绝启动是正确的；升级 memex 或从备份恢复（G9） |
| 证据包 `truncated: true` | 超 `depth` 上限 | 换更大的 `depth`，或接受截断（已显式标注，G5） |
| `export-site` 报 `node: not found` 或 `npm run build` 失败 | 站点构建要 Node，但 Python 侧不依赖它 | 不影响 MCP 与分析。先 `cd web && npm ci`；只想拿数据不想出页就**不加** `--build`，直接读 `site-data.json`（`schema_id: memex/site/1`） |
| CI（`validate-main`）里前端产物用例整组 skip | CI 镜像不装 Node（这是有意的，见 `deployment.md` §11） | 属预期，不是回归。本地装了 Node 时它们照常跑（185 例全绿）；守卫用 `shutil.which("node")`，别改回跑 `node --version` 看返回码 |
| 站点页缺了刚分析完的仓 | 站点是**快照**，不是常驻服务 | 重跑 `memex export-site --build` 后刷新。commit 已自动重新 dump，所以数据是新的，只是没重新排版 |
