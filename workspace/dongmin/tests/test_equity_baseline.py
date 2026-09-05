"""T0.7 — `data/equity/baseline.json` 읽기·상수 조회. 스키마·원자 교체는 stage 와 같다."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import stage.baseline as stage_baseline
from equity import baseline as eq_baseline
from equity.baseline import Baseline

SAMPLE: dict[str, object] = {
    "measured_at": "2026-09-05",
    "security_span": {"respan_count": 2, "thresholds": {"EG7": 0.001}},
    "trading_calendar": {"gap_trading_days": 0},
    "_measured": [
        {"table": "security_span", "metric": "respan_count", "inputs": {"stg_listing_daily": "b1"},
         "sql": "SELECT count(*) FROM …", "value": 2, "measured_at": "2026-09-05",
         "growing": False},
    ],
}


def test_JSON_스키마가_stage와_같다(tmp_path: Path) -> None:
    assert eq_baseline.write is stage_baseline.write     # 원자 교체 규약을 그대로 재사용한다
    path = tmp_path / "baseline.json"
    eq_baseline.write(path, SAMPLE)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == set(SAMPLE)
    entry = raw["_measured"][0]
    assert set(entry) >= {"table", "metric", "sql", "value", "measured_at", "growing"}
    assert not (tmp_path / "baseline.json.tmp").exists()  # 임시 파일을 남기지 않는다
    assert eq_baseline.load(path).get("security_span", "respan_count") == 2


def test_없는_상수는_None을_돌려준다(tmp_path: Path) -> None:
    bl = eq_baseline.load(tmp_path / "baseline.json")     # 파일 자체가 없다 = 첫 빌드
    assert bl.data == {}
    assert bl.get("security_span", "respan_count") is None
    assert bl.get("없는테이블", "없는상수") is None


def test_없는_상수는_skip_no_baseline() -> None:
    from equity.gates import SkipGate, require_const

    class _Ctx:
        rule = type("R", (), {"name": "security_span"})()
        baseline = Baseline({})

    with pytest.raises(SkipGate) as e:
        require_const(_Ctx(), "respan_count")            # pyright: ignore[reportArgumentType]
    assert e.value.reason == "no_baseline"
    assert e.value.metrics["missing_metric"] == "security_span.respan_count"


def test_thresholds_하위딕셔너리도_읽는다() -> None:
    bl = Baseline(SAMPLE)
    assert bl.get("security_span", "threshold_EG7") == 0.001
    assert bl.get("trading_calendar", "threshold_EG7") is None


def test_require는_없으면_KeyError(tmp_path: Path) -> None:
    bl = Baseline(SAMPLE, tmp_path / "baseline.json")
    assert bl.require("security_span", "respan_count") == 2
    with pytest.raises(KeyError, match="baseline constant not found"):
        bl.require("security_span", "없는상수")


def test_path_for는_equity_root_아래다(tmp_path: Path) -> None:
    assert eq_baseline.path_for(tmp_path) == tmp_path / "baseline.json"


def test_객체가_아닌_JSON은_예외(tmp_path: Path) -> None:
    p = tmp_path / "baseline.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline.json root must be an object"):
        eq_baseline.load(p)
