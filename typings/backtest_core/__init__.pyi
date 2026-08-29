"""`backtest_core` (rust/backtest_core, PyO3 확장) 로컬 타입 스텁. 실 시그니처는 src/lib.rs."""

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
