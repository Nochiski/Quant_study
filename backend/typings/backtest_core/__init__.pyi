"""`backtest_core` PyO3 확장 로컬 타입 스텁. 구현은 backend/rust/backtest_core/src/lib.rs."""

__version__: str

def execution_price(
    order_type: str,
    side: str,
    open: float,
    high: float,
    low: float,
    limit_price: float | None = None,
    stop_price: float | None = None,
    already_triggered: bool = False,
) -> tuple[float | None, bool]: ...
def floor_delta_shares(delta_notional: float, reference_price: float) -> int: ...

class Portfolio:
    def __init__(
        self, initial_cash: float, allow_short: bool = False, allow_margin: bool = False
    ) -> None: ...
    def apply(self, key: str, side: str, quantity: int, price: float, fee: float) -> None: ...
    def charge(self, amount: float) -> None: ...
    def apply_corporate_action(
        self,
        key: str,
        new_quantity: int,
        new_average_price: float,
        cash_paid: float,
        settlement_price: float,
    ) -> None: ...
    def mark(self, closes: list[tuple[str, float]]) -> None: ...
    @property
    def cash(self) -> float: ...
    def held_qty(self, key: str) -> int: ...
    def average_price(self, key: str) -> float | None: ...
    def snapshot(
        self,
    ) -> tuple[float, list[tuple[str, int, float, float, float, float]], float, float]: ...

def liquidity_cap(volume: int, participation: str) -> int: ...
def quote_numbers(
    side: str,
    base_price: float,
    remaining: int,
    volume: int,
    participation: str | None,
    slip: float,
    limit_price: float | None,
    buying_power: float,
    held: int,
    fee_rate: float,
    fok: bool,
) -> tuple[float, int, float, str]: ...

class BuyingPower:
    def __init__(
        self, equity: float, leverage: float, positions: list[tuple[str, int, float, float]]
    ) -> None: ...
    @property
    def available(self) -> float: ...
    def quantity_of(self, key: str) -> int: ...
    def consume(self, key: str, side: str, quantity: int, price: float, fee: float) -> None: ...
    def checkpoint(self) -> tuple[float, float, list[tuple[str, int]], list[tuple[str, float]]]: ...
    def restore(
        self, state: tuple[float, float, list[tuple[str, int]], list[tuple[str, float]]]
    ) -> None: ...

def process_market(
    ts: str,
    entries: list[
        tuple[
            str,
            str,
            str,
            str,
            str,
            tuple[float | None, float | None, str | None, str | None],
            str,
            int,
            bool,
            str | None,
            str | None,
        ]
    ],
    groups: list[tuple[str, str, list[str]]],
    bars: dict[str, tuple[float, float, float, int]],
    power: BuyingPower,
    fee_rate: float,
    default_participation: str | None,
    slippage: tuple[str, float, float],
) -> list[tuple[str, str, int, float, float, float, str]]: ...
