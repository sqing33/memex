"""memex —— agent 驱动的跨仓库「实现借鉴」知识库。

核心层（store/fetch/evidence/contract/session/mcp/site）只用标准库；
向量计算与嵌入属默认层（numpy / sentence-transformers）；多语言语法校验属可选层（tree-sitter）。
见 docs/tech-design.md §3.2。
"""

from .constants import CONTRACT_ID, CONTRACT_VERSION, SCHEMA_VERSION

__all__ = ["CONTRACT_ID", "CONTRACT_VERSION", "SCHEMA_VERSION", "__version__"]

__version__ = "0.0.1"
