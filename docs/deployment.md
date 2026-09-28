# 最终形态部署：Docker + 远程 MCP（deployment）

> 本文是**终态规格**，不是迁移计划。描述的是「要做成什么样」与「为达到它必须改什么」，
> 不写"先……再……"的过渡版本。
> 上游契约：[`tech-design.md`](tech-design.md) 第四章（部署形态）、
> [`decisions.md`](decisions.md) **E15–E18**、
> [`operations.md`](operations.md)（环境变量与运维）、
> [`mcp-tools.md`](mcp-tools.md)（工具签名）。
> 与第四章的关系：第四章是**架构决定**（为什么这么选），本文是**可执行规格**
> （具体哪几个端点、哪几个配置项、哪几处代码要改、怎么验收）。

---

## 1. 目标形态一句话

memex 以**单容器**跑在服务器上（`memex serve-http`），本地 coding agent 通过
**Streamable HTTP + 静态 Bearer Token** 连 `https://<host>/mcp`；
代码本体的取回走 **E18 的 `git bundle` 下发**（agent 拉回本地、用自己的
`Read`/`Grep` 读，零 MCP 文件往返），分析结果经 `commit_report` 回传落库。

容器只承载**机械活 + 知识库**，不承载 agent 本身——这是全项目的第一原则，
部署形态不能把它改掉。

## 2. 拓扑

```
┌─ 本地开发机 ─────────────────┐        ┌─ 服务器 ───────────────────────────┐
│                              │        │                                     │
│  coding agent                │  HTTPS │  反代链路（已有的，TLS 终结在此）    │
│   ├─ MCP 客户端 ─────────────┼───────>│   └─> 宿主 :8931                      │
│   │    POST /mcp             │        │        memex 容器（普通 docker run）  │
│   │    Authorization: Bearer │        │         ├─ ThreadingHTTPServer      │
│   │                           │        │         │   ├─ POST /mcp           │
│   │                           │<───────┤         │   ├─ GET  /bundles/{id}    │
│   │                           │ JSON   │         │   └─ GET  /healthz         │
│   └─ git clone <bundle_url> ─┼───────>│         │                          │
│        （一次性，之后本地读） │  HTTPS │         │  $MEMEX_HOME              │
└──────────────────────────────┘        │         │   ├─ memex.db  （真源）    │
                                        │         │   ├─ index.db  （派生）    │
                                        │         │   ├─ repos/    （克隆）    │
                                        │         │   └─ bundles/  （待下发）  │
                                        └─────────────────────────────────────┘
```

| 组件 | 归属 | 说明 |
|---|---|---|
| 容器 | 服务器 | `memex serve-http --host 0.0.0.0 --port 8931`；**容器内必须绑 `0.0.0.0`** |
| 端口发布 | 宿主机 | `8931:8931`——**正常映射**，容器服务直接对外 |
| 反代 | 宿主机 | **用你已有的反代链路**，memex 侧只管在 8931 提供明文 HTTP（§4.4） |
| 卷 | 宿主机 | 两个目录：**数据**+**模型缓存**；容器内 `/data/memex`（=E17 的远程 `MEMEX_HOME`）与 `/data/models` |
| 嵌入模型 | 卷内 | **不烘焙**：首启按需下载进卷内 HF 缓存，之后不再联网（§5.2） |
| agent | 本地 | 永远不在服务器上 |

## 3. 端到端链路（一次分析的完整时序）

| # | 谁 | 动作 | 落点 |
|---|---|---|---|
| 1 | agent | `tools/call fetch_repo {repo_url}` | 服务器 clone → `$MEMEX_HOME/repos/` |
| 2 | agent | `tools/call request_repo_bundle {repo_id}` | 服务器 `git bundle create` → 返回带签名 URL |
| 3 | agent | `git clone <url> <本地目录>` | 一次性传输；之后**全部本地读** |
| 4 | agent | `tools/call get_evidence_pack {repo_id, depth}` | 文件树 + README + 符号清单，控制它要读的文件数 |
| 5 | agent | 本地 `Read`/`Grep` 读代码、切片 | 零 MCP 往返 |
| 6 | agent | `tools/call begin_analysis {repo_id}` | 开业务会话（**与 `Mcp-Session-Id` 不是一回事**） |
| 7 | agent | `tools/call recall {mechanism_desc}` | 三路召回 + RRF |
| 8 | agent | 写 `report.json`，`tools/call commit_report` | 服务端重切校验 → `code_mismatch=0` → 落库 |
| 9 | agent | 下一轮复用同一 `session_id` | 会话 TTL 7200s |

