"""把 docs/report.schema.json 生成为 src/memex/contract/schema_data.py。

docs 是唯一事实源；本脚本保证包内嵌入的 schema 与 docs 不漂移。
用法：python3 tools/gen_schema_data.py
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "report.schema.json")
DST = os.path.join(ROOT, "src", "memex", "contract", "schema_data.py")

Q3 = "'" * 3

HEADER = (
    '"""memex 报告契约的机器可读版本（由 docs/report.schema.json 生成，勿手改）。\n\n'
    'docs/report.schema.json 是唯一事实源；改契约请先改 docs，再运行：\n'
    '    python3 tools/gen_schema_data.py\n'
    '校验器 contract/validator.py 直接读本模块的 SCHEMA。\n"""\n\n'
    'from __future__ import annotations\n\n'
    'import json\n'
    'from typing import Any\n\n'
    '_RAW = r' + Q3 + "\n"
)
FOOTER = "\n" + Q3 + "\nSCHEMA: dict[str, Any] = json.loads(_RAW)\n"


def main() -> None:
    with open(SRC, encoding="utf-8") as fh:
        data = json.load(fh)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    assert Q3 not in body, "schema 含三引号，无法嵌入"
    with open(DST, "w", encoding="utf-8") as fh:
        fh.write(HEADER + body + FOOTER)
    print("wrote " + DST)


if __name__ == "__main__":
    main()