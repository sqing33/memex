# src

memex 的实现代码。**V1 垂直切片已落地**：完整契约 + 一条端到端闭环
（`fetch → evidence → begin → validate → commit → search`），17 个 MCP 工具全部实现。

模块划分以 [`../docs/tech-design.md`](../docs/tech-design.md) §3.1 为准，
与 `/workspace/recall/` 的复用关系见 [`../docs/solution-analysis.md`](../docs/solution-analysis.md) 附录。

契约先于代码：工具面见 [`../docs/mcp-tools.md`](../docs/mcp-tools.md)（G1/G2/G8/G22），
报告契约见 [`../docs/report.schema.json`](../docs/report.schema.json)（G3）与
[`../docs/report-contract.md`](../docs/report-contract.md)（G13）；
配置与运行语义见 [`../docs/operations.md`](../docs/operations.md)。

## 实际结构（与 tech-design §3.1 一致）

```
src/memex/
  __init__.py     契约常量（CONTRACT_ID / CONTRACT_VERSION / SCHEMA_VERSION）与版本号
  __main__.py     `python -m memex` 入口
  cli.py          init / serve-mcp / serve-http / reindex / migrate / export-site / stats / forget-repo / import-vibecraft
  core.py         配置、路径、slugify、repo URL 解析、度量单位（count_units）
  constants.py    五原理轴 / 卡片五类 / 错误码 / RRF / kind_prior 等共享常量
  fsutil.py       语言识别、忽略规则、gitignore 匹配、walk_files、哈希（fetch 与 evidence 共用）
  limits.py       depth 档位（fast/standard/deep）与各上限常量
  embeddings.py   嵌入器抽象：sentence-transformers / http / hash（degraded）
  store/          SQLite + 向量 + FTS5（db / analysis / index / search，14 张表）
  fetch/          仓库抓取与缓存 + git bundle 下发/上传 + 镜像与身份解析（G10/G22）
  evidence/       静态证据包：入口点 / 模块树 / 符号清单 + 代码切片
  contract/       报告 schema + 校验器（validator 是写入路径关卡）+ 契约下发
  session/        会话状态机 + session_stats 归档（G18）
  mcp/            MCP 协议层与工具面（17 个 handler + stdio/HTTP 服务端 + help 内容）
  patterns/       跨仓聚类 + pattern chunk 合成（G14）
  analyze/        提交路径：render / rows / commit
  batch/          服务端 LLM 批量分析（V5 排期，V1 为占位）
  import_/        VibeCraft 回填（G12）；目录名带下划线避开关键字
  site/           export-site 的模板与渲染
```

**依赖分层**（tech-design §3.2）：`store`/`fetch`/`evidence`/`contract`/`session`/`mcp`/`site`
属**核心层**，只用标准库；`patterns` 的向量计算与嵌入属**默认层**（numpy / sentence-transformers）；
多语言语法校验属**可选层**（tree-sitter）。核心层零第三方依赖由离线测试层自动验证。

## 测试

```
uv run --with pytest python3 -m pytest tests/ -q      # 单元 + 端到端（19 项）
uv run --with mypy   python3 -m mypy --strict src/memex  # 类型检查（零错误）
```

`tests/` 覆盖：契约校验器、提交路径与检索往返、跨仓聚类（含 fork 不重复计数）、
会话状态机、以及一条完整的 MCP 端到端闭环（本地 fixture 仓库，不走网络）。