**降级路径**（服务器出网受限或 bundle 太大时）：退回 `read_file_slice` 批量读。
`get_evidence_pack` 必须把符号清单给足，把「需要读的文件数」压下来——
否则退化成"每次读文件一次网络往返"，正是本方案要避免的形态（`tech-design.md` §4.2）。

## 4. HTTP 面

三个端点。**`/mcp` 已存在，另两个要新建。**

### 4.1 `POST /mcp`（已实现，需按 §6 收口）

Streamable HTTP，**只回 `application/json`**，不实现 SSE（E15）。
现状：`mcp/server.py:267-298` 已经是这个形态，POST 校验 `Content-Type`、
回 `Mcp-Session-Id`。要改的是认证、Origin、限流、session 校验（§6）。

### 4.2 `GET /bundles/{repo_id}`（**新建，终态的关键路径**）

这是让远程形态**真正可用**的端点。现状是断的：

- `fetch/bundle.py:53` 返回 `"url": "file://" + str(bundle_path)`，
  同文件 docstring（:3-4）自陈「**V1 为本地形态**」；
- `mcp/server.py:258` 的 `do_GET` 只放行 `/mcp`，**没有任何 bundle 下载端点**；
- 而 `mcp-tools.md:465` 把它描述为「一次性下载地址（带短时签名）」。

即：**文档写的是终态，代码是本地专用形态，远程 agent 拿到 `file://` 什么也做不了。**

**终态契约**：

| 项 | 规定 |
|---|---|
| 路径 | `GET {MEMEX_PUBLIC_BASE_URL}/bundles/{repo_id}?commit={sha}&exp={epoch}&sig={hex}` |
| 签名 | `HMAC-SHA256(MEMEX_TOKEN, f"{repo_id}\n{commit_sha}\n{exp}")`，取 hex 前 32 字符 |
| 校验 | `hmac.compare_digest` 常数时间比较 + `exp > now` + `commit` 与 `repo_id` 绑定 + 文件存在 |
| 鉴权头 | **不要求 Bearer**——agent 的 `git clone`/`curl` 不便带 header，**票据即凭证** |
| 响应 | `200` + `application/octet-stream` + `Content-Length` + `Content-Disposition: attachment` |
| 失败 | `403`（签名错/过期）、`404`（仓或 bundle 不存在）；**不回 `401`**，避免提示 token 探测面 |
| 不做的事 | **下载后不删文件**。见下方说明 |

**「一次性」的确切含义**（要改写 `mcp-tools.md:465` 的措辞）：
**短时（默认 1h）+ 绑定到具体 `commit_sha` 的只读票据**。
**不物理删除**——删了就破坏「网络中断后重试」，而 bundle 本来就能按 ref 重新生成。
清理靠 `$MEMEX_HOME/bundles/` 的 **LRU：保留最近 20 个，总量超
`MEMEX_MAX_BUNDLE_BYTES` 就删最老**。

**密钥不新增**：HMAC 密钥就是 `MEMEX_TOKEN`。**换 token = 轮换全部在途票据**，
运维面只有一个 secret。

### 4.3 `GET /healthz`（**新建**）

容器 healthcheck 与外部探活都要用。**不要 Bearer**（探活不该持有凭证），
因此返回体**不得泄露路径、库名、token**。

```json
{ "ok": true, "version": "0.0.1", "uptime_s": 3600,
  "db_ok": true, "embedder_ready": true, "embedder_error": null }
```

`ok=false` 当且仅当 `db_ok=false`。`embedder_ready=false` **不**让 `ok` 变 false
——预热未完成是正常状态（§5.2）：**首启正在下载模型**时就是这一态，此时 `/healthz`
返回 200 但 `embedder_ready=false`，真正的嵌入失败由**首个需要向量的工具调用显式报错**，
不静默降级（D1/G22）。

### 4.4 反代：接入已有的反代链路

**服务器上已有完整的反代链路，memex 不再自带 Caddy/Nginx 配置**——
容器按普通 `docker run` 跑，端口正常映射，反代那一侧怎么配是既成事实。

memex 侧对反代的**唯一要求**（三条）：

| 要求 | 原因 |
|---|---|
| `/mcp`、`/bundles/*`、`/healthz` **三个路径都要被代理到 8931** | `/bundles` 是 bundle 下发的下载端点，漏了它 T4 返回的 URL 取不到东西 |
| **不要在反代层改写 `Accept` 头** | MCP 规范要求客户端 `POST /mcp` 的 `Accept` 同时列 `application/json` 与 `text/event-stream`；memex 只回 JSON 分支（E15），改写会破坏协商 |
| **不要对 `/mcp` 做请求体大小限制** | 证据包与报告都在这一个端点上走；反代默认的 `client_max_body_size` 之类要放到足够大 |

