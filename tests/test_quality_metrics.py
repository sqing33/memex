"""质量指标必须真算，不能写死（G23 bug5）。

旧实现里 axis_completeness / evidence_coverage 是字面量 1.0，
commit.py 甚至不读校验器的值。五仓实测全 1.0 看着像「质量好」，
其实是构造出来的——同 pattern_members.score 写死 1.0 是同一个病。

这些测试直接打 _counts 的真算结果：造一份只有部分轴有内容、
部分卡没证据、少数卡不可借鉴的报告，断言四个比率**各自不同**。
比率若全被写成 1.0，这些断言立刻失败。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memex.contract.validator import validate_report  # noqa: E402

REPO_ROOT = "/root/.memex/repos/github.com__tokio-rs__axum"


def _counts_of(report: dict) -> dict:
    vres = validate_report(report, repo_root=REPO_ROOT)
    assert vres["is_valid"], vres["problems"]
    return vres["counts"]


def test_真实报告的四个质量比率都能算出来():
    rep = json.loads(Path("/workspace/memex/reports/axum_report.json").read_text())
    c = _counts_of(rep)
    for key in ("axis_completeness", "evidence_coverage", "axis_diversity", "reusable_rate"):
        assert key in c, f"counts 缺 {key}"
        assert isinstance(c[key], float), f"{key} 必须是数值"
        assert 0.0 <= c[key] <= 1.0, f"{key} 越界: {c[key]}"


def test_证据覆盖率低时不被写死成1():
    """有卡没证据 → evidence_coverage 必须真的小于 1。"""
    rep = json.loads(Path("/workspace/memex/reports/axum_report.json").read_text())
    # 直接调 _counts，绕开「缺证据会被校验器拦」的门禁，测的是指标本身
    from memex.contract.validator import _counts

    for feat in rep["features"]:
        for card in feat["cards"]:
            card["evidence"] = []
    c = _counts(rep)
    assert c["cards"] > 0
    assert c["evidence_coverage"] == 0.0, f"没证据的卡不该算出 1.0，实际 {c['evidence_coverage']}"


def test_可借鉴率低时不被写死成1():
    from memex.contract.validator import _counts

    rep = json.loads(Path("/workspace/memex/reports/axum_report.json").read_text())
    for feat in rep["features"]:
        for card in feat["cards"]:
            card["reusable"] = False
    c = _counts(rep)
    assert c["reusable_rate"] == 0.0, f"全不可借鉴不该算出 1.0，实际 {c['reusable_rate']}"


def test_轴区分度在五轴雷同时下降():
    """雷同的五轴区分度趋近 0——这正是 axis_reuse 门禁要拦的形态。"""
    from memex.contract.validator import _counts

    rep = json.loads(Path("/workspace/memex/reports/axum_report.json").read_text())
    base = _counts(rep)["axis_diversity"]
    assert base > 0.9, f"真实报告的轴区分度应接近 1，实际 {base}"

    same = rep["features"][0]["principles"]["runtime_control_flow"]
    for feat in rep["features"]:
        for ax in feat["principles"]:
            feat["principles"][ax] = same
    assert _counts(rep)["axis_diversity"] < 0.5, "五轴雷同时区分度应大幅下降"


def test_部分轴留白时完整度下降():
    from memex.contract.validator import _counts

    rep = json.loads(Path("/workspace/memex/reports/axum_report.json").read_text())
    blanked = 0
    for feat in rep["features"]:
        for ax in list(feat["principles"]):
            if blanked % 3 == 0:
                feat["principles"][ax] = ""
            blanked += 1
    c = _counts(rep)
    assert c["axis_completeness"] < 1.0, f"有空轴时完整度必须小于 1，实际 {c['axis_completeness']}"
