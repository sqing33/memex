"""证据层：证据包、代码切片、符号抽取（T2 / T3 / A2 / D3）。"""

from __future__ import annotations

from .pack import build_pack
from .slice import read_slice
from .symbols import Symbol, extract_symbols

__all__ = ["Symbol", "build_pack", "extract_symbols", "read_slice"]
