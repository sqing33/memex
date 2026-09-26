"""site 导出（P2-2 / C11）的回归测试。

site/ 此前一个测试都没有，而它是唯一会把库里的字符串拼进文件系统路径的
模块（out/<repo_id>.html），所以这里重点盯两件事：

1. 空值渲染成破折号，而不是被禁的占位符（N/A / 无 / 未知 …），
   且仓库描述被 HTML 转义，不会把标签当标记注入；
2. 产物是纯静态的：内联 CSS、无外链资源、无 JS 依赖（C11 的承诺）。

关于文件名安全：原以为 repo_id 能造成路径穿越，实测**证伪**了，记在这里
免得再查一遍——vibecraft__ + slugify(...) 兜底路径上的 slugify 会吃掉 / 与 ..；
正常路径的 repo_id_for 只有在 full_name 恰好两段时才用，所以 repo_id 里
不可能出现路径分隔符。剩下唯一的边界是超长名（411 字节 > 255）抛 OSError，
属于显式报错而非静默失败，可接受。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config, Paths  # noqa: E402
from memex.store import db as store_db  # noqa: E402

from memex.site.render import export_site  # noqa: E402


@pytest.fixture()
def paths(tmp_path) -> Paths:
    os.environ["MEMEX_HOME"] = str(tmp_path / "home")
    os.environ["MEMEX_EMBEDDER"] = "hash:64"
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return cfg.paths


def _seed_repo(paths, repo_id, *, full_name=None, **cols):
    """种一个仓。默认值刻意留空，好让「破折号」那条断言真的被测到。"""
    conn = store_db.connect(paths.db)
    try:
        row = {
            "full_name": full_name or repo_id,
            "url": "https://example.invalid/" + repo_id,
            "host": "example.invalid",
            "language": None,
            "stars": None,
            "license": None,
            "description": None,
            "is_stale": 0,
            "is_fork": 0,
            "default_branch": "main",
            "source": "upload",
        }
        row.update(cols)
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, language, stars, "
            "license, description, is_stale, is_fork, default_branch, source, identity_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                repo_id,
                row["full_name"],
                row["url"],
                row["host"],
                row["language"],
                row["stars"],
                row["license"],
                row["description"],
                row["is_stale"],
                row["is_fork"],
                row["default_branch"],
                row["source"],
                "id#" + repo_id,
            ),
        )
    finally:
        conn.close()


def _seed_analysis(paths, repo_id, *, aid=None, **cols):
    conn = store_db.connect(paths.db)
    try:
        row = {
            "commit_sha": "0" * 40,
            "analyst": "agent-x",
            "producer": "agent",
            "status": "committed",
            "created_at": "2026-01-01T00:00:00Z",
            "counts": '{"features": 2, "cards": 5, "evidence": 3}',
            "quality": '{"code_mismatch": 0}',
            "reindex_state": "indexed",
        }
        row.update(cols)
        conn.execute(
            "INSERT INTO analyses(analysis_id, repo_id, commit_sha, contract_version, depth, "
            "analyst, producer, status, created_at, counts_json, quality_json, "
            "finished_at, reindex_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                aid or ("an-" + repo_id.replace("/", "")),
                repo_id,
                row["commit_sha"],
                "memex/report/1",
                "standard",
                row["analyst"],
                row["producer"],
                row["status"],
                row["created_at"],
                row["counts"],
                row["quality"],
                row["created_at"],
                row["reindex_state"],
            ),
        )
    finally:
        conn.close()


def _read(path):
    return Path(path).read_text(encoding="utf-8")

DESC = '<script>alert("x")</script> & more'


def test_export_site_writes_index_and_one_page_per_repo(paths, tmp_path):
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["repos"] == 1
    assert (out / "index.html").exists()
    assert (out / "github.com__o__a.html").exists()
    assert Path(res["files"][0]).name == "index.html", "index 要排在第一个"
    assert "o/a" in _read(out / "index.html")


def test_export_site_uses_dash_not_banned_placeholder(paths, tmp_path):
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))
    page = _read(out / "github.com__o__a.html")

    assert "—" in page, "空值应渲染成破折号"
    for banned in ("N/A", "未知", "待补充", "TODO"):
        assert banned not in page, "产物里出现了被禁占位符：" + banned


def test_export_site_escapes_repo_description(paths, tmp_path):
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a", description=DESC)
    out = tmp_path / "site"
    export_site(paths, out=str(out))
    page = _read(out / "github.com__o__a.html")

    assert "<script>alert" not in page, "仓库描述里的标签被当成标记注入了"
    assert "&lt;script&gt;" in page
    assert "&amp;" in page


def test_export_site_output_is_self_contained(paths, tmp_path):
    """C11 承诺：内联 CSS、无外链资源、无 JS 依赖。"""
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    for name in ("index.html", "github.com__o__a.html"):
        page = _read(out / name)
        assert "<style>" in page, name + " 缺内联样式"
        assert "<script" not in page.lower(), name + " 不该有 JS"
        assert "http://" not in page, name + " 引用了明文外链"


def test_export_site_marks_stale_fork_and_pending(paths, tmp_path):
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__stale", full_name="o/stale", is_stale=1)
    _seed_repo(paths, "github.com__o__fork", full_name="o/fork", is_fork=1)
    _seed_analysis(paths, "github.com__o__stale", aid="an-stale", reindex_state="pending")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    stale_page = _read(out / "github.com__o__stale.html")
    fork_page = _read(out / "github.com__o__fork.html")

    assert "stale" in stale_page
    assert "reindex pending" in stale_page
    assert ">fork<" in fork_page
    assert "stale" not in fork_page, "干净的仓不该白挂 stale 标签"


def test_export_site_repo_without_analysis(paths, tmp_path):
    """没分析过的仓也要出页面，并如实说「尚未分析」而不是显示成 0。"""
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__fresh", full_name="o/fresh")
    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["repos"] == 1
    assert "尚未分析" in _read(out / "github.com__o__fresh.html")


def test_export_site_lists_every_analysis(paths, tmp_path):
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    # 幂等键是 (repo_id, commit_sha, contract_version)，两次分析必须换 commit
    _seed_analysis(paths, "github.com__o__a", aid="an-old",
                   commit_sha="a" * 40, created_at="2026-01-01T00:00:00Z")
    _seed_analysis(paths, "github.com__o__a", aid="an-new",
                   commit_sha="b" * 40, created_at="2026-02-01T00:00:00Z")
    out = tmp_path / "site"
    export_site(paths, out=str(out))

    page = _read(out / "github.com__o__a.html")
    assert "agent-x" in page, "analyst 要如实显示"
    # 模板是 <dt>卡片数</dt><dd>5</dd>，两个分析块各出现一次
    assert page.count("<dt>卡片数</dt><dd>5</dd>") == 2, "两次分析都要列出卡片数"
    assert "code_mismatch" in page, "quality_json 要如实摊出来"


def test_export_site_errors_when_db_missing(paths, tmp_path):
    """零假成功：库不存在要显式报错，不是导出一个空站点。"""
    from memex.core import MemexError
    from memex.site.render import export_site

    paths.db.unlink()
    with pytest.raises(MemexError) as ei:
        export_site(paths, out=str(tmp_path / "site"))
    assert ei.value.code == "not_found"


def test_export_site_repo_id_has_no_path_separator(paths, tmp_path):
    """文件名安全：repo_id 里出现 / 会写到 out 目录外面去。"""
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__a", full_name="o/a")
    # 绕过正常入口，直接种一行「脏」repo_id，模拟历史脏数据
    conn = store_db.connect(paths.db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
            "VALUES(?,?,?,?,?,?)",
            ("evil/../../x", "evil", "https://example.invalid/x",
             "example.invalid", "id#evil", "upload"),
        )
    finally:
        conn.close()

    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    for f in res["files"]:
        assert Path(f).parent.resolve() == out.resolve(), "产物写到了 out 之外：" + f

def test_export_site_skips_dirty_repo_id_and_reports_it(paths, tmp_path):
    """脏 repo_id 要被显式跳过并记账，而不是崩掉整个导出。"""
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__o__good", full_name="o/good")
    conn = store_db.connect(paths.db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO repos(repo_id, full_name, url, host, identity_key, source) "
            "VALUES(?,?,?,?,?,?)",
            ("evil/../../x", "evil", "https://example.invalid/x",
             "example.invalid", "id#evil", "upload"),
        )
    finally:
        conn.close()

    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["skipped_repo_ids"] == ["evil/../../x"], "脏 repo_id 要被记账"
    assert res["repos"] == 1, "只数真正导出的仓"
    assert (out / "github.com__o__good.html").exists(), "好仓不该被连累"
    for f in res["files"]:
        assert Path(f).parent.resolve() == out.resolve(), "产物写到了 out 之外：" + f


def test_page_filename_rewrites_and_rejects():
    from memex.site.render import _page_filename

    assert _page_filename("github.com__sqing33__PTNexus") == "github.com__sqing33__PTNexus"
    assert _page_filename("evil/../../x") != "evil/../../x"
    assert "/" not in (_page_filename("evil/../../x") or "")
    assert _page_filename("") is None
    assert _page_filename(".") is None
    assert _page_filename("..") is None
    long_name = _page_filename("a" * 400)
    assert long_name is not None and len(long_name.encode("utf-8")) <= 255
    cjk = _page_filename("仓" * 200)
    assert cjk is not None and len(cjk.encode("utf-8")) <= 255

def test_page_filename_collision_is_contained_by_skipping(paths, tmp_path):
    """a/b 与 a_b 会重写成同名——防碰撞靠的是调用方跳过，不靠重写本身。"""
    from memex.site.render import _page_filename

    # 重写函数本身不提供唯一性：a/b 与 a_b 压成同一个 a_b。
    assert _page_filename("a/b") == _page_filename("a_b") == "a_b"

    # 防碰撞发生在 export_site：改写后与原值不一致的 id 全部跳过、不写盘。
    # a_b 是合法 id，原样落盘；a/b 需要改写，所以被跳过——
    # 落盘的那批 id 上映射是恒等的，同名文件不可能出现。
    _seed_repo(paths, "a/b", full_name="o/ab")
    _seed_repo(paths, "a_b", full_name="o/a_b")
    _seed_repo(paths, "github.com__o__good", full_name="o/good")
    out = tmp_path / "site"
    res = export_site(paths, out=str(out))

    assert res["skipped_repo_ids"] == ["a/b"], res["skipped_repo_ids"]
    assert res["repos"] == 2, "a_b 与 good 落盘，a/b 跳过"
    assert [Path(f).name for f in res["files"]] == ["index.html", "a_b.html", "github.com__o__good.html"]
    assert (out / "a_b.html").exists(), "a_b 是合法 id，原样落盘"

def test_analysis_block_shows_analyst_not_model(paths, tmp_path):
    """分析块展示「分析者」（analyst），不展示「模型」——analyses 表没有模型列。

    decisions.md 已定：`meta.embedder` 是**全库唯一**索引模型（reindex 时原子切换），
    不是每次分析各记一个；`analyst` 记的是谁做的分析（如 dsh-mcp-client）。
    所以旧文档那一行「已分析 commit / 分析时间 / 模型 ← analyses」本身就是错的：
    analyses 里根本没有模型这一列，写进文档只会让人去找一个不存在的字段。
    """
    from memex.site.render import export_site

    _seed_repo(paths, "github.com__a__b", full_name="a/b")
    _seed_analysis(paths, "github.com__a__b", aid="an-analyst", analyst="dsh-mcp-client")
    out = tmp_path / "site"
    export_site(paths, out=str(out))
    page = _read(out / "github.com__a__b.html")
    assert "分析者" in page
    assert "dsh-mcp-client" in page
    # 旧文档承诺的「模型」字段在 analyses 里不存在，页面不得凭空造一个
    assert "模型" not in page, "页面出现了 analyses 里根本没有的「模型」字段"
