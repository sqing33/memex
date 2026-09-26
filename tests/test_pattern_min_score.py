"""给 T10 加测试：min_score 必须如实出现在 list_patterns 出参里。

先写测试、在旧代码上确认失败，再改实现（AGENTS.md：零假成功 + 先取证再动代码）。
"""
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


def test_list_patterns_exposes_min_score(tmp_path, monkeypatch):
    """min_score = 簇内成员 score 的最小值，必须如实出现在 T10 出参里。

    V4 实测：0.60 阈值下簇精度仅 25% 且无零误报阈值，server 分不出松紧，
    所以必须把最紧余弦交出去，而不是假装簇很确定。
    """
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")
    conn = store_db.connect(cfg.paths.db)
    emb = hash_embedder(64)
    a = _seed_repo_and_card(conn, emb, repo_id="org__a", identity="org/a",
                            mech="Bounded retry with exponential backoff across remote calls.")
    b = _seed_repo_and_card(conn, emb, repo_id="org__b", identity="org/b",
                            mech="Cache entries evicted by an LRU journal rebuilt atomically.")
    recluster(conn, cfg, threshold=0.0)

    row = conn.execute("SELECT MIN(score) AS ms FROM pattern_members").fetchone()
    expect = row["ms"]
    assert expect is not None and expect < 1.0, f"测试前提不成立：min score={expect}"

    res = H._t10_list_patterns(_rt(conn, cfg), {})
    assert res["ok"] is True
    assert res["items"], "应当有一个 pattern"
    item = res["items"][0]
    assert "min_score" in item, f"T10 出参缺 min_score，实际键={sorted(item)}"
    assert abs(item["min_score"] - expect) < 1e-6, (
        f"min_score 应为 pattern_members.score 的最小值，期望={expect} 实际={item['min_score']}"
    )
    # 必须是真实数值（不是写死的 1.0 / 0.0 之类的假信号）
    assert 0.0 < item["min_score"] < 1.0, f"min_score 应是真实余弦，实际={item['min_score']}"
