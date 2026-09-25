# src

memex 的代码（待写）。

**注意：本目录目前是有意空着的。** 技术设计定稿前不落代码 —— 此前 `/workspace/recall/`
的教训是：重心未定就写实现，会把方案锁死在一个可能被推翻的架构上。

模块划分以 [`../docs/tech-design.md`](../docs/tech-design.md) §3.1 为准，
与 `/workspace/recall/` 的复用关系见 [`../docs/solution-analysis.md`](../docs/solution-analysis.md) 附录。

契约先于代码落定：工具面见 [`../docs/mcp-tools.md`](../docs/mcp-tools.md)（G1/G2/G8/G22），
报告契约见 [`../docs/report.schema.json`](../docs/report.schema.json)（G3）与
[`../docs/report-contract.md`](../docs/report-contract.md)（G13）；
配置与运行语义见 [`../docs/operations.md`](../docs/operations.md)（G 组第 2–3 批）。
`src/` 仍为空——技术设计定稿前不落代码。

## 预定结构（与 tech-design §3.1 一致）

```
src/memex/
  core.py       配置、路径、slugify、repo URL 解析
  cli.py        init / serve-mcp / serve-http / reindex / migrate / export-site / stats / forget-repo / import-vibecraft
  store/        SQLite + 向量 + FTS5（可沿用 recall/store）
  fetch/        仓库抓取与缓存（可沿用 recall/analyze/fetch.py）＋ git bundle 下发/上传（T4/T17）＋ 镜像与身份解析（G10/G22）
  evidence/     静态证据包：入口点 / 模块树 / 符号清单（可沿用 recall/analyze/codeindex.py）
  contract/     报告 schema + 校验器（可沿用 recall/analyze/schema.py，地位提升为写入路径关卡）
  session/      会话状态机 + session_stats 归档（新增，G18）
  mcp/          MCP 协议层与工具面（协议层沿用 recall/recall/mcp_server.py，工具面重写）；
                本地 stdio + 远程 Streamable HTTP（只回 application/json）
  patterns/     三通道融合检索 + 聚类（可沿用 recall/store/search.py, recall/analyze/cluster.py）
                + kind_prior / pattern chunk 合成（G14）
                + intent.py（非对称分解，V4 实验分支）
  batch/        服务端 LLM 批量分析（即 recall/analyze/llm.py + pipeline.py 迁移而来）
  import_/      VibeCraft 回填（G12）；目录名带下划线避开关键字
  site/         export-site 的模板与渲染
```

**依赖分层**（tech-design §3.2）：`store`/`fetch`/`evidence`/`contract`/`session`/`mcp`/`site`
属**核心层**，只用标准库；`patterns` 的向量计算与嵌入属**默认层**（numpy / sentence-transformers）；
多语言语法校验属**可选层**（tree-sitter）。核心层零第三方依赖这条承诺，由离线测试层自动验证（§3.5）。
