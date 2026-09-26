# -*- coding: utf-8 -*-
"""回归测试：顶层三个对象数组的**形状**必须被逐元素校验。

Doraemon 真实闭环暴露的洞：validate_report 收了 characteristics / entry_points /
cross_feature_risks 的纯字符串、写了契约外的 note 字段，is_valid 仍是 true，
直到 commit_report 在 render 阶段崩 internal（str has no attribute get）。
校验器是唯一的硬闸门（code_mismatch 必须为 0），漏检形状等于闸门失效。
"""
import json
from pathlib import Path

from memex.contract.validator import validate_report

from test_contract import CARD_OK, _report, _root  # noqa: F401

GOOD_EV = [{"path": "main.py", "start_line": 1, "end_line": 1}]


def test_characteristics_must_be_objects(tmp_path):
    rep = _report(CARD_OK)
    rep["characteristics"] = ["只是一句纯字符串说明，没有任何结构化字段"]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["where"].startswith("/characteristics/0") for p in res["problems"])


def test_characteristics_missing_detail(tmp_path):
    rep = _report(CARD_OK)
    rep["characteristics"] = [{"title": "标题够长了", "evidence": GOOD_EV}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["code"] == "missing_field" and "detail" in p["where"] for p in res["problems"])


def test_characteristics_detail_minlength(tmp_path):
    rep = _report(CARD_OK)
    rep["characteristics"] = [{"title": "标题够长了", "detail": "太短", "evidence": GOOD_EV}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["where"] == "/characteristics/0/detail" for p in res["problems"])


def test_characteristics_evidence_required(tmp_path):
    rep = _report(CARD_OK)
    rep["characteristics"] = [{"title": "标题够长了", "detail": "detail 长度足够通过十五个字符的下限检查。"}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any("evidence" in p["where"] for p in res["problems"])


def test_entry_point_note_rejected(tmp_path):
    """Doraemon 报告里用的就是 note 而不是 role，必须拒收。"""
    rep = _report(CARD_OK)
    rep["entry_points"] = [{"path": "main.py", "note": "这里写的是 note 而不是契约字段 role"}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    codes = {(p["code"], p["where"]) for p in res["problems"]}
    assert ("unknown_field", "/entry_points/0/note") in codes
    assert ("missing_field", "/entry_points/0/role") in codes


def test_entry_point_path_must_exist(tmp_path):
    rep = _report(CARD_OK)
    rep["entry_points"] = [{"path": "no/such/file.py", "role": "Entry point for the CLI", "kind": "main"}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["code"] == "bad_evidence_path" for p in res["problems"])


def test_entry_point_kind_enum_closed(tmp_path):
    rep = _report(CARD_OK)
    rep["entry_points"] = [{"path": "main.py", "role": "Entry point for the CLI", "kind": "not-a-kind"}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["code"] == "bad_enum" for p in res["problems"])


def test_entry_point_role_minlength(tmp_path):
    rep = _report(CARD_OK)
    rep["entry_points"] = [{"path": "main.py", "role": "CLI", "kind": "main"}]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["where"] == "/entry_points/0/role" for p in res["problems"])


def test_cross_feature_risks_must_be_objects(tmp_path):
    rep = _report(CARD_OK)
    rep["cross_feature_risks"] = ["风险描述也是纯字符串，同样没有结构化字段。"]
    res = validate_report(rep, repo_root=_root(tmp_path))
    assert res["is_valid"] is False
    assert any(p["where"].startswith("/cross_feature_risks/0") for p in res["problems"])


def test_schema_defs_match_validator_shapes():
    """把校验器的三组常量与 docs/report.schema.json 的 $defs 对齐，防止再次漂移。"""
    import memex.contract.validator as v

    schema = json.loads((Path(__file__).resolve().parents[1] / "docs" / "report.schema.json").read_text(encoding="utf-8"))
    defs = schema["$defs"]
    for name, required, allowed in (
        ("Characteristic", v._CHARACTERISTIC_REQUIRED, v._CHARACTERISTIC_ALLOWED),
        ("Risk", v._RISK_REQUIRED, v._RISK_ALLOWED),
        ("EntryPoint", v._ENTRY_REQUIRED, v._ENTRY_ALLOWED),
    ):
        spec = defs[name]
        assert set(spec["required"]) == set(required), name
        assert set(spec["properties"]) == allowed, name
        assert spec.get("additionalProperties") is False, name