容器内是明文 HTTP，这是**可以接受的**——TLS 由反代那一侧终结，
容器只在服务器本机的 docker 网络里被访问。**但前提是 §6 的认证与限流必须实装**：
端口既然是正常映射到全网的，`/mcp` 就是任何人都能扫到的端点。

## 5. 容器形态

### 5.1 Dockerfile

| 层 | 内容 | 理由 |
|---|---|---|
| base | `python:3.12-slim` + `git` | `git` binary 是硬依赖：T1 clone / T4 bundle / T17 verify 都要 |
| deps | `pip install .[default]`（**先装 CPU-only torch**，见下） | 嵌入模型必需；`analysis` extra **不进生产镜像**；CPU wheel 免拖整套 CUDA |
| src | `pip install --no-deps .` | 装 `memex` 命令（`pyproject.toml:23-24`） |
| user | 非 root `memex`（uid 1000） | 服务要 clone 任意仓、能读整个 home，有 root 就等于全盘可读 |
| entry | `exec memex serve-http --host 0.0.0.0 --port 8931` | `exec` 让 PID 1 是 memex，信号直达 |

**不引入 uvicorn/starlette**。`pyproject.toml:20` 声明了 `http` extra 但
`mcp/server.py` 零 import、恒用标准库 `ThreadingHTTPServer`——多出的两个端点
标准库完全够。**把 `http` extra 从 `pyproject.toml` 删掉**，别留一个永远不被
使用的依赖层。

> **`exec` + `0.0.0.0`**：现状 `cli.py:34` 的 `--host` 默认 `127.0.0.1`，
> **在容器里绑回环等于外部访问不到**。这是 Docker 化的必改点。

### 5.2 嵌入模型：首启自动下载，不进镜像

**镜像里没有模型。** 模型（`paraphrase-multilingual-MiniLM-L12-v2`，约 470MB）由
容器**首次启动时**由 `memex` 自己下载到卷内 HF 缓存
（`HF_HOME=/data/models`），之后一直命中缓存、不再联网。

为什么不烘焙：
- **镜像瘦身**：烘焙会让镜像多出 ~470MB 模型层，且它和代码更新节奏无关，纯拖累 `docker pull`；
- **换模型=改 env**：卷内缓存让「换嵌入器」只需改 `MEMEX_EMBEDDER` 重起，不必重打镜像；
- **失败仍然显式**：首启下载失败**不静默降级**——`/healthz` 保持 `embedder_ready=false`，
  首个需要向量的工具调用显式报错（D1/G22），绝不悄悄换用错模型。

镜像已内建，部署时**无需任何额外配置**：
- `Dockerfile` 设 `ENV HF_HOME=/data/models`（**独立的模型卷挂载点**），缓存落进模型卷，重建容器不丢；
- `MEMEX_EMBEDDER_LOAD_TIMEOUT` 默认 **300s**（`embeddings.py:_load_timeout_seconds`），
  给首次下载留足时间——早先的 20s 会把「第一次下载」误判成加载失败；
- `src/memex/embeddings.py` 本就是**离线优先、缺则联网**：先
  `SentenceTransformer(model, local_files_only=True)`，失败才联网下载；
- 下载走 HTTPS，故 base 阶段装了 `ca-certificates`。

> **首启观察窗口**：容器起来后 `/healthz` 先返回 `status=warming` /
> `embedder_ready=false`，下载完成即转 `ok`。这是正常态，不是故障。

> **纯离线部署**：目标机不能出网时，预先往模型卷目录放好 HF 缓存
> 即可首启命中；否则 `huggingface_hub` 回源带退避重试会**挂几分钟**（`tech-design.md` §4.6）。

**CPU 弱的服务器**可以改 `MEMEX_EMBEDDER=http:<url>` 打到专门的 embedding 服务，
把重活分离出去（`operations.md` §1.4）。

### 5.3 卷与目录

**模型缓存单独一个卷**，与数据卷分开——这样重建库 / 整体替换数据时不必重下那 470MB 模型。

```
【数据卷】/data/memex/              ← MEMEX_HOME（E17，要备份）
  memex.db                真源：repos/analyses/features/cards/evidence/
                               patterns/pattern_members/pattern_intents/
                               sessions/session_stats/meta      【千金难买，备份它】
  index.db                派生：chunks/chunk_vectors/chunk_fts 【可 reindex 重建】
  repos/<owner>__<name>/  克隆（可从 git 重建，不备份）
  bundles/                待下发 bundle（LRU 20 个）
  credentials.json        0600，私有仓凭据

【模型卷】/data/models/           ← HF_HOME（§5.2，纯缓存）
  hub/…                   HuggingFace 缓存（嵌入模型，约 470MB，可重新下载）
```

