"""슬리피지 모델 단위 테스트 (4d): 주당 슬리피지 손계산과 slip ≥ 0 계약."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.slippage import (
    FixedBpsSlippage,
    NoSlippage,
    VolumeShareSlippage,
)
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.types.actions import NoAction
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.orders import Side
from tests.conftest import day, make_instrument, make_ohlc

INSTRUMENT = make_instrument()
BAR = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0, volume=1_000)


def order(side: Side = Side.BUY, quantity: int = 100) -> OrderEvent:
    return OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        source_action=NoAction(),
    )


def test_no_slippage_is_zero() -> None:
    assert NoSlippage().slippage_per_share(order(), BAR, 100.0, Decimal(100)) == 0.0


def test_fixed_bps_is_proportional_to_price() -> None:
    # 10bp × 100 = 0.1 / 주
    assert FixedBpsSlippage(bps=10.0).slippage_per_share(order(), BAR, 100.0, Decimal(100)) == (
        pytest.approx(0.1)
    )
    assert FixedBpsSlippage(bps=10.0).slippage_per_share(order(), BAR, 50.0, Decimal(1)) == (
        pytest.approx(0.05)
    )


def test_volume_share_uses_squared_share_times_impact() -> None:
    # share = 100/1000 = 0.1 → 0.1² × 0.1 × 100 = 0.1
    model = VolumeShareSlippage(volume_limit=0.025, price_impact=0.1)
    # share 0.1은 volume_limit 0.025로 캡 → 0.025² × 0.1 × 100 = 0.00625
    assert model.slippage_per_share(order(), BAR, 100.0, Decimal(100)) == pytest.approx(0.00625)
    uncapped = VolumeShareSlippage(volume_limit=1.0, price_impact=0.1)
    assert uncapped.slippage_per_share(order(), BAR, 100.0, Decimal(100)) == pytest.approx(0.1)


def test_volume_share_with_zero_volume_is_full_limit_impact() -> None:
    empty = make_ohlc(day(2), INSTRUMENT, 100.0, 100.0, 100.0, 100.0, volume=0)
    model = VolumeShareSlippage(volume_limit=0.025, price_impact=0.1)
    assert model.slippage_per_share(order(), empty, 100.0, Decimal(1)) == pytest.approx(0.00625)


@pytest.mark.parametrize(
    "model",
    [NoSlippage(), FixedBpsSlippage(bps=5.0), VolumeShareSlippage(0.025, 0.1)],
)
def test_slippage_is_never_negative_for_either_side(model: SlippageModel) -> None:
    for side in Side:
        assert model.slippage_per_share(order(side), BAR, 100.0, Decimal(10)) >= 0.0


def test_negative_parameters_rejected() -> None:
    with pytest.raises(ValueError, match="bps"):
        FixedBpsSlippage(bps=-1.0)
    with pytest.raises(ValueError, match="volume_limit"):
        VolumeShareSlippage(volume_limit=0.0, price_impact=0.1)
