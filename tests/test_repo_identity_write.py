"""G10 写侧：身份 / fork / 改名合并的回归测试。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memex.core import Config
from memex.fetch import hostmeta
from memex.fetch import repo as R
from memex.store import db as store_db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    cfg = Config(home=tmp_path / "home")
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    return store_db.connect(str(cfg.paths.db))


def _insert(conn: sqlite3.Connection, repo_id: str, full_name: str, **kw) -> None:
    R._upsert_repo(
        conn,
        repo_id=repo_id,
        full_name=full_name,
        url="https://github.com/" + full_name,
        host="github.com",
        identity_key=kw.pop("identity_key", "github.com#" + full_name),
        source="clone",
        repo_path="/tmp/" + repo_id,
        head_sha=kw.pop("head_sha", "aaa"),
        default_branch="main",
        subpath=None,
        is_stale=False,
        is_local=False,
        language="python",
        **kw,
    )


# ---------------- 单元：宿主元数据解析 ----------------

def test_parse_github_reads_id_and_fork_parent():
    m = hostmeta._parse_github(
        {
            "id": 12345678,
            "fork": True,
            "parent": {"full_name": "torvalds/linux"},
            "stargazers_count": 42,
            "license": {"spdx_id": "MIT"},
            "description": "  Linux  ",
            "default_branch": "master",
        }
    )
    assert m.numeric_id == 12345678
    assert m.fork is True
    assert m.fork_of == "torvalds/linux"
    assert m.stars == 42
    assert m.license == "MIT"
    assert m.description == "Linux"
    assert m.default_branch == "master"
    assert m.reason is None


def test_noassertion_license_is_absent_not_named():
    """GitHub 对「无许可证」返回 NOASSERTION——那是「没有」，不能当成许可证名写进库。"""
    m = hostmeta._parse_github({"id": 1, "fork": False, "license": {"spdx_id": "NOASSERTION"}})
    assert m.license is None
    assert m.fork is False


def test_identity_key_degrades_without_numeric_id():
    """没查到 id 时必须退化成 owner/name 并标记「未校验」，不能编一个数字 id。"""
    ref = R.parse_repo_url("https://github.com/octo/demo")
    key, degraded = hostmeta.HostMeta().identity_key(ref)
    assert key == "github.com#octo/demo"
    assert degraded is True


def test_identity_key_uses_numeric_id_when_available():
    ref = R.parse_repo_url("https://github.com/octo/demo")
    key, degraded = hostmeta.HostMeta(numeric_id=999).identity_key(ref)
    assert key == "github.com#999"
    assert degraded is False


def test_unsupported_host_degrades_with_reason(tmp_path: Path):
    ref = R.parse_repo_url("https://gitlab.com/octo/demo", allow_hosts=("gitlab.com",))
    m = hostmeta.fetch_host_meta(Config(home=tmp_path), ref)
    assert m.numeric_id is None
    assert "未实现元数据 API" in (m.reason or "")


# ---------------- 写侧：fork / 星标 ----------------

def test_upsert_writes_fork_relation(conn: sqlite3.Connection):
    _insert(conn, "github.com__o__fork", "o/fork", is_fork=True, fork_of="o/upstream")
    row = R.get_repo(conn, "github.com__o__fork")
    assert bool(row["is_fork"]) is True
    assert row["fork_of"] == "o/upstream"


def test_upsert_writes_stars_license_description(conn: sqlite3.Connection):
    _insert(conn, "github.com__o__a", "o/a", stars=123, license="MIT", description="desc")
    row = R.get_repo(conn, "github.com__o__a")
    assert (row["stars"], row["license"], row["description"]) == (123, "MIT", "desc")


def test_absent_fork_of_does_not_erase_known_relation(conn: sqlite3.Connection):
    """宿主这次没给 fork_of 时，必须保留库里已知的——「不知道」不是「没有」。"""
    _insert(conn, "github.com__o__a", "o/a", is_fork=True, fork_of="o/up")
    _insert(conn, "github.com__o__a", "o/a", is_fork=False, fork_of=None)
    row = R.get_repo(conn, "github.com__o__a")
    assert row["fork_of"] == "o/up"


def test_absent_stars_does_not_erase_known_value(conn: sqlite3.Connection):
    _insert(conn, "github.com__o__a", "o/a", stars=123)
    _insert(conn, "github.com__o__a", "o/a", stars=None)
    assert R.get_repo(conn, "github.com__o__a")["stars"] == 123


# ---------------- 改名合并（P0-4） ----------------

def _owner_row(conn: sqlite3.Connection, *, repo_id: str, full_name: str, identity_key: str, head_sha: str = "aaa"):
    _insert(conn, repo_id, full_name, identity_key=identity_key, head_sha=head_sha)
    return conn.execute(
        "SELECT repo_id, full_name, head_sha, aliases_json FROM repos WHERE identity_key = ? AND repo_id <> ?",
        (identity_key, "github.com__o__newname"),
    ).fetchone()


def test_rename_merges_into_existing_repo_id(conn: sqlite3.Connection):
    """同一 identity_key 的另一个名字 → 沿用既有 repo_id，旧名进 aliases。"""
    row = _owner_row(conn, repo_id="github.com__o__old", full_name="o/old", identity_key="github.com#555")
    out = R._merge_renamed(
        conn,
        Config(home=Path("/tmp/x")),
        owner=row,
        ref=R.parse_repo_url("https://github.com/o/newname"),
        identity_key="github.com#555",
        head_sha="aaa",
        language="go",
        meta=hostmeta.HostMeta(numeric_id=555),
    )
    assert out["merged_into"] == "github.com__o__old"
    assert out["renamed_from"] == "o/old"
    assert out["repo"]["repo_id"] == "github.com__o__old"
    assert out["repo"]["repo_full_name"] == "o/newname"
    assert out["aliases"] == ["o/old"]
    assert R.get_repo(conn, "github.com__o__old")["aliases_json"] == '["o/old"]'


def test_rename_does_not_create_a_second_row(conn: sqlite3.Connection):
    row = _owner_row(conn, repo_id="github.com__o__old", full_name="o/old", identity_key="github.com#555")
    R._merge_renamed(
        conn, Config(home=Path("/tmp/x")), owner=row,
        ref=R.parse_repo_url("https://github.com/o/newname"),
        identity_key="github.com#555", head_sha="aaa", language="go",
        meta=hostmeta.HostMeta(numeric_id=555),
    )
    n = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    assert n == 1, "改名后不该多出一条 repos 行"


def test_rename_with_new_head_marks_stale(conn: sqlite3.Connection):
    """改名顺带有了新提交：既有分析必须标 stale（绝不自动重分析）。"""
    row = _owner_row(conn, repo_id="github.com__o__old", full_name="o/old", identity_key="github.com#555", head_sha="old")
    R._merge_renamed(
        conn, Config(home=Path("/tmp/x")), owner=row,
        ref=R.parse_repo_url("https://github.com/o/newname"),
        identity_key="github.com#555", head_sha="new", language="go",
        meta=hostmeta.HostMeta(numeric_id=555),
    )
    assert bool(R.get_repo(conn, "github.com__o__old")["is_stale"]) is True


def test_rename_appends_to_existing_aliases(conn: sqlite3.Connection):
    row = _owner_row(conn, repo_id="github.com__o__old", full_name="o/old", identity_key="github.com#555")
    # 先种好历史名，再触发改名（顺序反了的话种数据本身会把 aliases 覆盖掉）
    conn.execute("UPDATE repos SET aliases_json = ? WHERE repo_id = ?", ('["o/v1"]', "github.com__o__old"))
    row = conn.execute(
        "SELECT repo_id, full_name, head_sha, aliases_json FROM repos WHERE identity_key = ?",
        ("github.com#555",),
    ).fetchone()
    out = R._merge_renamed(
        conn, Config(home=Path("/tmp/x")), owner=row,
        ref=R.parse_repo_url("https://github.com/o/newname"),
        identity_key="github.com#555", head_sha="aaa", language="go",
        meta=hostmeta.HostMeta(numeric_id=555),
    )
    assert out["aliases"] == ["o/v1", "o/old"], "改名轨迹要累加，不能只留最近一次"


def test_repo_summary_exposes_identity_fields(conn: sqlite3.Connection):
    _insert(conn, "github.com__o__a", "o/a", is_fork=True, fork_of="o/up", aliases=["o/old"])
    s = R.repo_summary(R.get_repo(conn, "github.com__o__a"))
    assert s["is_fork"] is True
    assert s["fork_of"] == "o/up"
    assert s["aliases"] == ["o/old"]
    assert s["merged_into"] is None