**为什么拆**：数据卷**有状态、要备份**；模型卷**纯缓存、可再生**。分开的好处：
- 重建库 / 搬数据卷时模型卷不动，首启不必再下 470MB；
- 模型卷可放读多写少的盘，数据卷留在系统盘；
- 备份策略各自独立——模型卷**不该进备份**，省掉 470MB 死重量。

**拆不拆都行**：不拆则模型落数据卷内，功能完全一样；
拆只是把「可再生的重物」从备份对象里摘出去。

**E17 已真拆**：`core.py` 的 `Paths.index_db` 指向 `index.db`；`store.db.connect()`
连 `memex.db`（main）后 `ATTACH DATABASE index.db AS idx`——真源 11 表落 main、
派生 3 表（`chunks`/`chunk_vectors`/`chunk_fts`）落 idx，检索跨库 JOIN 走 `ATTACH`。
裸表名 `chunks` 由 SQLite 解析到 attached 的 idx，故消费侧（`search.py`/`index.py`/
`cluster.py`/`vibecraft.py`）SQL 无需改动；旧库（14 表全在 `memex.db`）在
`create_schema` 时由 `_migrate_legacy_split` 一次性搬迁。
验收：删掉 `index.db` 后 `memex reindex` 能完整重建检索能力。

**WAL 必须在本地文件系统**——WAL 依赖共享内存，网络盘（NFS）不安全
（`tech-design.md` §4.5）。Docker 的 named volume 是本地盘，没问题；
**不要**把数据卷（`/data/memex`）挂到 NFS。模型卷是纯文件缓存、无 WAL，
放网络盘技术上可行但没必要（每次读模型要过网）。

### 5.4 compose

```yaml
services:
  memex:
    build: .
    image: memex:local
    restart: unless-stopped
    ports: ["8931:8931"]                  # 正常映射；反代链路在前面接
    volumes:
      - memex-data:/data/memex          # 数据卷（要备份）
      - memex-models:/data/models      # 模型卷（纯缓存，不用备份）
    environment:
      MEMEX_HOME: /data/memex
      MEMEX_TOKEN: ${MEMEX_TOKEN:?必须设置}
      MEMEX_PUBLIC_BASE_URL: https://memex.example.com
      MEMEX_ALLOWED_ORIGINS: https://memex.example.com
      MEMEX_EMBEDDER: paraphrase-multilingual-MiniLM-L12-v2
    shm_size: "256mb"                       # SQLite WAL 共享内存，别太小
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8931/healthz',timeout=5).status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 120s                    # 首次要 load 模型，给足
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 4G }
volumes:
  memex-data:
  memex-models:
```


**卷的两种挂法**（§5.3）。**生产**用上面两个具名卷；**本地开发**常改成绑定挂载，
数据落仓库 `./data`（已 `.gitignore`），删容器不丢库、也能直接用 `sqlite3` 打开
`./data/memex.db`；模型缓存同理单独挂 `./models`（可整体替换数据而不重下模型）：

```yaml
    volumes:
      - ./data:/data/memex
      - ./models:/data/models   # 可再次删除重建，不影响数据
```

两者都须落**本地文件系统**（数据卷的 WAL 依赖共享内存，NFS 不安全，见 §5.3）。
用 `python -c` 而不是 `curl` 探活，省掉镜像里一个包。

`healthcheck` **不要带 Bearer**——这就是 §4.3 不要鉴权的原因。

**宿主机目录惯例**：把整个服务目录放一处，数据与模型各一个**子目录**——
例如 NAS 上 `/vol1/1000/Docker/memex/`，下面 `data/` + `models/` 分别 bind 到
`/data/memex` 与 `/data/models`。这样备份、迁移、删缓存都只动一个子目录，
不必记两条互不相干的宿主机路径。

## 6. 认证与安全（终态必做项）

### 6.1 静态 Bearer Token（E16）

`Authorization: Bearer <token>`，由应用层校验（`server.py:253-255` 已有，**真实生效**）。
`startup.py:113-115` 的「远程无 token 拒启」门禁也已生效。
**不上 OAuth 2.1**——那是给陌生用户的（`tech-design.md` §4.4）。

token 生成：`openssl rand -hex 32`。**只进环境变量，不进镜像、不进 compose 文件**。

### 6.2 Origin 校验（**当前是 P0 漏洞，必须修**）

