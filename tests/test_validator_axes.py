"""五轴正文不得雷同（axis_reuse 门禁）+ 门禁基线（G23）。

V2 实测事故：urllib3 报告五份五轴正文一字不差（distinct=1/5），
校验器不报错、照样落库。当时手工改了，但下一个 agent 还会再犯。

G23 坏样本九类实测：八类被拦住，唯一漏网的就是「五轴正文完全相同」这一类。

用真实提交的 axum 报告当夹具（reports/axum_report.json 已被 validate/commit 过，
本身就是完全合法的报告），只按需改一处轴正文。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.contract.validator import validate_report  # noqa: E402

SRC = Path("/workspace/memex/reports/axum_report.json")

# 真库重建后克隆目录名会变（memex 自己的 repo_id 风格 vs 手工 clone 的短名），
# 所以不写死路径；找不到真实 axum 克隆时下面每个测试显式 skip。
from _axum_repo import require_axum_repo  # noqa: E402
AXES = ["runtime_control_flow", "data_flow", "state_lifecycle",
        "failure_recovery", "concurrency_timing"]
BASE = json.loads(SRC.read_text())


def _rep() -> dict:
    return copy.deepcopy(BASE)


def _problems(vres: dict) -> set[str]:
    return {p.get("code") for p in (vres.get("problems") or []) if isinstance(p, dict)}


def test_基准_真实axum报告本身合法():
    vres = validate_report(_rep(), repo_root=require_axum_repo())
    assert vres["is_valid"], vres["problems"]


def test_五轴正文完全相同必须被拒():
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    vres = validate_report(rep, repo_root=require_axum_repo())
    assert not vres["is_valid"], "五轴一字不差必须被拒（urllib3 就是这样蒙混过关的）"
    assert "axis_reuse" in _problems(vres), _problems(vres)


def test_四轴相同只有一轴不同也必须被拒():
    """三轴雷同就没信息量了——五轴是五个维度，不是同一句话的五个副本。"""
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    keep = rep["features"][0]["principles"]["failure_recovery"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    rep["features"][0]["principles"]["failure_recovery"] = keep
    vres = validate_report(rep, repo_root=require_axum_repo())
    assert not vres["is_valid"], "四轴雷同必须被拒"
    assert "axis_reuse" in _problems(vres), _problems(vres)


def test_五轴各不相同仍然放行():
    """门禁不能误伤：真实报告本来就该过。"""
    vres = validate_report(_rep(), repo_root=require_axum_repo())
    assert vres["is_valid"], _problems(vres)


def test_雷同判定忽略标点与空白差异():
    rep = _rep()
    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for ax in AXES:
        rep["features"][0]["principles"][ax] = same
    rep["features"][0]["principles"]["data_flow"] = same.replace("，", "、").replace("。", "！")
    vres = validate_report(rep, repo_root=require_axum_repo())
    assert not vres["is_valid"], "只改标点不应绕过雷同判定"
    assert "axis_reuse" in _problems(vres), _problems(vres)
