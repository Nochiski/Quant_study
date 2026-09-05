"""sizing 모듈 단위 테스트 — 라우터와 대조 하네스가 공유하는 수량 규칙."""

from __future__ import annotations

import pytest

from backtest_engine.sizing import floor_delta_shares


def test_floors_toward_zero() -> None:
    assert floor_delta_shares(70_000.0, 9_999.0) == 7  # 7.0007 → 7
    assert floor_delta_shares(-70_000.0, 9_999.0) == 7  # 부호는 호출 측 책임
    assert floor_delta_shares(999.0, 1_000.0) == 0


def test_exact_multiple() -> None:
    assert floor_delta_shares(70_000.0, 7_000.0) == 10


def test_non_positive_price_rejected() -> None:
    with pytest.raises(ValueError, match="reference_price=0"):
        floor_delta_shares(1_000.0, 0.0)