规范 MUST 要求（防 DNS rebinding，`tech-design.md:668` / `:711`）。

**现状整条链是错的**：

```
cli.py:139      serve_http(_config(), ...)        _config() 从不设 MEMEX_IS_HTTP
core.py:265     is_http = False
core.py:299     allow_local_paths = _env_bool(..., not is_http) = True
server.py:220   _with_is_http(cfg, True)          只改 is_http，不重算 allow_local_paths
server.py:249   if resolved.allow_local_paths: return True   ← 任意 Origin 放行
```

**后果**：持 token 的远程调用方能让服务端读容器内任意目录（`read_file_slice` /
`get_evidence_pack` 走 `allow_local_paths`），且 `Origin` 校验形同不存在。

**终态**：新增 `MEMEX_ALLOWED_ORIGINS`（逗号分隔的 origin 白名单）。

- 无 `Origin` 头（curl / git / 非浏览器 MCP 客户端）→ **放行**；
- 有 `Origin` 头 → 必须在白名单内，否则 `403`；
- **绝不再拿 `allow_local_paths` 当 Origin 开关**——两者语义无关，
  这正是本 bug 的根因。

### 6.3 `allow_local_paths` 在远程恒 false

`core.py:299` 的默认值 `not is_http` 方向正确，但 `_with_is_http` 没重算它。
终态：`serve_http` 入口**显式**把 `allow_local_paths` 置 `false`
（不再接受 `MEMEX_ALLOW_LOCAL_PATHS` 在 `serve-http` 下打开）。
连带效果：`fetch_repo` 出参的 `repo_path`（`fetch/repo.py:278`）在远程恒 `null`——
这是**正确**的，agent 本来就看不到服务器路径。**这条要写进 `mcp-tools.md` T1 出参说明**，
否则 agent 会去读一个恒 null 的字段。

### 6.4 限流（G7，**三闸全是空壳，必须实装**）

现状：`http_qps_per_token` / `embed_concurrency` 全仓**只有声明没有消费点**
（只在 `core.py:248,249` 赋值）；`fetch/repo.py:26` 的
`_CLONE_GATE = threading.Semaphore(3)` 是**硬编码**，改
`MEMEX_CLONE_CONCURRENCY` 无效；`rate_limited`（`constants.py:51`）是
**从不抛出的死错误码**，而 `operations.md:317` / `mcp-tools.md:117` 承诺了
`details.retry_after_seconds`。

公网暴露的服务不带限流 = 任何拿到 token 的人都能：
把 clone 闸打满、把 embedding 打满、或者拿你当**免费 clone 代理**
（`tech-design.md` §4.4 明确点名这是滥用面最宽的工具）。

**终态**：

| 闸 | 实现 | 超限行为 |
|---|---|---|
| `MEMEX_HTTP_QPS_PER_TOKEN` | `/mcp` 入口按 Bearer token 计数的令牌桶 | `429` + JSON-RPC `rate_limited` + `details.retry_after_seconds` |
| `MEMEX_CLONE_CONCURRENCY` | **删掉硬编码 Semaphore(3)**，改读 `cfg.clone_concurrency` | 排队等待（不报错） |
| `MEMEX_EMBED_CONCURRENCY` | 语义量闸，包住 embed 批处理 | 排队等待（不报错） |

`fetch_repo` 另有 `MEMEX_MAX_REPO_BYTES`（2 GiB）已有的体积闸，保留。

### 6.5 下载票据安全

见 §4.2。补两条：

- `/bundles/{repo_id}` **必须校验 `repo_id` 形状**（`owner__name`，与
  `fetch/bundle.py:24-25` 同一规则），**必须防路径穿越**——不接受 `..`、`/`。
- 票据 TTL 默认 3600s（`fetch/bundle.py:19` `BUNDLE_TTL_SECONDS` 已是这个值，**收进配置**）。

### 6.6 危险工具

`MEMEX_TOOLS` 默认 `read,write,network`，`destructive` 从不默认开
（`operations.md:17`，G28）。远程形态**照旧**——不要为了图方便把
`destructive` 打开。

## 7. 客户端接入

MCP 标准 `mcpServers` 格式（DSH / Claude Code / Cursor 通用）：

```json
{
  "mcpServers": {
    "memex": {
      "type": "http",
      "url": "https://memex.example.com/mcp",
      "headers": { "Authorization": "Bearer <你的 token>" }
    }
  }
}
```

命令行等价：

```bash
claude mcp add --transport http memex https://memex.example.com/mcp \
  --header "Authorization: Bearer $MEMEX_TOKEN"
```

