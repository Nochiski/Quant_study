"""도구의 계산 부분 — 지정 채널 저장·검증, 가까운 지지·저항, 기간 표기, 채널 안 위치."""

import json
from pathlib import Path

import numpy as np
import pytest

from technical_analysis import fib_channel as fc
from technical_analysis.tool import channels, summary

DATES = np.array(["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"], dtype="datetime64[D]")
HIGH = np.array([11.0, 12, 13, 12, 14])
LOW = np.array([9.0, 10, 8, 9, 10])


def test_parse_point_spec() -> None:
    p = channels.parse_point("2026-10-21:low")
    assert p == channels.PointSpec(date="2026-10-21", side="low")


@pytest.mark.parametrize("bad", ["2026-10-21", "2026-10-21:mid", "21-10-2026:low"])
def test_parse_point_rejects_bad_spec(bad: str) -> None:
    with pytest.raises(ValueError, match="날짜:low"):
        channels.parse_point(bad)


def test_resolve_points_takes_low_or_high_of_that_bar() -> None:
    specs = [channels.parse_point(s) for s in ("2026-10-19:low", "2026-10-21:low", "2026-10-23:high")]
    r = channels.resolve(specs, DATES, HIGH, LOW)
    assert r.ok
    assert r.channel == fc.Channel(0, 9.0, 2, 8.0, 4, 14.0)


def test_resolve_reports_non_trading_day() -> None:
    specs = [channels.parse_point(s) for s in ("2026-10-18:low", "2026-10-21:low", "2026-10-23:high")]
    r = channels.resolve(specs, DATES, HIGH, LOW)
    assert r.status is channels.ResolveStatus.NOT_A_TRADING_DAY
    assert "2026-10-18" in r.detail


def test_resolve_requires_dates_in_order_a_before_b() -> None:
    specs = [channels.parse_point(s) for s in ("2026-10-21:low", "2026-10-19:low", "2026-10-23:high")]
    assert channels.resolve(specs, DATES, HIGH, LOW).status is channels.ResolveStatus.BAD_ORDER


def test_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "channels.json"
    specs = [channels.parse_point(s) for s in ("2019-08-06:low", "2022-09-30:low", "2024-07-11:high")]
    channels.save(path, "코스피", "코스피", specs)
    assert channels.load_all(path)["코스피"].points == specs
    assert json.loads(path.read_text())["코스피"]["name"] == "코스피"
    channels.remove(path, "코스피")
    assert channels.load_all(path) == {}


def test_months_label() -> None:
    assert summary.span_label(5, False) == "5일"
    assert summary.span_label(21 * 14 + 3, False) == "14개월"
    assert summary.span_label(63, True) == "3개월+"


def test_nearest_levels_split_above_and_below_and_merge_close_ones() -> None:
    levels = [
        summary.Level(105.0, "R1"),
        summary.Level(105.1, "채널 5선"),  # 0.2% 안 → 한 줄로 합침
        summary.Level(110.0, "R2"),
        summary.Level(95.0, "S1"),
        summary.Level(90.0, "POC"),
        summary.Level(120.0, "R3"),
        summary.Level(130.0, "먼 저항"),
        summary.Level(float("nan"), "없음"),
    ]
    near = summary.nearest(levels, price=100.0, atr=2.0, k=3)
    assert [lv.labels for lv in near.above] == [["R1", "채널 5선"], ["R2"], ["R3"]]
    assert [lv.labels for lv in near.below] == [["S1"], ["POC"]]
    assert near.above[0].distance_pct == pytest.approx(5.0)
    assert near.above[0].distance_atr == pytest.approx(2.5)


def test_channel_position_reports_level_and_neighbours() -> None:
    ch = fc.Channel(0, 100.0, 10, 100.0, 5, 120.0)  # 평평, 폭 20 → 0.5 간격 10
    pos = summary.channel_position(ch, x=20, price=133.0)
    assert pos.ratio == pytest.approx(1.65)
    assert (pos.below_ratio, pos.above_ratio) == (1.5, 2.0)
    assert pos.below_value == pytest.approx(130.0)
    assert pos.above_value == pytest.approx(140.0)


def test_grid_neighbours_are_ordered_by_price_for_down_channel() -> None:
    # 하락 채널: 폭 −20 → 레벨이 커질수록 값이 낮아진다(0.5 간격 = 10)
    ch = fc.Channel(0, 100.0, 10, 90.0, 5, 75.0)
    below, above = summary.grid_neighbours(ch, x=10, price=67.0, k=2)
    assert [round(v, 6) for _, v in below] == [60.0, 50.0]
    assert [r for r, _ in below] == [1.5, 2.0]
    assert [round(v, 6) for _, v in above] == [70.0, 80.0]
    assert [r for r, _ in above] == [1.0, 0.5]


def test_grid_neighbours_up_channel() -> None:
    ch = fc.Channel(0, 100.0, 10, 100.0, 5, 120.0)
    below, above = summary.grid_neighbours(ch, x=20, price=133.0, k=2)
    assert below == [(1.5, 130.0), (1.0, 120.0)]
    assert above == [(2.0, 140.0), (2.5, 150.0)]
