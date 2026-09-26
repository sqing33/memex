"""G26 实测发现的 bug：serve-http 只能扛 2 个并发客户端，4 个就开始报错。

实测（reports/g26_bench3.py，真实 HTTP 打真读库工具 list_repos）：
  n=1  1/1 ok
  n=2  2/2 ok
  n=4  1/4   {"code":"internal","details":{"reason":"SQLite objects created in a
                      thread can only be used in that same thread. ..."}}
  n=8  3/8   同样报错
  n=16 2/16  同样报错

根因：serve_http 里所有请求线程共用同一个 server = Server(resolved)，
也就是同一个 server.rt，而 Runtime.conn 是**单个懒加载连接**；
store/db.py:46 的 sqlite3.connect() 没传 check_same_thread=False（默认 True），
于是任何「不是建连接那个线程」的请求直接 ProgrammingError。

这条正是 docs/tech-design.md §4.8「单机需要支撑几十个并发 agent」的前提，
现在连 4 个都不到。而 serve-http 正是远程部署形态（operations.md）。
"""
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.mcp import handlers as H  # noqa: E402
from memex.store import db as store_db  # noqa: E402


def test_shared_connection_usable_from_request_threads(tmp_path, monkeypatch):
    """Runtime.conn 必须能被多个请求线程共用（serve-http 是每请求一线程）。

    这不是「慢一点」的问题：check_same_thread 默认 True 时，
    第二个线程第一次碰这个连接就 ProgrammingError，
    对外表现为 ok:false / internal /「服务端异常」——零假成功的反例。
    """
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMEX_EMBEDDER", "hash:64")
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")

    class RT:
        def __init__(self):
            self.cfg = cfg
            self._conn = None
            self._embedder = None
            self._embedder_error = None
            self._warm_started = False

        @property
        def conn(self):
            if self._conn is None:
                self._conn = store_db.connect(self.cfg.paths.db)
            return self._conn

    rt = RT()
    H._t12_list_repos(rt, {})          # 主线程先建连接（模拟第一个 HTTP 请求）

    n = 8
    errors: list[str] = []
    barrier = threading.Barrier(n)

    def work() -> None:
        try:
            barrier.wait()
            for _ in range(5):
                res = H._t12_list_repos(rt, {})
                if not res.get("ok"):
                    errors.append(str(res.get("error"))[:200])
        except BaseException as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    ts = [threading.Thread(target=work) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"{n} 个并发请求必须全部成功，实际失败 {len(errors)} 个：{errors[:2]}"


def test_shared_connection_requires_serialized_sqlite():
    """允许跨线程共用连接的前提是 SQLite 本身是 serialized 编译的。

    threadsafety: 3 = 连接可跨线程共用；2 = 只能跨线程、不能共用连接。
    若构建出来是 2 或更低，直接放开 check_same_thread=False 是不安全的，
    必须显式报错而不是等到线上随机崩。
    """
    if sqlite3.threadsafety < 3:
        pytest = __import__("pytest")
        pytest.fail(
            f"SQLite threadsafety={sqlite3.threadsafety} < 3，"
            "连接不能跨线程共用；当前实现会静默走向随机崩溃，必须显式拒绝"
        )
