"""serve-http 并发：Runtime 的连接必须**每线程一个**（deployment.md A5）。

历史 bug（G26 实测，reports/g26_bench3.py）：serve_http 里所有请求线程共用同一个
server.rt，而 Runtime.conn 曾是**单个懒加载连接**。SQLite 虽以 serialized 编译
（threadsafety>=3，check_same_thread=False），但 pysqlite 的语句缓存
（cached_statements）会让并发线程**复用同一条 sqlite3_stmt**：一条线程 sqlite3_step
推进游标，另一条 reset/step 同一句，结果集被当场吃掉 —— 对外就是 _t12_list_repos
里 dict(r) 抛 `IndexError: tuple index out of range`（实测 n=8 即现）。

修法（A5）：Runtime.conn 改线程局部（threading.local），每线程独享一个连接与游标状态，
线程间不共享任何可变状态。这条测试因此**必须用真实 Runtime**——手写替身测不出实现里
的线程局部。
"""
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.core import Config  # noqa: E402
from memex.mcp import handlers as H  # noqa: E402
from memex.store import db as store_db  # noqa: E402


def test_runtime_conn_is_thread_local(tmp_path, monkeypatch):
    """Runtime.conn 必须能被多个请求线程各自独立使用（serve-http 每请求一线程）。

    不是「慢一点」的问题：共用连接时第二个线程一碰就报错/吃游标，
    对外表现为 ok:false / internal /「服务端异常」——零假成功的反例。
    """
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMEX_EMBEDDER", "hash:64")
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")

    rt = H.Runtime(cfg)
    try:
        H._t12_list_repos(rt, {})          # 主线程先建连接（模拟第一个 HTTP 请求）

        n = 8
        errors: list[str] = []
        conn_ids: list[int] = []
        ids_lock = threading.Lock()
        barrier = threading.Barrier(n)

        def work() -> None:
            try:
                barrier.wait()
                for _ in range(5):
                    res = H._t12_list_repos(rt, {})
                    if not res.get("ok"):
                        errors.append(str(res.get("error"))[:200])
                with ids_lock:
                    conn_ids.append(id(rt.conn))
            except BaseException as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

        ts = [threading.Thread(target=work) for _ in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert not errors, f"{n} 个并发请求必须全部成功，实际失败 {len(errors)} 个：{errors[:2]}"
        # 每线程独享一个连接：n 个线程 + 主线程各一个（线程局部已弃用主线程那个）
        assert len(conn_ids) == n
        assert len(set(conn_ids)) == n, "连接没有按线程隔离，仍在跨线程共用"
    finally:
        rt.close()


def test_runtime_close_releases_thread_connections(tmp_path, monkeypatch):
    """close() 必须释放各线程的连接（Windows 上句柄不还，库文件删不掉）。"""
    monkeypatch.setenv("MEMEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMEX_EMBEDDER", "hash:64")
    cfg = Config.from_env()
    store_db.init_db(cfg.paths, embedder_spec="hash:64")

    rt = H.Runtime(cfg)
    _ = rt.conn                       # 主线程建连接
    rt.close()
    # close() 之后应能重新开（不是永久关闭），且新连接仍是可用的
    assert H._t12_list_repos(rt, {}).get("ok") is True
    rt.close()


def test_shared_connection_requires_serialized_sqlite():
    """允许跨线程各建连接的前提是 SQLite 本身是 serialized 编译的。

    threadsafety: 3 = 连接可跨线程用；2 = 只能跨线程、不能跨线程共享连接。
    若构建出来是 2 或更低，store.connect 必须显式报错而不是等线上随机崩。
    """
    import pytest

    if sqlite3.threadsafety < 3:
        pytest.fail(
            f"SQLite threadsafety={sqlite3.threadsafety} < 3，"
            "连接不能跨线程用；当前实现会静默走向随机崩溃，必须显式拒绝"
        )