DSH GUI 等价路径：MCP 服务器设置 → 添加 → 类型 `http` → 填 URL 与 Authorization 头。

**客户端不需要为 `/bundles` 配任何东西**——票据即凭证，`git clone` 直接用返回的 URL。

## 8. 需要实现的改动清单

> 这是「要做成最终形态还差什么」的**完整**答案。按模块分组，每条给出位置、
> 现状证据、目标与**验收判据**。P0 不做完远程形态**不可用或不安全**。

### A 组｜阻断级（不做则功能不通或不安全）

| # | 位置 | 现状 | 目标 | 验收 |
|---|---|---|---|---|
| A1 | `fetch/bundle.py:53` + 新增 `mcp/server.py` 路由 | 返回 `file://`，无下载端点 | 返回 `{MEMEX_PUBLIC_BASE_URL}/bundles/{repo_id}?…sig=…`；新增 `GET /bundles/{repo_id}` | 远程 agent `git clone` 回来的 URL **真能拉到 bundle**，sha256 对得上 |
| A2 | `mcp/handlers.py:1291` + `mcp-tools.md` T17 | `bundle_path` = 服务器本地路径，远程 agent 传不进来 | 新增 `bundle_url`（服务端可匿名 GET 的直链，如预签名 OSS/S3 URL）为主路径；`bundle_path` 只留本地形态 | 无外网/无私有凭据场景能跑通 T17 上传登记 |
| A3 | `cli.py:139` + `core.py:299` + `server.py:220` | `allow_local_paths` 远程仍 `true`，任意 Origin 放行 | `serve_http` 入口显式置 `false` | 远程形态 `read_file_slice` 拒本地路径 |
| A4 | `core.py` + `server.py:245-251` | 无 Origin 白名单，靠 `allow_local_paths` 误打误撞 | 新增 `MEMEX_ALLOWED_ORIGINS`，无 Origin 放行 / 有则白名单 | 带伪造 `Origin` 的 `POST /mcp` 返回 403 |
| A5 | `handlers.py:78-95` `Runtime.conn` | 全进程共用一个 `sqlite3.Connection`；8 线程并发即 `IndexError: tuple index out of range`（`handlers.py:1104` 对 `sqlite3.Row` 做 `dict(r)`） | 线程局部连接（`threading.local`）或连接池；SQLite 须 serialized 模式 | `tests/test_serve_http_concurrency.py` 通过；`operations.md:97-108` 的 n=200 复测通过 |
| A6 | `fetch/repo.py:26` | `_CLONE_GATE = Semaphore(3)` 硬编码 | 读 `cfg.clone_concurrency` | 改 `MEMEX_CLONE_CONCURRENCY=8` 实际生效 |
| A7 | `server.py` 入口 | 无 QPS 限流；`rate_limited` 是死码 | 按 token 令牌桶 + `429` + `retry_after_seconds` | 压测超 `MEMEX_HTTP_QPS_PER_TOKEN` 返回 429 且带 `retry_after_seconds` |
| A8 | 新增 `/healthz` | 无 | 见 §4.3 | `docker compose ps` 健康；返回体无路径/token |

### B 组｜可用性（不做则难用或数据有风险）

| # | 位置 | 现状 | 目标 | 验收 |
|---|---|---|---|---|
| B1 | 新增 `MEMEX_PUBLIC_BASE_URL` | **全仓无任何 base_url 配置** | 必填（远程形态缺省拒启）；服务端自己拼绝对 `https://` URL | 反代换端口/换域名时 T4 返回的 URL 不用改代码 |
| B2 | 新增 `MEMEX_ALLOWED_ORIGINS` | 无 | 见 A4 | 同 A4 |
| B3 | `embeddings.py:127` + `server.py:223` `rt.warm()` | 预热**已实现**（daemon 线程 + 超时） | 保持；补 `/healthz` 的 `embedder_ready` 反映 | 首启后第一个 `search` 不卡十几秒 |
| B4 | `core.py:72-74` / `store/db.py` | E17 分库是假的：14 张表全在 `memex.db` | 真拆：`memex.db` 真源 11 表、`index.db` 派生 3 表；跨库检索用 `ATTACH` | 删掉 `index.db` 后 `memex reindex` 能完整重建检索能力 |
| B5 | `fetch/bundle.py` | 每次调用都重新 `git bundle create` | 同 `ref` 已有则直接复用（按 `repo_id.sha12.bundle` 存在性命中） | 同 ref 连续两次调用，第二次不重新打包 |
| B6 | `fetch/bundle.py` | 无 LRU，bundle 无限堆积 | `bundles/` 保留最近 20 个，总量超 `MEMEX_MAX_BUNDLE_BYTES` 删最老 | 反复请求后 `bundles/` 目录大小有界 |

