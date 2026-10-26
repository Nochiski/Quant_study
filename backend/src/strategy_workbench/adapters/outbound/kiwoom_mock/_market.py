"""키움 모의 호가·현재가·종목 상태(S07 실행기의 `MarketSource`). 주문하지 않는다.

- 호가: `ka10004` 주식호가. 매도·매수 1~10호가 가운데 0이 아닌 칸만 1호가부터 쓴다.
- 현재가·종목명: `ka10001` 주식기본정보의 `cur_prc`·`stk_nm`. 장 마감 뒤에는 그날 종가다.
- 종목 상태(리스크 가드의 주문 직전 판정, 2026-10-26 모의 실측으로 세 TR 모두 답함):
  - 표시: `ka10100` 종목정보의 `auditInfo`(감리구분: 정상·관리종목·거래정지·단기과열·투자주의·
    투자경고, `|`로 여럿)와 `orderWarning`(투자유의: 0 없음·1 ETF 주의·2 정리매매·3 단기과열·
    4 투자위험·5 투자경고). 모르는 값·빈 값은 `UNKNOWN`이다(리스크가 매수를 막는 쪽). 관리종목은
    종목상태 `state`의 `|` 토큰에도 본다 — 감리구분만 보면 관리종목의 47%를 놓친다(원장
    `database/docs/COLLECT_PLAN.md` 게이트 E2, `stage/rules_kiwoom.py`의 `is_admin_issue`). 그 밖의
    `state` 토큰(증거금·담보대출·신용가능)은 표시가 아니다.
  - 상하한가: `ka10001`의 `upl_pric`·`lst_pric`(부호를 뗀다). 0이면 읽지 못한 것이다.
  - VI: `ka10054` 그 종목의 그날 발동 행. 응답 행의 종목이 조회 종목인 것만 쓰고, 거래소가 NXT 인
    행만 뺀다(모르는 표기는 KRX 로 본다). 발동 `trde_cntr_proc_time`, 해제 `virelis_time`
    (`000000`이면 아직 모름) HHmmss. 범위 칸을 비우면 1511로 거절해 넓은 범위를 채운다.
    2026-10-26 모의 실측: 종목을 주면 그 종목 행만 오고 `stex_tp`는 `KRX`, 안 풀린 VI 는
    `000000`이다.
"""

from __future__ import annotations

from datetime import time

from strategy_workbench.adapters.outbound.kiwoom_mock._broker import KiwoomMockBroker
from strategy_workbench.adapters.outbound.kiwoom_mock._rows import (
    ContractViolation,
    number,
    text,
)
from strategy_workbench.adapters.outbound.kiwoom_mock._rows import symbol as kiwoom_symbol
from strategy_workbench.application.offline_execution.facade.execution import BrokerError
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    MarketError,
    PriceInfo,
)
from strategy_workbench.domain.rebalance.facade.rebalance import Quote
from strategy_workbench.domain.risk.facade.risk import SecurityFlag, TradeStatus, ViWindow

_LEVELS = ("fpr", *(f"{index}th_pre" for index in range(2, 11)))
_AUDIT_FLAGS = {
    "관리종목": SecurityFlag.ADMIN,
    "거래정지": SecurityFlag.HALTED,
    "단기과열": SecurityFlag.OVERHEATED,
    "투자주의": SecurityFlag.CAUTION,
    "투자경고": SecurityFlag.INVESTMENT_WARNING,
    "투자위험": SecurityFlag.INVESTMENT_DANGER,
}
# 종목상태(`state`) 토큰 가운데 표시인 것
_STATE_FLAGS = {"관리종목": SecurityFlag.ADMIN, "거래정지": SecurityFlag.HALTED}
_WARNING_FLAGS = {
    "1": SecurityFlag.CAUTION,
    "2": SecurityFlag.LIQUIDATION,
    "3": SecurityFlag.OVERHEATED,
    "4": SecurityFlag.INVESTMENT_DANGER,
    "5": SecurityFlag.INVESTMENT_WARNING,
}
# 그 종목 하나, 전체 시장·발동 구분·방향, KRX. 쓰지 않는 거래량·거래대금 범위도 채워야 한다
_VI_QUERY = {
    "mrkt_tp": "000",
    "bf_mkrt_tp": "0",
    "motn_tp": "0",
    "skip_stk": "000000000",
    "trde_qty_tp": "0",
    "min_trde_qty": "0",
    "max_trde_qty": "100000000",
    "trde_prica_tp": "0",
    "min_trde_prica": "0",
    "max_trde_prica": "100000000",
    "motn_drc": "0",
    "stex_tp": "1",
}


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

    def trade_status(self, symbol: str) -> TradeStatus:
        info = self._call("ka10100", "/api/dostk/stkinfo", symbol)
        basic = self._call("ka10001", "/api/dostk/stkinfo", symbol)
        vi = self._call("ka10054", "/api/dostk/stkinfo", symbol, _VI_QUERY)
        try:
            upper, lower = abs(number(basic, "upl_pric")), abs(number(basic, "lst_pric"))
            windows = tuple(
                _vi_window(row)
                for row in _rows(vi, "motn_stk")
                # 거래소 접미사(`_NX`·`_AL`)·접두어를 떼고 조회 종목과 견준다
                if kiwoom_symbol(text(row, "stk_cd").split("_", 1)[0]) == symbol
                and text(row, "stex_tp") != "NXT"
            )
        except (ContractViolation, ValueError) as error:
            raise MarketError(f"unreadable status — symbol={symbol} error={error}") from error
        if upper <= 0 or lower <= 0:
            raise MarketError(
                f"no price limits — symbol={symbol} upl_pric={basic.get('upl_pric')!r} "
                f"lst_pric={basic.get('lst_pric')!r}"
            )
        return TradeStatus(symbol, _flags(info), upper, lower, windows)

    def _call(
        self,
        api_id: str,
        path: str,
        symbol: str,
        extra: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            return self._client.call(api_id, path, {**(extra or {}), "stk_cd": symbol}).body
        except BrokerError as error:
            raise MarketError(f"kiwoom {api_id} failed — symbol={symbol} error={error}") from error


def _flags(info: dict[str, object]) -> frozenset[SecurityFlag]:
    flags: set[SecurityFlag] = set()
    for word in text(info, "auditInfo").split("|"):
        word = word.strip()
        if word != "정상":
            flags.add(_AUDIT_FLAGS.get(word, SecurityFlag.UNKNOWN))
    warning = text(info, "orderWarning")
    if warning != "0":
        flags.add(_WARNING_FLAGS.get(warning, SecurityFlag.UNKNOWN))
    flags |= {
        _STATE_FLAGS[token.strip()]
        for token in text(info, "state").split("|")
        if token.strip() in _STATE_FLAGS
    }
    return frozenset(flags)


def _rows(body: dict[str, object], key: str) -> list[dict[str, object]]:
    rows = body.get(key) or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ContractViolation(f"kiwoom list field is not a list of objects — key={key}")
    return rows


def _vi_window(row: dict[str, object]) -> ViWindow:
    release = text(row, "virelis_time")
    return ViWindow(
        _hhmmss(text(row, "trde_cntr_proc_time")),
        None if release in ("", "000000") else _hhmmss(release),
    )


def _hhmmss(value: str) -> time:
    if len(value) != 6 or not value.isdigit():
        raise ContractViolation(f"kiwoom time is not HHmmss — value={value!r}")
    return time(int(value[:2]), int(value[2:4]), int(value[4:]))


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
