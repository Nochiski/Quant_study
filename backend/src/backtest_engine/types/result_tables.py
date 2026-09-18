"""결과 집계용 columnar 테이블 계약.

`EventStore.result_tables()`가 답하는 공개 타입이다. 호출자가 결과를 다시 자기 레코드로
옮겨 담는 것이 목적일 때, `PortfolioSnapshot`·`Position`·`OrderEvent`·`FillEvent`·
`CostAccrued` 같은 공개 도메인 객체는 중간 산물일 뿐이다. 그 객체를 만들고 곧바로 버리는
비용(Decimal 변환, dataclass `__post_init__` 검사, 보유 종목 수만큼의 `Position` 생성)을
없애려고 primitive 행만 답한다.

행은 dataclass도 `NamedTuple`도 아닌 plain tuple이다 — 객체 생성 회피가 목적이므로 행마다
클래스 인스턴스를 만들면 취지가 사라진다. 원소 의미는 각 `TypeAlias`의 docstring이 정본이고,
세션·종목은 인덱스로 참조해 `sessions`/`instruments` 조회표로 푼다.

수량은 `int`다. 커널은 정수 주식 수량만 지원하며 Python 코어의 `Decimal` 수량도 정수여야
한다 (아니면 `result_tables()`가 `ValueError`).

enum은 `.value` 문자열로 나른다 (`Side`/`OrderType`/`TimeInForce`/`CostKind`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from backtest_engine.types.instruments import InstrumentId

__all__ = [
    "CostRow",
    "FillRow",
    "FillTotals",
    "OrderRow",
    "PositionRow",
    "ResultTables",
    "SnapshotRow",
]

SnapshotRow: TypeAlias = tuple[int, float, float, float, float]
"""`(session_index, cash, equity, gross_exposure, positions_value)`.

`positions_value`는 그 스냅샷의 `market_value`를 행 순서대로 왼쪽부터 더한 값이다
(0.0에서 시작하는 좌→우 결합 — Python `sum(generator)`과 bit 동일).
"""

PositionRow: TypeAlias = tuple[int, int, int, float, float, float, float]
"""`(session_index, instrument_index, quantity, average_price, market_price, market_value,
unrealized_pnl)`."""

OrderRow: TypeAlias = tuple[str, str, int, int, str, int, str, str]
"""`(order_id, decision_id, session_index, instrument_index, side, quantity, order_type,
time_in_force)`.

주문을 만든 `source_action`은 담지 않는다 — 결정 객체 복원이 필요한 호출자는 `orders()`를
쓴다.
"""

FillRow: TypeAlias = tuple[str, str, int, int, str, int, float, float, float]
"""`(fill_id, order_id, session_index, instrument_index, side, quantity, price, fee,
slippage_per_share)`."""

CostRow: TypeAlias = tuple[int, str, int | None, float]
"""`(session_index, kind, instrument_index, amount)`. 종목 없는 비용(margin interest)은
`instrument_index`가 None이다."""


@dataclass(frozen=True)
class FillTotals:
    """FILL 레코드 순서로 누산한 체결 집계.

    셋 다 0.0에서 시작하는 좌→우 결합이라 같은 순서의 Python `sum(generator)`과 bit 동일하다.
    결합 순서가 달라지면 부동소수 합이 달라져 지표가 코어별로 갈린다.
    """

    traded_notional: float
    """Σ `quantity × price`."""

    total_fees: float
    """Σ `fee`."""

    total_slippage_cost: float
    """Σ `quantity × |slippage_per_share|`."""


@dataclass(frozen=True, eq=False)
class ResultTables:
    """한 번의 실행 결과를 primitive 행으로 고정한 묶음.

    `eq=False`: 행 수가 세션×종목 규모라 `==`/`hash`를 기본 제공하면 실수로 전체 비교를
    유발한다. 동등 비교가 필요한 테스트는 필드를 직접 비교한다.
    """

    sessions: tuple[datetime, ...]
    """`session_index` → 세션 ts. feed 세션 전체를 순서대로 담는다."""

    instruments: tuple[InstrumentId, ...]
    """`instrument_index` → 종목. bar 첫 등장 순서다."""

    snapshots: tuple[SnapshotRow, ...]
    """SNAPSHOT 레코드 순서."""

    positions: tuple[PositionRow, ...]
    """스냅샷 순서 × 그 스냅샷 안의 원장 삽입 순서."""

    orders: tuple[OrderRow, ...]
    """ORDER 레코드 순서."""

    fills: tuple[FillRow, ...]
    """FILL 레코드 순서."""

    costs: tuple[CostRow, ...]
    """COST 레코드 순서."""

    fill_totals: FillTotals