### C 组｜一致性（静默错误，远程单库下危害被放大）

| # | 位置 | 现状 | 目标 | 验收 |
|---|---|---|---|---|
| C1 | `store/search.py:118-120` | embedder 过滤为空时**静默退化为 `WHERE 1=1`**，拉任意已有向量 | 按 `meta.embedder` 显式过滤；缺失即 `conflict`，**不兜底** | 换 embedder 未 reindex 时检索**报错**而非返回错向量 |
| C2 | `store/index.py:142-168` | 写入无断言 | 写入前断言 `chunk_vectors.embedder == meta.embedder`，否则拒绝写入 | 满足 decisions.md:708-714 的「写入断言+拒绝写入」 |
| C3 | `patterns/cluster.py:270-275` | `_embedder_for(conn)` 只读 meta，忽略 `recluster(conn,cfg)` 已拿到的 cfg | 以传入 cfg 为准 | 装了 sentence-transformers 时不再出现 hash:64 与 ST 两种向量空间混进同一张 `chunk_vectors` |
| C4 | `startup.py:87` | 混模型只 `warnings` | 启动即 `conflict` 拒启，提示 `memex reindex --embedder X` | 混模型库启动被拒 |
| C5 | `import_/vibecraft.py:241` | `count_units(md) < 20` 硬编码，同文件 :29 import 的是 `MIN_PRINCIPLE_UNITS`(=40) | ⚠️**原「统一用 MIN_PRINCIPLE_UNITS」是错的**：`mechanism_desc` 的阈值是 20（`validator.py:309`、`report.schema.json` `minLength:20`），40 是**原理轴**的。正解：`constants.py` 新增 `MIN_MECHANISM_UNITS = 20`，vibecraft 改用它 | 同一张卡在 vibecraft 路径与完整契约路径判定一致；vibecraft 三卡（27/29/33 单元）**不再**被误判 `principle_too_short` |

### D 组｜契约收口（文档与代码对齐）

| # | 位置 | 现状 | 目标 |
|---|---|---|---|
| D1 | `mcp-tools.md:465` | 「一次性下载地址」措辞含糊 | 按 §4.2 改写为「短时签名只读票据，绑定 commit，下载后不删文件」 |
| D2 | `mcp-tools.md` T1 出参 | 未说明远程 `repo_path` 恒 null | 明确「远程形态 `repo_path` 恒 `null`（`allow_local_paths=false`）」 |
| D3 | `mcp-tools.md` T17 | 入参只有 `bundle_path` | 补 `bundle_url`，标注二者互斥与适用形态 |
| D4 | `operations.md` §1 | 无 `MEMEX_PUBLIC_BASE_URL` / `MEMEX_ALLOWED_ORIGINS` / `MEMEX_BUNDLE_TTL_SECONDS` | 补进环境变量总表，标注「远程必填」 |
| D5 | `operations.md:28` | `MEMEX_ALLOW_LOCAL_PATHS` 默认值描述与代码实际行为不符 | 改成「`serve-http` 下**恒 false**，不接受环境变量打开」 |
| D6 | `pyproject.toml:20` | `http` extra 声明但零 import | 删除 |
| D7 | `server.py:224,290-292` | `Mcp-Session-Id` 下发后 **只写不查**，任意 id 都能继续 POST | 要么真正校验，要么**不下发**（`tech-design.md` §4.3 说是可选项） |
| D8 | `operations.md:97-108` | 「n=200 全通」在本机复现失败、且无可复现脚本 | 改写为「每线程一条连接」的硬约束 + 8 线程 Barrier 实测口径（`tests/test_serve_http_concurrency.py`）；规模数字移出契约，作部署后实测项 |

## 9. 运维

| 事项 | 做法 |
|---|---|
| **备份** | 只备份 `memex.db`（真源）。`sqlite3 /data/memex/memex.db ".backup /backup/memex-$(date +%F).db"`，每日全量 + 离线留存。`index.db` 不备份（`memex reindex` 可重建），`repos/` 不备份（可重新 clone） |
| **换嵌入模型** | `docker compose exec memex memex reindex --embedder <新模型>`（需先改环境变量再重启）；**换完必须重跑**，否则 C1 会报 `conflict` |
| **token 轮换** | 改 `MEMEX_TOKEN` 环境变量 + `docker compose up -d`。**在途 bundle 票据同时全部失效**（HMAC 密钥即 token）——这是有意的 |
| **升级** | `docker compose build && docker compose up -d`。库比代码旧先 `docker compose run --rm memex memex migrate --dry-run`；库比代码新**拒绝启动是正确的**（G9） |
| **排障入口** | `docker compose logs`；`/healthz` 看 `embedder_ready`；MCP 调用错误看 `details.reason`（`operations.md:107-108`：`SQLite objects created in a thread` = 连接亲和性，`ConnectionResetError` = backlog） |
| **会话清扫** | **已实现**：`server.py:302-315` 每 60s 后台线程 + 每次工具调用顺带清扫（`operations.md:86-93`） |

