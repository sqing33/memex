"""site —— 把知识库导成站点数据（decisions.md C11 / tech-design.md §2.7）。

Python 侧只做**查库 + 写 JSON**；排版是 web/ 里 React 预渲染的活，
Node 工具链不进本包（pyproject.toml 的 dependencies = [] 保持空）。
"""

from __future__ import annotations

from .dump import SCHEMA_ID, dump_site_data

__all__ = ["dump_site_data", "SCHEMA_ID"]
