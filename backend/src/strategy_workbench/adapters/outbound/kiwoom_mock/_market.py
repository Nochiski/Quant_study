"""키움 모의 호가·현재가(S07 실행기의 `MarketSource`). 주문하지 않는다.

- 호가: `ka10004` 주식호가. 매도·매수 1~10호가 가운데 0이 아닌 칸만 1호가부터 쓴다.
- 현재가·종목명: `ka10001` 주식기본정보의 `cur_prc`·`stk_nm`. 장 마감 뒤에는 그날 종가다.
"""

from __future__ import annotations

from strategy_workbench.adapters.outbound.kiwoom_mock._broker import KiwoomMockBroker
from strategy_workbench.adapters.outbound.kiwoom_mock._rows import ContractViolation, number, text
from strategy_workbench.application.offline_execution.facade.execution import BrokerError
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    MarketError,
    PriceInfo,
)
from strategy_workbench.domain.rebalance.facade.rebalance import Quote

_LEVELS = ("fpr", *(f"{index}th_pre" for index in range(2, 11)))


class KiwoomMarket:
    """주문 어댑터와 같은 접속(토큰·호출 간격)을 쓴다. 따로 접속하면 1초 안에 토큰을 두 번 받아
    호출 한도(1700)에 걸린다(2026-10-21 실측)."""

    def __init__(self, broker: KiwoomMockBroker) -> None:
        self._client = broker.client

    def quote(self, symbol: str) -> Quote:
        body = self._call("ka10004", "/api/dostk/mrkcond", symbol)
        return Quote(symbol, _book(body, "sel"), _book(body, "buy"))

    def price(self, symbol: str) -> PriceInfo:
        body = self._call("ka10001", "/api/dostk/stkinfo", symbol)
        try:
            last = abs(number(body, "cur_prc"))
        except ContractViolation as error:
            raise MarketError(f"unreadable price — symbol={symbol} error={error}") from error
        if last <= 0:
            raise MarketError(f"no current price — symbol={symbol}")
        return PriceInfo(symbol, text(body, "stk_nm"), last)

    def _call(self, api_id: str, path: str, symbol: str) -> dict[str, object]:
        try:
            return self._client.call(api_id, path, {"stk_cd": symbol}).body
        except BrokerError as error:
            raise MarketError(f"kiwoom {api_id} failed — symbol={symbol} error={error}") from error


def _book(body: dict[str, object], side: str) -> tuple[int, ...]:
    prices: list[int] = []
    for level in _LEVELS:
        try:
            price = abs(number(body, f"{side}_{level}_bid"))
        except ContractViolation as error:
            raise MarketError(f"unreadable quote — field={side}_{level}_bid") from error
        if price == 0:
            break
        prices.append(price)
    return tuple(prices)
