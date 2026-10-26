"""S07 실행기의 S05 게이트웨이와 키움 호가 어댑터. 가짜 키움 서버로만 돈다."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import (
    KiwoomCredentials,
    KiwoomMockBroker,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.market import KiwoomMarket
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    MarketError,
    OrderRequest,
    SubmitStatus,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import OfflineOrderGateway
from strategy_workbench.domain.risk.facade.risk import SecurityFlag, ViWindow
from tests.test_adapters_kiwoom_mock import ACCOUNT, APP_KEY, SECRET, SYMBOL, Clock, FakeKiwoom

CREDENTIALS = KiwoomCredentials(APP_KEY, SECRET, ACCOUNT)


def broker(tmp_path: Path, fake: FakeKiwoom, clock: Clock, *, transmit: bool) -> KiwoomMockBroker:
    (tmp_path / "ledger").mkdir(exist_ok=True)
    return KiwoomMockBroker(
        CREDENTIALS,
        tmp_path / "ledger" / "kiwoom-reference.sqlite",
        transport=fake,
        transmit=transmit,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def gateway(
    tmp_path: Path, fake: FakeKiwoom, clock: Clock, *, transmit: bool = True
) -> OfflineOrderGateway:
    return OfflineOrderGateway(
        tmp_path / "ledger", broker(tmp_path, fake, clock, transmit=transmit)
    )


def buy(intent_id: str = "weekly-2026-10-23-005930-buy") -> OrderRequest:
    return OrderRequest(intent_id, SYMBOL, "buy", 5, 70_000, 1_400)


def test_gateway_previews_without_a_ledger_then_sends_once(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    g = gateway(tmp_path, fake, clock)
    preview = g.state()
    assert (preview.matched, preview.cash_krw, dict(preview.holdings)) == (
        True,
        10_000_000,
        {SYMBOL: 10},
    )
    assert not g.initialized() and not (tmp_path / "ledger" / "execution.sqlite").exists()
    g.initialize()
    assert g.initialized()
    assert g.submit(buy()).status is SubmitStatus.SENT
    assert g.submit(buy()).status is SubmitStatus.EXISTS
    assert fake.api_ids().count("kt10000") == 1
    fake.fill("0000001", 2, 69_900, commission=480)
    state = g.state()
    assert state.matched
    [order] = state.orders
    assert (order.state, order.filled_quantity, order.filled_value_krw, order.open_quantity) == (
        "partial",
        2,
        139_800,
        3,
    )
    assert g.cancel(order.intent_id).status is SubmitStatus.SENT
    assert g.state().orders[0].terminal
    assert {"kt00007", "ka10170", "kt00018", "kt00001"} <= set(g.evidence())
    g.close()


def test_unsettled_presend_read_maps_to_retry_without_sending(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    g = gateway(tmp_path, fake, clock)
    g.initialize()
    assert g.submit(buy("first")).status is SubmitStatus.SENT
    fake.every["kt00001"] = [lambda: fake.fill("0000001", 1, 70_000, commission=240)]
    assert g.submit(buy("second")).status is SubmitStatus.RETRY
    assert fake.api_ids().count("kt10000") == 1
    g.close()


def test_gateway_without_transmit_rejects_instead_of_sending(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    g = gateway(tmp_path, fake, clock, transmit=False)
    g.initialize()
    assert g.submit(buy()).status is SubmitStatus.REJECTED
    assert "kt10000" not in fake.api_ids()
    g.close()


def test_market_shares_the_broker_session_and_reads_book_and_price(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.quotes[SYMBOL] = ((70_100, 70_200, 70_300), (70_000, 69_900))
    fake.last_prices[SYMBOL] = 70_050
    shared = broker(tmp_path, fake, clock, transmit=False)
    shared.snapshot()
    market = KiwoomMarket(shared)
    quote = market.quote(SYMBOL)
    assert (quote.asks, quote.bids) == ((70_100, 70_200, 70_300), (70_000, 69_900))
    price = market.price(SYMBOL)
    assert (price.name, price.last_price_krw) == ("삼성전자", 70_050)
    assert not {"kt10000", "kt10001", "kt10003"} & set(fake.api_ids())
    assert fake.api_ids().count("au10001") == 1  # 토큰은 한 번만 받는다
    shared.close()


def test_market_reads_flags_price_limits_and_the_days_krx_vi(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.audit[SYMBOL] = ("투자경고", "5")
    fake.price_limits[SYMBOL] = (91_000, 49_000)
    fake.last_prices[SYMBOL] = 70_000
    fake.vi[SYMBOL] = [
        {
            "stk_cd": SYMBOL,
            "trde_cntr_proc_time": "090801",
            "virelis_time": "091031",
            "stex_tp": "KRX",
        },
        {
            "stk_cd": SYMBOL,
            "trde_cntr_proc_time": "092000",
            "virelis_time": "000000",
            "stex_tp": "KRX",
        },
        {
            "stk_cd": SYMBOL,
            "trde_cntr_proc_time": "172311",
            "virelis_time": "172511",
            "stex_tp": "NXT",
        },
        # 조회 종목이 아닌 행은 쓰지 않는다(리뷰 P2-4)
        {
            "stk_cd": "000660",
            "trde_cntr_proc_time": "091500",
            "virelis_time": "091700",
            "stex_tp": "KRX",
        },
        # 모르는 거래소 표기는 KRX 로 본다(빼지 않는 쪽)
        {
            "stk_cd": SYMBOL,
            "trde_cntr_proc_time": "093000",
            "virelis_time": "093200",
            "stex_tp": "1",
        },
    ]
    shared = broker(tmp_path, fake, clock, transmit=False)
    status = KiwoomMarket(shared).trade_status(SYMBOL)
    assert status.flags == {SecurityFlag.INVESTMENT_WARNING}
    assert (status.upper_limit_krw, status.lower_limit_krw) == (91_000, 49_000)
    # NXT·다른 종목 행은 빼고, 해제 시각 000000 은 아직 모름(None)
    assert status.vi == (
        ViWindow(time(9, 8, 1), time(9, 10, 31)),
        ViWindow(time(9, 20), None),
        ViWindow(time(9, 30), time(9, 32)),
    )
    [vi_call] = [call for call in fake.calls if call.api_id == "ka10054"]
    assert (vi_call.body["stk_cd"], vi_call.body["stex_tp"]) == (SYMBOL, "1")
    assert not {"kt10000", "kt10001", "kt10003"} & set(fake.api_ids())
    shared.close()


@pytest.mark.parametrize(
    ("audit", "warning", "state", "flags"),
    [
        ("정상", "0", "증거금40%|담보대출|신용가능", set()),
        (
            "관리종목|투자주의",
            "2",
            "증거금100%",
            {SecurityFlag.ADMIN, SecurityFlag.CAUTION, SecurityFlag.LIQUIDATION},
        ),
        # 관리종목은 감리구분이 정상이어도 종목상태 토큰에만 있을 수 있다
        # (COLLECT_PLAN 게이트 E2, 리뷰 P2-3)
        ("정상", "0", "증거금100%|관리종목", {SecurityFlag.ADMIN}),
        ("거래정지", "4", "증거금100%", {SecurityFlag.HALTED, SecurityFlag.INVESTMENT_DANGER}),
        ("단기과열", "3", "", {SecurityFlag.OVERHEATED}),
        ("처음 보는 값", "0", "", {SecurityFlag.UNKNOWN}),
        ("정상", "", "", {SecurityFlag.UNKNOWN}),
        ("정상", "9", "", {SecurityFlag.UNKNOWN}),
    ],
)
def test_kiwoom_status_words_map_to_flags_and_unknown_words_block(
    tmp_path: Path, audit: str, warning: str, state: str, flags: set[SecurityFlag]
) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.audit[SYMBOL] = (audit, warning)
    fake.state[SYMBOL] = state
    fake.price_limits[SYMBOL] = (91_000, 49_000)
    fake.last_prices[SYMBOL] = 70_000
    shared = broker(tmp_path, fake, clock, transmit=False)
    assert KiwoomMarket(shared).trade_status(SYMBOL).flags == flags
    shared.close()


def test_missing_price_limits_are_unreadable(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.last_prices[SYMBOL] = 70_000  # 상하한가 0
    shared = broker(tmp_path, fake, clock, transmit=False)
    with pytest.raises(MarketError, match="price limits"):
        KiwoomMarket(shared).trade_status(SYMBOL)
    shared.close()


def test_rate_limited_query_is_retried_once_after_the_interval(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.rate_limited = {"au10001", "kt00001"}
    snapshot = broker(tmp_path, fake, clock, transmit=False).snapshot()
    assert snapshot.account.cash_krw == 10_000_000
    for api_id in ("au10001", "kt00001"):
        calls = [call.at for call in fake.calls if call.api_id == api_id]
        assert len(calls) == 2 and calls[1] - calls[0] >= 1.1 - 1e-9, api_id
