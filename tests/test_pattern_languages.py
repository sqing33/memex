"""T10 languages 字段测试：必须来自成员卡片的 cards.language，不能来自恒 NULL 的 repos.language。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.patterns import recluster  # noqa: E402
from memex.store import db as store_db  # noqa: E402
from memex.embeddings import hash_embedder  # noqa: E402

from tests.test_patterns import _seed_repo_and_card  # noqa: E402
from memex.mcp import handlers as H  # noqa: E402


def _rt(conn, cfg):
    class RT:
        pass
    rt = RT()
    rt.conn = conn
    rt.cfg = cfg
    return rt


def test_list_patterns_languages_come_from_member_cards(tmp_path, monkeypatch):
    """languages 必须来自成员卡片的 cards.language。

    跨语言模式的核心卖点就是「这几个仓用了不同语言」。但 list_patterns 原来从
    repos.language 取，而 fetch 路径从不填它（实测 5/5 仓为 NULL），
    于是这个字段恒为空——契约里写着 languages，实际永远拿不到。
    """
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    emb = hash_embedder(64)
    _seed_repo_and_card(conn, emb, repo_id="org__a", identity="org/a",
                        mech="Bounded retry with exponential backoff across remote calls.",
                        language="python")
    _seed_repo_and_card(conn, emb, repo_id="org__b", identity="org/b",
                        mech="Cache entries evicted by an LRU journal rebuilt atomically.",
                        language="kotlin")
    conn.execute("UPDATE repos SET language = NULL")   # 真实 fetch 路径就是这样
    recluster(conn, cfg, threshold=0.0)

    res = H._t10_list_patterns(_rt(conn, cfg), {})
    assert res["ok"] is True and res["items"], res
    item = res["items"][0]
    assert item.get("languages") == ["kotlin", "python"], (
        f"languages 应来自成员卡片 cards.language，去重排序后实际={item.get('languages')}"
    )
