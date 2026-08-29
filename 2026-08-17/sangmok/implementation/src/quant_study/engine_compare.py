"""커스텀 backtest_engine과 Zipline을 동일 조건에서 실행하는 대조 하네스.

정합 규칙 (양쪽 모두):
- 판단: 세션 T 마감, 종가 기준. 체결: 다음 실제 세션 시가, 시장가 전량.
- 수량: backtest_engine.sizing.floor_delta_shares (단일 진실 원천을 공유).
- 수수료: 체결 금액 × fee_bps (Zipline은 PerDollar).
- 슬리피지: 기본 시나리오는 없음. `buy-hold-slippage`는 양쪽에 같은 거래량 비중 충격 모델
  (share² × price_impact × price, share = min(체결량/거래량, volume_limit))을 두되 가격 기준은
  다음 bar 시가로 통일한다 (Zipline 기본 VolumeShareSlippage는 종가 기준).
- 공매도: `short-hold`는 비중 −allocation 단일 진입. 차입 비용 0 (Zipline도 없음).

Zipline 쪽 차이 흡수:
- FixedSlippage는 다음 bar 종가에 체결되므로, 다음 bar 시가에 체결하는
  NextBarOpenSlippage를 하네스 전용으로 정의한다.
- XKRX 캘린더에는 CSV에 없는 세션(휴장 불일치)이 있을 수 있다. 그런 bar는
  시가가 NaN이므로 LiquidityExceeded로 미루면 다음 실제 bar 시가에 체결되어
  커스텀 엔진의 "다음 CSV 세션 시가"와 같은 세션이 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pandas as pd
from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.slippage import VolumeShareSlippage
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    ExecutionStyle,
    ExecutionTiming,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceField
from backtest_engine.types.orders import TimeInForce
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from zipline import run_algorithm
from zipline.api import order, set_benchmark, set_commission, set_slippage, symbol
from zipline.finance import commission
from zipline.finance.slippage import LiquidityExceeded, SlippageModel
from zipline.utils.calendar_utils import get_calendar


class CompareScenario(StrEnum):
    BUY_HOLD = "buy-hold"
    GOLDEN_CROSS = "golden-cross"
    BUY_HOLD_SLIPPAGE = "buy-hold-slippage"  # 4d 슬리피지 모델 대조
    SHORT_HOLD = "short-hold"  # 5a 공매도 회계 대조


@dataclass(frozen=True)
class CompareParams:
    ticker: str
    short_window_days: int = 20
    long_window_days: int = 60
    allocation: float = 0.7  # 1.0이면 수수료 때문에 현금 경계에서 두 엔진이 갈라진다
    capital_base_krw: float = 10_000_000.0
    fee_bps: float = 15.0
    volume_limit: float = 0.025  # buy-hold-slippage: 세션 거래량 대비 체결 상한 = 참여율 캡
    price_impact: float = 0.1


# --- 커스텀 엔진 쪽 전략 ------------------------------------------------------


class EngineCompareStrategy:
    """대조 전용 전략: buy&hold(1회 진입) 또는 골든크로스(밴드·긴급청산 없음)."""

    def __init__(self, instrument: InstrumentId, scenario: CompareScenario, params: CompareParams):
        self._instrument = instrument
        self._scenario = scenario
        self._params = params
        lookback = params.long_window_days if scenario is CompareScenario.GOLDEN_CROSS else 1
        self.prices = HistoryRequest(
            instruments=(instrument,), field=PriceField.CLOSE, lookback=lookback
        )
        self._entered = False

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(self.prices,),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(
                {EngineFeature.SHORT_SELLING}
                if self._scenario is CompareScenario.SHORT_HOLD
                else ()
            )
            | frozenset(
                {EngineFeature.PARTIAL_FILL}
                if self._scenario is CompareScenario.BUY_HOLD_SLIPPAGE
                else ()
            ),
        )

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        if not isinstance(event, MarketSnapshot):
            return StrategyDecision.no_action(ctx.now, "market_event_only")

        if self._scenario is not CompareScenario.GOLDEN_CROSS:
            if self._entered:
                return StrategyDecision.no_action(ctx.now, "holding")
            self._entered = True
            target_weight = (
                -self._params.allocation
                if self._scenario is CompareScenario.SHORT_HOLD
                else self._params.allocation
            )
        else:
            closes = ctx.history(self.prices).column(self._instrument)
            short_ma = float(closes[-self._params.short_window_days :].mean())
            long_ma = float(closes.mean())
            target_weight = self._params.allocation if short_ma > long_ma else 0.0

        return StrategyDecision.of(
            ctx.now,
            SetPortfolioTarget(
                targets=(WeightTarget(self._instrument, target_weight),),
                scope=TargetScope.PATCH,
                execution=self._execution(),
            ),
            reason=self._scenario.value,
        )

    def _execution(self) -> ExecutionPolicy:
        if self._scenario is CompareScenario.BUY_HOLD_SLIPPAGE:
            # Zipline VolumeShareSlippage처럼 세션 거래량 × volume_limit까지만 체결하고 잔량은 이월.
            return ExecutionPolicy(
                ExecutionStyle.MARKET,
                ExecutionTiming.NEXT_OPEN,
                TimeInForce.GTC,
                max_participation=self._params.volume_limit,
            )
        return ExecutionPolicy.market_next_open()


def run_engine_side(
    bars: tuple[Bar, ...], scenario: CompareScenario, params: CompareParams
) -> dict[date, float]:
    """커스텀 엔진을 실행해 세션 날짜 → equity 매핑을 반환한다."""
    instrument = bars[0].instrument
    slippage = (
        VolumeShareSlippage(volume_limit=params.volume_limit, price_impact=params.price_impact)
        if scenario is CompareScenario.BUY_HOLD_SLIPPAGE
        else None
    )
    engine = BacktestEngine(
        RunConfig(
            run_id=f"compare-{params.ticker}-{scenario.value}",
            initial_cash=params.capital_base_krw,
            fee_bps=params.fee_bps,
        ),
        slippage=slippage,
    )
    result = engine.run(EngineCompareStrategy(instrument, scenario, params), DataFeed(bars))
    return {snapshot.ts.date(): snapshot.equity for snapshot in result.snapshots}


def make_compare_instrument(ticker: str) -> InstrumentId:
    return InstrumentId(venue="XKRX", symbol=ticker, asset_class=AssetClass.EQUITY, currency="KRW")


# --- Zipline 쪽 ---------------------------------------------------------------


class NextBarOpenVolumeShareSlippage(SlippageModel):
    """Zipline VolumeShareSlippage의 식을 다음 bar 시가 기준으로 적용한다.

    체결량 = min(잔량, volume_limit × 거래량), 충격 = share² × price_impact × open
    (share = 체결량 / 거래량). 커스텀 엔진의 VolumeShareSlippage + max_participation과 동일.
    """

    def __init__(self, volume_limit: float, price_impact: float) -> None:
        super().__init__()
        self._volume_limit = volume_limit
        self._price_impact = price_impact

    def process_order(self, data, order_obj):  # noqa: ANN001  # reason: zipline 콜백 시그니처
        open_price = data.current(order_obj.asset, "open")
        volume = data.current(order_obj.asset, "volume")
        if open_price is None or math.isnan(open_price) or volume is None or math.isnan(volume):
            raise LiquidityExceeded()
        remaining = abs(order_obj.amount - order_obj.filled)
        max_volume = int(volume * self._volume_limit)
        fill = min(remaining, max_volume)
        if fill <= 0:
            raise LiquidityExceeded()
        share = min(fill / volume, self._volume_limit)
        impact = share * share * math.copysign(self._price_impact, order_obj.amount) * open_price
        return open_price + impact, int(math.copysign(fill, order_obj.amount))


class NextBarOpenSlippage(SlippageModel):
    """다음 bar의 시가에 전량 체결. 시가가 NaN인 캘린더 전용 세션은 미룬다."""

    def process_order(self, data, order_obj):  # noqa: ANN001  # reason: zipline 콜백 시그니처
        open_price = data.current(order_obj.asset, "open")
        if open_price is None or math.isnan(open_price):
            raise LiquidityExceeded()
        return open_price, order_obj.amount


def make_compare_initialize(
    ticker: str, fee_bps: float, scenario: CompareScenario, params: CompareParams
):
    def initialize(context) -> None:  # noqa: ANN001  # reason: zipline 콜백 시그니처
        context.asset = symbol(ticker)
        context.entered = False
        # data.history가 번들 시작 이전을 요청하면 LookupError가 나므로 경과 세션을 센다.
        context.calendar_bars = 0
        set_benchmark(context.asset)
        set_commission(us_equities=commission.PerDollar(cost=fee_bps / 10_000))
        if scenario is CompareScenario.BUY_HOLD_SLIPPAGE:
            set_slippage(
                us_equities=NextBarOpenVolumeShareSlippage(params.volume_limit, params.price_impact)
            )
        else:
            set_slippage(us_equities=NextBarOpenSlippage())

    return initialize


def make_compare_handle_data(scenario: CompareScenario, params: CompareParams):
    def handle_data(context, data) -> None:  # noqa: ANN001  # reason: zipline 콜백 시그니처
        context.calendar_bars += 1
        if not data.can_trade(context.asset):
            return
        close_t = data.current(context.asset, "close")
        if close_t is None or math.isnan(close_t):
            # CSV에 없는 캘린더 전용 세션 — 커스텀 엔진에는 존재하지 않는 날.
            return
        open_t = data.current(context.asset, "open")
        if open_t is None or math.isnan(open_t):
            # 거래정지 행(가격 0)은 zipline 저장소에서 NaN으로 읽힌다.
            # 커스텀 엔진 로더는 같은 행을 drop하므로 여기서도 세션이 아닌 것으로 본다.
            return

        if scenario is not CompareScenario.GOLDEN_CROSS:
            if context.entered:
                return
            context.entered = True
            target_weight = (
                -params.allocation if scenario is CompareScenario.SHORT_HOLD else params.allocation
            )
        else:
            window = data.history(
                context.asset,
                ["open", "close"],
                # 번들 시작 이전을 요청하지 않도록 경과 세션 수로 제한한다.
                bar_count=min(context.calendar_bars, params.long_window_days * 2),
                frequency="1d",
            )
            # 거래정지 행(open NaN)은 커스텀 엔진 로더가 drop하는 행과 동일하게 제외한다.
            history = window["close"][window["open"].notna()].dropna()
            if len(history) < params.long_window_days:
                return
            history = history.tail(params.long_window_days)
            short_ma = float(history.tail(params.short_window_days).mean())
            long_ma = float(history.mean())
            target_weight = params.allocation if short_ma > long_ma else 0.0

        # 수량 규칙은 커스텀 엔진 라우터와 같은 함수를 사용한다.
        equity = context.portfolio.portfolio_value
        held = context.portfolio.positions[context.asset].amount
        delta_notional = target_weight * equity - held * close_t
        shares = int(floor_delta_shares(delta_notional, close_t))
        if shares <= 0:
            return
        if delta_notional > 0:
            order(context.asset, shares)
        elif scenario is CompareScenario.SHORT_HOLD:
            order(context.asset, -shares)  # 보유 없이 매도 = 공매도 진입
        else:
            sell_shares = min(shares, held)
            if sell_shares > 0:
                order(context.asset, -sell_shares)

    return handle_data


def run_zipline_side(
    frame: pd.DataFrame,
    scenario: CompareScenario,
    params: CompareParams,
    bundle: str,
    environ: dict[str, str],
) -> dict[date, float]:
    """준비된 번들로 Zipline을 실행해 세션 날짜 → equity 매핑을 반환한다."""
    performance = run_algorithm(
        start=pd.Timestamp(frame.index.min()),
        end=pd.Timestamp(frame.index.max()),
        # 기본값은 XNYS(뉴욕) 캘린더라 한국 휴일에 유령 세션이 생긴다.
        trading_calendar=get_calendar("XKRX"),
        initialize=make_compare_initialize(params.ticker, params.fee_bps, scenario, params),
        handle_data=make_compare_handle_data(scenario, params),
        capital_base=params.capital_base_krw,
        data_frequency="daily",
        bundle=bundle,
        default_extension=False,
        environ=environ,
    )
    return {
        timestamp.date(): float(value)
        for timestamp, value in performance["portfolio_value"].items()
    }
