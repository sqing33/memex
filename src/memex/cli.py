"""cli —— memex 子命令（docs/operations.md §3）。

    init / serve-mcp / serve-http / reindex / migrate / export-site / stats
    / forget-repo / import-vibecraft

CLI 是薄边界：只做参数解析 + 配置装配 + 调用对应模块；
业务失败一律打印 `错误：<message>` 到 stderr 并返回非零码（不吐栈）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from .core import Config


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memex", description="agent 驱动的跨仓库「实现借鉴」知识库")
    p.add_argument("--version", action="version", version="memex " + __version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="建库、写 meta、初始化目录")
    s.add_argument("--embedder", default=None, help="嵌入器规格（如 hash:64 / http:<url>）")

    s = sub.add_parser("serve-mcp", help="本地 stdio 形态")
    s.add_argument("--home", default=None)

    s = sub.add_parser("serve-http", help="远程 Streamable HTTP 形态")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8931)

    s = sub.add_parser("reindex", help="重算 chunk 向量并更新 meta（换模型后用，G11）")
    s.add_argument("--embedder", default=None)
    s.add_argument("--repo", action="append", dest="repo_ids", default=None, metavar="REPO_ID",
                 help="只重建该仓的索引，可重复；不给 = 整层清空重建（P1-4）")

    s = sub.add_parser("migrate", help="真源层迁移（G9）")
    s.add_argument("--to", default=None)
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("export-site", help="导出站点数据（--build 顺带排版出 HTML）")
    s.add_argument("--out", default=None)
    s.add_argument("--build", action="store_true",
                   help="顺带跑 web/ 的 React 预渲染出 HTML（需先 cd web && npm ci）")

    sub.add_parser("stats", help="知识库总览（含按 producer 分组的质量，G21）")

    s = sub.add_parser("forget-repo", help="CLI 版删除（G8）")
    s.add_argument("repo_id")
    s.add_argument("--yes", action="store_true")

    s = sub.add_parser("import-vibecraft", help="从 VibeCraft 回填（G12）")
    s.add_argument("path")
    s.add_argument("--dry-run", action="store_true")

    return p


def _config(home: str | None = None) -> Config:
    if home:
        import os

        os.environ["MEMEX_HOME"] = home
    return Config.from_env()


def _export_site(out: str | None, *, build: bool) -> dict[str, Any]:
    """export-site：导 site-data.json，可选顺带跑 React 预渲染。

    构建失败**不抛**、命令仍返回 0，原因只进返回值的 site.build_error。
    理由（decisions.md C11 改写二）：站点是展示层，一个 CSS 编译错不该把
    已经校验通过、code_mismatch=0 的分析变成失败；反过来，失败必须显式
    出现在返回值里——静默跳过等于骗人说「站点是新的」。
    """
    import subprocess
    from pathlib import Path

    from .site.dump import dump_site_data

    cfg = _config()
    res = dump_site_data(cfg.paths, out=out)
    if not build:
        return res

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if not web_dir.is_dir():
        res["site"] = {"built": False, "build_error": "找不到 web/ 目录（源码树形态才带）"}
        return res
    # 数据文件与产物目录用环境变量传给 prerender.mjs，`npm run build`
    # 才能保持成一条不带参数的普通脚本（package.json 里不用写死路径）。
    env = {
        **os.environ,
        "MEMEX_SITE_DATA": str(Path(res["data_file"]).resolve()),
        "MEMEX_SITE_OUT": str(Path(res["out_dir"]).resolve()),
    }
    try:
        proc = subprocess.run(
            ["npm", "run", "build", "--silent"],
            cwd=str(web_dir), capture_output=True, text=True, timeout=300, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        res["site"] = {"built": False, "build_error": str(exc)}
        return res
    if proc.returncode != 0:
        res["site"] = {
            "built": False,
            "build_error": (proc.stderr or proc.stdout or "npm run build 失败").strip()[:2000],
            "returncode": proc.returncode,
        }
        return res
    res["site"] = {"built": True, "out_dir": res["out_dir"]}
    return res


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cmd = args.command
    try:
        if cmd == "init":
            from .store.db import init_db

            cfg = _config()
            res = init_db(cfg.paths, embedder_spec=args.embedder or cfg.embedder or None)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if cmd == "serve-mcp":
            from .mcp.server import serve_stdio

            serve_stdio(_config(args.home))
            return 0
        if cmd == "serve-http":
            from .mcp.server import serve_http

            serve_http(_config(), host=args.host, port=args.port)
            return 0
        if cmd == "reindex":
            from .store.db import reindex

            cfg = _config()
            res = reindex(
                cfg.paths,
                embedder_spec=args.embedder or cfg.embedder or None,
                repo_ids=args.repo_ids,
            )
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if cmd == "migrate":
            from .store.db import migrate

            res = migrate(_config().paths, to=args.to, dry_run=args.dry_run)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if cmd == "export-site":
            res = _export_site(args.out, build=bool(getattr(args, "build", False)))
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if cmd == "stats":
            from .store import connect
            from .store.db import stats

            cfg = _config()
            conn = connect(cfg.paths.db)
            try:
                print(json.dumps(stats(conn), ensure_ascii=False, indent=2))
            finally:
                conn.close()
            return 0
        if cmd == "forget-repo":
            from .store import connect
            from .store.db import forget_repo

            if not args.yes:
                print("危险操作：请加 --yes 确认（G8）", file=sys.stderr)
                return 2
            cfg = _config()
            conn = connect(cfg.paths.db)
            try:
                res = forget_repo(conn, args.repo_id, confirm=True)
            finally:
                conn.close()
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if cmd == "import-vibecraft":
            from .import_.vibecraft import import_vibecraft

            cfg = _config()
            res = import_vibecraft(cfg.paths, cfg, args.path, dry_run=args.dry_run)
            print(json.dumps(res, ensure_ascii=False))
            return 0
    except Exception as exc:  # noqa: BLE001 —— CLI 边界统一兜底，打印而非栈
        print("错误：" + str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
