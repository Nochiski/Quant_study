"""engine_diff 순수 로직 단위 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from quant_study.engine_diff import diff_equity_curves, format_report


def d(day: int) -> date:
    return date(2026, 8, day)


def test_identical_curves_ok() -> None:
    curve = {d(1): 100.0, d(2): 110.0}
    report = diff_equity_curves(curve, dict(curve), rel_tol=1e-9)
    assert report.ok
    assert report.matched_sessions == 2
    assert report.max_rel_diff == 0.0
    assert report.breach_count == 0


def test_breach_detected_with_detail() -> None:
    left = {d(1): 100.0, d(2): 110.0}
    right = {d(1): 100.0, d(2): 100.0}
    report = diff_equity_curves(left, right, rel_tol=1e-6)
    assert not report.ok
    assert report.breach_count == 1
    assert report.first_breach is not None
    assert report.first_breach.session == d(2)
    assert report.first_breach.rel_diff == pytest.approx(0.1)
    assert report.max_diff_session == d(2)


def test_one_sided_sessions_counted_not_compared() -> None:
    left = {d(1): 100.0, d(2): 110.0}
    right = {d(1): 100.0, d(3): 120.0}
    report = diff_equity_curves(left, right, rel_tol=1e-6)
    assert report.matched_sessions == 1
    assert report.left_only_sessions == 1
    assert report.right_only_sessions == 1
    assert report.ok  # 공통 세션은 일치


def test_empty_intersection_is_not_ok() -> None:
    report = diff_equity_curves({d(1): 100.0}, {d(2): 100.0}, rel_tol=1e-6)
    assert not report.ok


def test_invalid_tolerance_rejected() -> None:
    with pytest.raises(ValueError, match="rel_tol"):
        diff_equity_curves({}, {}, rel_tol=0.0)


def test_format_report_mentions_breach() -> None:
    left = {d(1): 100.0}
    right = {d(1): 90.0}
    report = diff_equity_curves(left, right, rel_tol=1e-6)
    text = format_report("buy-hold", report)
    assert "MISMATCH" in text
    assert "first breach" in text
