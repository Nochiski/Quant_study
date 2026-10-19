"""비교기·보고기 자체 테스트. 같은 것은 같다고, 한 비트·±0·순서·타입 차이는 다르다고 말해야 한다."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import pytest

from strategy_workbench.application.portfolio_design.facade.design import PortfolioPipelineResult
from strategy_workbench.domain.portfolio.facade.construction import ExclusionReason

from ._compare import Difference, compare, differences, format_report
from ._corpus import pipeline_cases
from ._engines import ENGINES, pipeline_source


@dataclass(frozen=True)
class _Row:
    value: float | None
    reasons: tuple[ExclusionReason, ...]
    weights: dict[str, float]


_ROW = _Row(1.5, (ExclusionReason.MISSING_FACTOR, ExclusionReason.FUTURE_DATA), {"a": 0.25})


def _paths(expected: object, actual: object) -> list[str]:
    return [path for path, _, _ in differences(expected, actual)]


def test_equal_values_report_nothing() -> None:
    nan = float("nan")
    assert _paths(_ROW, replace(_ROW)) == []
    assert _paths((nan, -0.0, None, 3), (nan, -0.0, None, 3)) == []


def test_one_bit_and_signed_zero_differ() -> None:
    assert _paths(_ROW, replace(_ROW, value=math.nextafter(1.5, 2.0))) == [".value"]
    assert _paths(0.0, -0.0) == [""]
    other_nan = struct.unpack("<d", struct.pack("<q", 0x7FF8000000000001))[0]
    assert _paths(float("nan"), other_nan) == [""]


def test_order_and_type_differ() -> None:
    swapped = (ExclusionReason.FUTURE_DATA, ExclusionReason.MISSING_FACTOR)
    assert _paths(_ROW, replace(_ROW, reasons=swapped)) == [".reasons[0]", ".reasons[1]"]
    assert _paths(_ROW, replace(_ROW, value=None)) == [".value"]
    assert _paths(1, 1.0) == [""]
    assert _paths(True, 1) == [""]
    assert _paths((1.0,), (1.0, 2.0)) == [".len"]
    assert _paths(_ROW, replace(_ROW, weights={"b": 0.25})) == [".weights.keys[0]"]
    assert _paths(frozenset({1, 2}), frozenset({1})) == [".len"]


def test_set_elements_and_dict_keys_compare_by_type_and_bits() -> None:
    assert _paths(frozenset({1}), frozenset({True})) == ["[0]"]
    assert _paths(frozenset({0.0}), frozenset({-0.0})) == ["[0]"]
    assert _paths({np.int64(1)}, {1}) == ["[0]"]
    assert _paths({0.0: "x"}, {-0.0: "x"}) == [".keys[0]"]
    assert _paths(frozenset({"b", "a"}), frozenset({"a", "b"})) == []


def test_types_without_an_equality_rule_are_refused() -> None:
    assert (
        _paths((None, "a", 1, True, date(2024, 1, 2)), (None, "a", 1, True, date(2024, 1, 2))) == []
    )
    with pytest.raises(TypeError):
        _paths(np.int64(1), np.int64(1))
    with pytest.raises(TypeError):
        _paths(object(), object())


def test_report_keeps_the_first_differences_and_the_total() -> None:
    pairs = [(f"case-{index}", (0.0, 1.0), (-0.0, 2.0)) for index in range(3)]
    report = compare("L1", pairs, limit=4)
    assert (report.cases, report.total, report.ok) == (3, 6, False)
    assert report.differences[0] == Difference(
        "L1", "case-0", "[0]", "0.0 (bits=0x0000000000000000)", "-0.0 (bits=0x8000000000000000)"
    )
    assert len(report.differences) == 4
    counted = format_report(report, values=False)
    assert "mismatches=6" in counted and "... 2 more" in counted
    assert "expected=" not in counted and "bits=" not in counted
    assert "expected=0.0" in format_report(report, values=True)


def test_a_one_bit_change_deep_in_a_pipeline_result_is_located() -> None:
    """실제 L3 결과 안 target 비중 한 칸을 1ulp 바꾸면 그 경로 하나만 나온다."""
    case = next(item for item in pipeline_cases() if item.key == "L3/synthetic/combo=0")
    result = ENGINES["object"].pipeline(case, *pipeline_source(case.source))
    assert isinstance(result, PortfolioPipelineResult)
    tape = result.preview.tape
    frame = tape.frames[-1]
    target = frame.targets[0]
    nudged = replace(target, weight=math.nextafter(target.weight, math.inf))
    frames = (*tape.frames[:-1], replace(frame, targets=(nudged, *frame.targets[1:])))
    changed = replace(result, preview=replace(result.preview, tape=replace(tape, frames=frames)))
    report = compare("L3", [(case.key, result, changed)])
    assert [item.path for item in report.differences] == [
        f".preview.tape.frames[{len(frames) - 1}].targets[0].weight"
    ]
