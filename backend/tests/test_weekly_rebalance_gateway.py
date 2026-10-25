"""S07 실행기의 S05 게이트웨이와 키움 호가 어댑터. 가짜 키움 서버로만 돈다."""

from __future__ import annotations

from pathlib import Path

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import (
    KiwoomCredentials,
    KiwoomMockBroker,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.market import KiwoomMarket
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    OrderRequest,
    SubmitStatus,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import OfflineOrderGateway
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


def test_rate_limited_query_is_retried_once_after_the_interval(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    fake.rate_limited = {"au10001", "kt00001"}
    snapshot = broker(tmp_path, fake, clock, transmit=False).snapshot()
    assert snapshot.account.cash_krw == 10_000_000
    for api_id in ("au10001", "kt00001"):
        calls = [call.at for call in fake.calls if call.api_id == api_id]
        assert len(calls) == 2 and calls[1] - calls[0] >= 1.1 - 1e-9, api_id
