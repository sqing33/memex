"""主语言检测：repos.language 只能来自克隆目录的本地统计，不联网。

背景：docs/decisions.md C11 标注站点要展示「语言 / stars / license <- repos（fetch 时抓取）」，
但 fetch/repo.py 的 _upsert_repo 把 language 写死 NULL，于是站点该列恒为破折号。
检测规则见 docs/tech-design.md §4.6.1「仓库元数据的来源边界」。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.fetch.detect import detect_language  # noqa: E402
from memex.store import db as store_db  # noqa: E402


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def test_detects_dominant_language_by_extension(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for i in range(3):
        _touch(root, f"src/mod{i}.rs")
    _touch(root, "README.md")
    assert detect_language(root) == "rust"


def test_ignores_vendored_and_build_dirs(tmp_path: Path) -> None:
    """vendored 依赖不该把宿主仓的主语言带偏——这正是 min_repos 判定要防的污染。"""
    root = tmp_path / "repo"
    for i in range(20):
        _touch(root, f"vendor/lib{i}.go")
    _touch(root, "main.py")
    assert detect_language(root) == "python", "vendor/ 里的 Go 抢走了主语言"


def test_returns_none_for_empty_or_unknown_repo(tmp_path: Path) -> None:
    """空仓 / 全是认不出的文件 -> 留 None，不猜。"""
    assert detect_language(tmp_path / "nope-not-there") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert detect_language(empty) is None
    docs = tmp_path / "docs-only"
    _touch(docs, "README.md")
    _touch(docs, "guide.txt")
    assert detect_language(docs) is None


def test_tie_break_is_deterministic(tmp_path: Path) -> None:
    """平局必须每次给同一个答案，否则同一仓反复 reindex 会来回翻。"""
    root = tmp_path / "repo"
    _touch(root, "a.go")
    _touch(root, "a.py")
    first = detect_language(root)
    for _ in range(3):
        assert detect_language(root) == first
    assert first == "go", "平局按语言名字典序"


def test_fetch_records_language_in_repos(tmp_path: Path, monkeypatch) -> None:
    """端到端：ensure_repo 之后 repos.language 必须有值（这是本次修的 bug）。"""
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")

    src = tmp_path / "src-repo"
    src.mkdir()
    for i in range(3):
        _touch(src, f"src/a{i}.rs")
    _touch(src, "Cargo.toml")

    from memex.core import parse_repo_url
    from memex.fetch import repo as fetch_repo
    from memex.fetch.gitutil import CloneResult

    ref = parse_repo_url("https://github.com/tokio-rs/axum")
    fake = CloneResult(repo_path=str(src), head_sha="a" * 40, default_branch="main")
    monkeypatch.setattr(fetch_repo.gitutil, "clone", lambda *a, **k: fake)

    conn = store_db.connect(cfg.paths.db)
    try:
        fetch_repo.ensure_repo(cfg, ref)
        row = conn.execute(
            "SELECT language FROM repos WHERE repo_id = ?", (ref.repo_id,)
        ).fetchone()
        assert row is not None
        assert row["language"] == "rust", (
            f"fetch 后 repos.language 仍为 {row['language']!r}（应为 rust）"
        )
    finally:
        conn.close()
