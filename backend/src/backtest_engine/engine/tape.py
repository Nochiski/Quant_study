"""선언형 tape 결정 규칙의 Python reference.

Rust `backtest_core/src/tape.rs`가 같은 규칙으로 결정을 만든다. 규칙을 바꾸면 두 곳을 함께
바꾸고 `tests/test_tape.py`의 python·rust 패리티 시나리오로 trace가 같은지 확인한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from backtest_engine.types.actions import PositionTarget, QuantityTarget
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import TapeFrame


def no_bar_reason(reason: str, symbols: Sequence[str]) -> str:
    """bar 없는 종목을 사유 문자열에 덧붙인다 — 이 포맷의 단일 진실 원천이다.

    Rust tape(`backtest_core/src/tape.rs`)는 사유와 심볼 목록만 넘기고 조립은 하지 않는다.
    trace가 byte 단위로 같아야 하므로 Python `repr(tuple)` 표기를 두 곳에서 흉내 내지 않는다.
    """
    return f"{reason} no_bar={tuple(symbols)}"


def evaluate_tape(
    frames: Mapping[date, TapeFrame],
    idle_reason: str,
    ctx: StrategyContext,
    event: StrategyEvent,
) -> StrategyDecision:
    """market 콜백이면 그 날짜의 프레임을 실행 가능한 결정으로 바꾸고, 아니면 NoAction.

    커널은 비중 목표를 그 세션 종가로 수량화하므로 bar 없는 종목(거래정지·기준가 세션)의
    WeightTarget은 라우팅을 죽인다. 보유 중이면 현재 수량으로 고정하고 미보유면 건너뛴다 —
    건너뛴 예산은 다음 프레임까지 현금에 남고, 심볼은 사유에 남아 run manifest가 추적한다.
    """
    if not isinstance(event, MarketSnapshot):
        return StrategyDecision.no_action(ctx.now, idle_reason)
    frame = frames.get(event.ts.date())
    if frame is None:
        return StrategyDecision.no_action(ctx.now, idle_reason)
    kept: list[PositionTarget] = []
    untradable: list[str] = []
    for target in frame.action.targets:
        if event.has(target.instrument):
            kept.append(target)
            continue
        untradable.append(target.instrument.symbol)
        held = ctx.position_qty(target.instrument)
        if held != 0:
            kept.append(QuantityTarget(instrument=target.instrument, quantity=held))
    reason = frame.reason
    if untradable:
        reason = no_bar_reason(reason, untradable)
    return StrategyDecision.of(ctx.now, replace(frame.action, targets=tuple(kept)), reason)
