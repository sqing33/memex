"""五轴正文不得雷同（axis_reuse 门禁）+ 门禁基线（G23）。

V2 实测事故：urllib3 报告五份五轴正文一字不差（distinct=1/5），校验器不报错、
照样落库。当时手工改了，但下一个 agent 还会再犯。

门禁口径（docs/report-contract.md 第 3 节）：按去标点、去空白后的归一化文本统计
distinct 轴数，三轴以上雷同即判 axis_reuse——所以 distinct<=3 都该拒
（三轴雷同、四轴雷同、五轴全同）。constants.MAX_AXIS_REUSE 与这条对齐。

夹具来源：tests/_report_fixture.py 现造合法报告 + 真代码文件。历史写法写死
'/workspace/memex/reports/axum_report.json'（被 gitignore，仓库里没有）必然
collection error。见 E23。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.contract.validator import validate_report  # noqa: E402

from _report_fixture import legal_report, write_repo  # noqa: E402

AXES = ["runtime_control_flow", "data_flow", "state_lifecycle",
        "failure_recovery", "concurrency_timing"]


def _rep() -> dict:
    return copy.deepcopy(legal_report())


def _validate(rep: dict, tmp_path: Path) -> dict:
    return validate_report(rep, repo_root=write_repo(tmp_path))


def _problems(vres: dict) -> set[str]:
    return {p.get("code") for p in (vres.get("problems") or []) if isinstance(p, dict)}


def test_基准_合法报告本身通过(tmp_path):
    vres = _validate(_rep(), tmp_path)
    assert vres["is_valid"], vres["problems"]


def test_五轴正文完全相同必须被拒(tmp_path):
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    vres = _validate(rep, tmp_path)
    assert not vres["is_valid"], "五轴一字不差必须被拒（urllib3 就是这样蒙混过关的）"
    assert "axis_reuse" in _problems(vres), _problems(vres)


def test_四轴相同只有一轴不同也必须被拒(tmp_path):
    """三轴雷同就没信息量了——五轴是五个维度，不是同一句话的五个副本。"""
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    keep = rep["features"][0]["principles"]["failure_recovery"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    rep["features"][0]["principles"]["failure_recovery"] = keep
    vres = _validate(rep, tmp_path)
    assert not vres["is_valid"], "四轴雷同必须被拒"
    assert "axis_reuse" in _problems(vres), _problems(vres)


def test_三轴相同其余两轴不同也必须被拒(tmp_path):
    """门禁的边界档：a=a=a+b+c（distinct=3）。

    文档说三轴以上雷同即判 axis_reuse，所以这一档必须被拒。
    旧实现 MAX_AXIS_REUSE=2 只拒 distinct<=2，正好把这个边界放行——
    这组测试就是当初漏掉的档（旧用例只覆盖 distinct=1/1/2）。
    """
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for ax in ("runtime_control_flow", "data_flow", "state_lifecycle"):
        rep["features"][0]["principles"][ax] = same
    vres = _validate(rep, tmp_path)
    assert not vres["is_valid"], "三轴雷同必须被拒（distinct=3 是门禁的边界）"
    assert "axis_reuse" in _problems(vres), _problems(vres)


def test_五轴各不相同仍然放行(tmp_path):
    """门禁不能误伤：合法报告本来就该过。"""
    vres = _validate(_rep(), tmp_path)
    assert vres["is_valid"], _problems(vres)


def test_雷同判定忽略标点与空白差异(tmp_path):
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    rep["features"][0]["principles"]["data_flow"] = same.replace(",", ";").replace(".", "!")
    vres = _validate(rep, tmp_path)
    assert not vres["is_valid"], "只改标点不应绕过雷同判定"
    assert "axis_reuse" in _problems(vres), _problems(vres)