## 10. 明确不做的事

写清楚免得下一个人又加一遍：

| 不做 | 理由 |
|---|---|
| **SSE 分支** | 工具全是请求-响应语义，无 server→client 推送需求（E15）。将来要做批量分析进度推送再加 |
| **OAuth 2.1 / 动态客户端注册** | 那是给陌生用户的。自用静态 token 足够（E16） |
| **多用户 / 租户隔离** | 单人或小团队自用。做了也是半成品 |
| **服务器上跑 agent** | 违反第一原则：机械活留 server，分析判断交给 agent |
| **bundle 下载后删文件** | 破坏网络中断后的重试；bundle 本来就能按 ref 重生成。靠 LRU 清理 |
| **把 `repos/` 纳入备份** | 可从 git 重建，备份它只是浪费存储 |
| **在请求路径上下载模型** | 会挂住 `initialize`；离线环境下退避重试能挂几分钟 |
| **"先本地 stdio 跑通再上远程"的过渡版本** | 本文的 `serve-http` 形态与本地 `serve-mcp` **共用同一套 `Runtime`/`handlers`**，不是两套实现；本地形态保留是为了单仓调试方便，不是"上一阶段" |

## 11. CI 与测试

`.circleci/config.yml` 里只有一条工作流 **`main-image`**，只在 `main` 分支 push 时触发：

```
validate-main ──┬─→ build-main-amd64 ──┐
                └─→ build-main-arm64 ──┴─→ build-main-manifest
```

| job | 干什么 |
|---|---|
| `validate-main` | `cimg/python:3.12` 容器里 `pip install -e '.[dev]'` → `python -m mypy src/memex` → `MEMEX_EMBEDDER=hash:64 python -m pytest tests/ -q` |
| `build-main-{amd64,arm64}` | machine executor（`ubuntu-2204:current`）上 `docker buildx build --platform linux/{amd64,arm64}`，push 到 `ghcr.io/sqing33/memex` |
| `build-main-manifest` | 校验两个 digest 的架构与 label 后 `docker buildx imagetools create` 合并成 `:latest` |

凭据来自 CircleCI **组织级 Context `ghcr`**（`GHCR_USERNAME` / `GHCR_TOKEN`，变量名与 Benchmark 同名）：
三个 build job 各挂一行 `context: ghcr`；`validate-main` 不需要凭据，不挂。
用 Context 而不是项目级 env，是为了**一次配、所有项目共用**——轮换 token 只改 context 一处。
（新项目接入照抄：建 context `ghcr` → 给用到它的 job 挂 `context: ghcr`。）
不需要在平台侧设分支触发器——触发条件写在 workflow 的 `filters` 里。

两个**刻意的取舍**，别在后续改动里丢掉：

- **validate-main 不联网**：测试用 `MEMEX_EMBEDDER=hash:64`（伪向量），`.[dev]` extra 里
  **没有** `sentence-transformers` / `numpy`。`huggingface_hub` / `sentence-transformers` 的
  import 靠 `# type: ignore[import-not-found]` 兜底，`pyproject.toml` 里对应三个模块关了
  `unused-ignore`。零网络依赖 = CI 不会因为模型仓库抽风而红。
- **validate-main 不装 Node**：前端产物测试（`tests/test_site_build.py`）是**站点展示层**回归，
  不是 MCP / 分析核心。CI 镜像里没有 Node，这些用例会**显式 `skip`**——这是有意的边界，
  与「Node 工具链不进 Python 包、分析路径永不依赖 Node、React build 属部署／手动」
  （`decisions.md` / `tech-design.md`）一致。本地有 Node 时它们照常跑（185 例全绿）。
  守卫用 `shutil.which("node")` 判定，**不要**改回跑 `node --version` 看返回码：
  二进制不存在时 `subprocess` 抛的是 `FileNotFoundError`，不是非零返回码——CI 上就是这么塌的。

镜像每次 main push 都重建；部署端只要 `docker compose pull && docker compose up -d`
（见仓库根 `docker-compose.yml` 头注释）。
