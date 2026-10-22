"""키움 모의 어댑터를 공식 명세 예시 모양의 가짜 서버로 검증한다. 네트워크를 쓰지 않는다."""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import (
    MOCK_BASE_URL,
    KiwoomBlock,
    KiwoomBlockedError,
    KiwoomCredentials,
    KiwoomHttpResponse,
    KiwoomMockBroker,
    KiwoomNoResponseError,
    UrllibTransport,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.probe import KiwoomConnectionProbe
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    ConnectionOutcome,
)
from strategy_workbench.application.offline_execution.facade.execution import (
    BrokerError,
    ExecutionStatus,
    OfflineExecutionService,
)
from strategy_workbench.domain.order_execution.facade.orders import Intent, OrderFact, OrderState

KST = timezone(timedelta(hours=9))
# 모두 가짜 값이다. 실제 키·계좌가 아니다.
APP_KEY = "fake-app-key-0123456789"
SECRET = "fake-secret-0123456789"
TOKEN = "fake-token-0123456789"
ACCOUNT = "5012345678"
SYMBOL = "005930"


def pad(value: int, width: int = 10) -> str:
    return f"-{abs(value):0{width - 1}d}" if value < 0 else f"{value:0{width}d}"


@dataclass
class Clock:
    now_value: datetime = field(default_factory=lambda: datetime(2026, 10, 21, 9, 30, tzinfo=KST))
    mono: float = 0.0

    def now(self) -> datetime:
        return self.now_value

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.mono += seconds
        self.now_value += timedelta(seconds=seconds)


@dataclass
class Call:
    api_id: str
    body: dict[str, object]
    headers: dict[str, str]
    at: float


class FakeKiwoom:
    """키움 모의 서버 대역. 필드 모양은 공식 명세, 표기 값은 2026-10-20·21 모의 실측을 따른다.

    실측 표기는 명세 예시와 다르다: REST 주문의 통신구분은 "모의투자", 지정가 매매구분은 "보통가",
    접수구분은 "주문완료", 원주문의 정정취소는 "일반", 취소 행은 같은 주문구분에 "취소"다.
    `kt00007` 체결단가는 평균을 원 단위로 반올림한 값이고, `ka10170` 당일매매일지는 종목별
    금액·비용을 원 단위로 정확히 준다.
    """

    def __init__(self, clock: Clock, *, account: str = ACCOUNT) -> None:
        self.clock = clock
        self.account = account
        self.token = TOKEN
        self.token_error: str | None = None
        self.order_mode = "accept"
        self.confirm_cancel = True
        self.page_size: int | None = None
        self.cash = 10_000_000
        self.holdings: dict[str, int] = {SYMBOL: 10}
        self.rows: list[dict[str, str]] = []
        self.past: dict[str, list[dict[str, str]]] = {}
        self.journal: dict[str, dict[str, int]] = {}
        self.journal_lag: list[tuple[str, str, int, int, int]] = []
        self.quotes: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
        self.rate_limited: set[str] = set()  # 다음 한 번만 호출 한도(1700)로 답할 TR
        self.last_prices: dict[str, int] = {}
        self.calls: list[Call] = []
        self.after: dict[str, list[Callable[[], None]]] = {}  # 한 번만 돈다
        self.every: dict[str, list[Callable[[], None]]] = {}  # 호출마다 돈다
        self.echo_secret_in_errors = False
        self.unreachable = False  # 연결 자체가 실패한다(인증서·DNS 등)
        self._next_ord_no = 1
        self._values: dict[str, int] = {}

    # --- 전송 ---------------------------------------------------------------------------
    def post(
        self, path: str, headers: Mapping[str, str], body: Mapping[str, object]
    ) -> KiwoomHttpResponse:
        api_id = headers["api-id"]
        self.calls.append(Call(api_id, dict(body), dict(headers), self.clock.mono))
        if self.unreachable:
            raise KiwoomNoResponseError(f"no response from kiwoom mock — path={path} unreachable")
        if api_id in self.rate_limited:
            self.rate_limited.discard(api_id)
            response = self._error("1700", "허용된 API 요청 개수를 초과하였습니다")
        elif api_id != "au10001" and headers.get("authorization") != f"Bearer {self.token}":
            response = self._error("8005", "Token이 유효하지 않습니다")
        else:
            response = getattr(self, f"_{api_id}")(dict(body), dict(headers))
        for hook in [*self.after.pop(api_id, []), *self.every.get(api_id, [])]:
            hook()
        return response

    def api_ids(self) -> list[str]:
        return [call.api_id for call in self.calls]

    def _ok(
        self, body: dict[str, object], continuation: str = "", key: str = ""
    ) -> KiwoomHttpResponse:
        return KiwoomHttpResponse(
            200,
            {**body, "return_code": 0, "return_msg": "정상적으로 처리되었습니다"},
            continuation,
            key,
        )

    def _error(self, code: str, message: str) -> KiwoomHttpResponse:
        suffix = f" {APP_KEY} {SECRET} {ACCOUNT}" if self.echo_secret_in_errors else ""
        return KiwoomHttpResponse(
            200, {"return_code": 3, "return_msg": f"실패했습니다[{code}:{message}]{suffix}"}
        )

    def _au10001(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        assert body == {
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "secretkey": SECRET,
        }
        if self.token_error:
            return self._error(self.token_error, "토큰 발급 실패")
        expires = (self.clock.now_value + timedelta(hours=24)).strftime("%Y%m%d%H%M%S")
        return self._ok({"expires_dt": expires, "token_type": "bearer", "token": self.token})

    def _ka00001(self, _: dict[str, object], __: dict[str, str]) -> KiwoomHttpResponse:
        return self._ok({"acctNo": self.account})

    def _kt00007(self, body: dict[str, object], headers: dict[str, str]) -> KiwoomHttpResponse:
        rows = self.rows if body["ord_dt"] == "" else self.past.get(str(body["ord_dt"]), [])
        return self._page(rows, "acnt_ord_cntr_prps_dtl", headers)

    def _ka10170(self, _: dict[str, object], headers: dict[str, str]) -> KiwoomHttpResponse:
        rows = [
            {"stk_cd": code, **{key: str(value) for key, value in totals.items()}}
            for code, totals in sorted(self.journal.items())
        ]
        return self._page(rows, "tdy_trde_diary", headers)

    def _ka10004(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        asks, bids = self.quotes.get(str(body["stk_cd"]), ((), ()))
        levels = ["fpr", *(f"{index}th_pre" for index in range(2, 11))]
        values: dict[str, object] = {}
        for index, level in enumerate(levels):
            # 실측처럼 매도 호가는 부호가 붙어 올 수 있다
            values[f"sel_{level}_bid"] = f"-{asks[index]}" if index < len(asks) else "0"
            values[f"buy_{level}_bid"] = str(bids[index]) if index < len(bids) else "0"
        return self._ok(values)

    def _ka10001(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        code = str(body["stk_cd"])
        return self._ok(
            {"stk_cd": code, "stk_nm": "삼성전자", "cur_prc": f"+{self.last_prices[code]}"}
        )

    def _kt00018(self, _: dict[str, object], headers: dict[str, str]) -> KiwoomHttpResponse:
        rows = [
            {"stk_cd": f"A{code}", "rmnd_qty": pad(quantity, 15), "crd_tp": "00"}
            for code, quantity in sorted(self.holdings.items())
        ]
        return self._page(rows, "acnt_evlt_remn_indv_tot", headers)

    def _kt00001(self, _: dict[str, object], __: dict[str, str]) -> KiwoomHttpResponse:
        return self._ok({"entr": pad(self.cash, 15), "d2_entra": pad(self.cash, 15)})

    def _page(
        self, rows: list[dict[str, str]], key: str, headers: dict[str, str]
    ) -> KiwoomHttpResponse:
        if self.page_size is None:
            return self._ok({key: [dict(row) for row in rows]})
        start = int(headers.get("next-key") or 0)
        end = start + self.page_size
        more = end < len(rows)
        page = [dict(row) for row in rows[start:end]]
        return self._ok({key: page}, "Y" if more else "N", str(end) if more else "")

    def _order(self, body: dict[str, object], io_name: str) -> KiwoomHttpResponse:
        if self.order_mode == "error":
            return self._error("1902", "종목 정보가 없습니다")
        if self.order_mode == "drop":
            raise KiwoomNoResponseError("injected request loss")
        ord_no = self.add_row(
            str(body["stk_cd"]),
            io_name,
            int(str(body["ord_qty"])),
            int(str(body["ord_uv"])),
            channel="모의투자",
        )
        if self.order_mode == "lose_twice":
            self.add_row(
                str(body["stk_cd"]),
                io_name,
                int(str(body["ord_qty"])),
                int(str(body["ord_uv"])),
                channel="모의투자",
            )
        if self.order_mode in ("lose", "lose_twice"):
            raise KiwoomNoResponseError("injected ACK loss after acceptance")
        return self._ok({"ord_no": ord_no.lstrip("0") or "0", "dmst_stex_tp": "KRX"})

    def _kt10000(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        return self._order(body, "현금매수")

    def _kt10001(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        return self._order(body, "현금매도")

    def _kt10003(self, body: dict[str, object], _: dict[str, str]) -> KiwoomHttpResponse:
        original = self.row(str(body["orig_ord_no"]))
        remaining = int(original["ord_remnq"])
        ord_no = self.add_row(
            str(body["stk_cd"]), original["io_tp_nm"], remaining, 0, channel="모의투자"
        )
        cancel = self.row(ord_no)
        cancel["ori_ord"] = original["ord_no"]
        cancel["mdfy_cncl"] = ""
        if self.confirm_cancel:
            cancel["mdfy_cncl"] = "취소"
            cancel["cnfm_qty"] = pad(remaining)
            original["ord_remnq"] = pad(0)
        return self._ok(
            {
                "ord_no": ord_no,
                "base_orig_ord_no": original["ord_no"],
                "cncl_qty": pad(remaining, 12),
            }
        )

    # --- 장면 만들기 ----------------------------------------------------------------------
    def add_row(self, code: str, io_name: str, quantity: int, price: int, *, channel: str) -> str:
        ord_no = f"{self._next_ord_no:07d}"
        self._next_ord_no += 1
        self.rows.append(
            {
                "ord_no": ord_no,
                "stk_cd": f"A{code}",
                "trde_tp": "보통가",
                "crd_tp": "보통매매",
                "ord_qty": pad(quantity),
                "ord_uv": pad(price),
                "cnfm_qty": pad(0),
                "acpt_tp": "주문완료",
                "rsrv_tp": "",
                "ord_tm": self.clock.now_value.strftime("%H:%M:%S"),
                "ori_ord": "0000000",
                "stk_nm": "삼성전자",
                "io_tp_nm": io_name,
                "loan_dt": "",
                "cntr_qty": pad(0),
                "cntr_uv": pad(0),
                "ord_remnq": pad(quantity),
                "comm_ord_tp": channel,
                "mdfy_cncl": "일반",
                "cnfm_tm": "",
                "dmst_stex_tp": "KRX",
                "cond_uv": pad(0),
            }
        )
        return ord_no

    def manual_order(self, quantity: int = 1, price: int = 70_000) -> str:
        return self.add_row(SYMBOL, "현금매수", quantity, price, channel="영웅문4")

    def row(self, ord_no: str) -> dict[str, str]:
        return next(row for row in self.rows if row["ord_no"] == ord_no)

    def fill(
        self,
        ord_no: str,
        quantity: int,
        price: int,
        *,
        commission: int = 0,
        tax: int = 0,
        journal: bool = True,
    ) -> None:
        row = self.row(ord_no)
        filled = int(row["cntr_qty"]) + quantity
        value = self._values.get(ord_no, 0) + quantity * price
        self._values[ord_no] = value
        row["cntr_qty"] = pad(filled)
        row["cntr_uv"] = pad((2 * value + filled) // (2 * filled))  # 원 단위 반올림 평균
        row["ord_remnq"] = pad(int(row["ord_remnq"]) - quantity)
        buy = "매수" in row["io_tp_nm"]
        code = row["stk_cd"][1:]
        self.holdings[code] = self.holdings.get(code, 0) + (quantity if buy else -quantity)
        self.cash += (-quantity * price if buy else quantity * price) - commission - tax
        entry = (code, "buy" if buy else "sell", quantity, quantity * price, commission + tax)
        if journal:
            self._journal(*entry)
        else:
            self.journal_lag.append(entry)

    def catch_up_journal(self) -> None:
        for entry in self.journal_lag:
            self._journal(*entry)
        self.journal_lag = []

    def _journal(self, code: str, side: str, quantity: int, value: int, costs: int) -> None:
        totals = self.journal.setdefault(
            code,
            {"buy_qty": 0, "buy_amt": 0, "sell_qty": 0, "sell_amt": 0, "cmsn_alm_tax": 0},
        )
        totals[f"{side}_qty"] += quantity
        totals[f"{side}_amt"] += value
        totals["cmsn_alm_tax"] += costs

    def roll_day(self) -> None:
        date = self.clock.now_value.strftime("%Y%m%d")
        self.past[date] = self.rows
        self.rows = []
        self.journal = {}
        self.clock.now_value = (self.clock.now_value + timedelta(days=1)).replace(hour=8, minute=0)


@dataclass
class Rig:
    clock: Clock
    fake: FakeKiwoom
    broker: KiwoomMockBroker
    ledger: ExecutionSqliteLedger
    service: OfflineExecutionService


def rig(tmp_path: Path, *, transmit: bool = True, account: str = ACCOUNT) -> Rig:
    clock = Clock()
    fake = FakeKiwoom(clock)
    broker = make_broker(tmp_path, clock, fake, transmit=transmit, account=account)
    ledger = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    return Rig(clock, fake, broker, ledger, OfflineExecutionService(ledger, broker))


def make_broker(
    tmp_path: Path, clock: Clock, fake: FakeKiwoom, *, transmit: bool, account: str = ACCOUNT
) -> KiwoomMockBroker:
    factory = partial(
        KiwoomMockBroker,
        KiwoomCredentials(APP_KEY, SECRET, account),
        tmp_path / "kiwoom.sqlite",
        transport=fake,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    # 미전송은 인자를 생략해 기본값으로 시험한다.
    return factory(transmit=True) if transmit else factory()


def buy(identity: str = "signal-1", quantity: int = 5, price: int = 70_000) -> Intent:
    return Intent(identity, SYMBOL, "buy", quantity, price, 1_000)


def fact_of(ledger: ExecutionSqliteLedger, intent_id: str = "signal-1") -> OrderFact:
    return next(f for f in ledger.orders() if f.intent.intent_id == intent_id)


def started(tmp_path: Path, *, transmit: bool = True) -> Rig:
    r = rig(tmp_path, transmit=transmit)
    assert r.service.initialize().status == ExecutionStatus.OK
    return r


# --- 미전송 기본값과 연결 차단 -------------------------------------------------------------


def test_default_is_dry_run_and_refusal_reconciles(tmp_path: Path) -> None:
    r = started(tmp_path, transmit=False)
    result = r.service.submit(buy())
    assert result.status == ExecutionStatus.OK
    assert result.order is not None and result.order.state == OrderState.REJECTED
    assert "kt10000" not in r.fake.api_ids()
    assert r.service.recover().status == ExecutionStatus.OK
    assert fact_of(r.ledger).state == OrderState.REJECTED


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("8030", KiwoomBlock.MODE_MISMATCH),
        ("8050", KiwoomBlock.IP_NOT_ALLOWED),
        ("8001", KiwoomBlock.AUTH_FAILED),
    ],
)
def test_connection_errors_block_before_any_order(
    tmp_path: Path, code: str, reason: KiwoomBlock
) -> None:
    r = rig(tmp_path)
    r.fake.token_error = code
    with pytest.raises(KiwoomBlockedError) as raised:
        r.broker.snapshot()
    assert raised.value.reason == reason
    assert r.service.initialize().status == ExecutionStatus.ERROR
    assert r.broker.submit(buy()).state == OrderState.REJECTED
    assert set(r.fake.api_ids()) == {"au10001"}


def test_a_transport_failure_is_logged_with_the_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # 리뷰 #594 P2: 호출 로그가 전송 뒤에만 있어 연결 자체가 실패하면 한 줄도 남지 않았다
    caplog.set_level(logging.INFO)
    r = rig(tmp_path)
    r.fake.unreachable = True
    with pytest.raises(KiwoomNoResponseError):
        r.broker.snapshot()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "kiwoom mock call — api_id=au10001 path=/oauth2/token" in logged
    assert "kiwoom mock call failed — api_id=au10001" in logged and "unreachable" in logged


def test_account_mismatch_blocks_before_reading_orders(tmp_path: Path) -> None:
    r = rig(tmp_path)
    r.fake.account = "5099999999"
    with pytest.raises(KiwoomBlockedError) as raised:
        r.broker.snapshot()
    assert raised.value.reason == KiwoomBlock.ACCOUNT_MISMATCH
    assert set(r.fake.api_ids()) == {"au10001", "ka00001"}


def test_reference_ledger_is_bound_to_one_account(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.broker.close()
    other = FakeKiwoom(r.clock, account="5099999999")
    broker = make_broker(tmp_path, r.clock, other, transmit=True, account="5099999999")
    with pytest.raises(KiwoomBlockedError) as raised:
        broker.snapshot()
    assert raised.value.reason == KiwoomBlock.ACCOUNT_MISMATCH


def test_a_broker_for_another_account_writes_nothing_to_the_ledger(tmp_path: Path) -> None:
    # 리뷰 #593 P2: 계좌 A 원장에 계좌 B 브로커가 거부 기록을 남겨 A 의 대사가 영구히 멈췄다
    r = started(tmp_path)
    r.broker.close()
    other = FakeKiwoom(r.clock, account="5099999999")
    for transmit in (True, False):
        broker = make_broker(tmp_path, r.clock, other, transmit=transmit, account="5099999999")
        with pytest.raises(KiwoomBlockedError) as raised:
            broker.submit(buy())
        assert raised.value.reason == KiwoomBlock.ACCOUNT_MISMATCH
        broker.close()
    mine = make_broker(tmp_path, r.clock, r.fake, transmit=True)
    assert mine.snapshot().orders == ()


def test_symbol_outside_krx_code_format_is_refused(tmp_path: Path) -> None:
    r = started(tmp_path)
    result = r.service.submit(Intent("odd", "SAMSUNG", "buy", 1, 70_000))
    assert result.order is not None and result.order.state == OrderState.REJECTED
    assert "kt10000" not in r.fake.api_ids()


# --- 정상 주문 수명 -------------------------------------------------------------------------


def test_a_second_order_on_a_symbol_with_an_unfinished_order_is_refused_before_sending(
    tmp_path: Path,
) -> None:
    # 리뷰 #596 P2: 같은 종목 미종결 주문 옆에 둘째 주문이 체결되면 금액을 나눌 수 없어 계좌가 정지됐다
    r = started(tmp_path)
    assert r.service.submit(buy()).status == ExecutionStatus.OK
    r.fake.fill("0000001", 2, 70_000, commission=21)
    second = r.service.submit(buy("signal-2", quantity=3))
    assert second.order is not None and second.order.state == OrderState.REJECTED
    assert r.fake.api_ids().count("kt10000") == 1
    # 앞 주문이 끝나면(취소 확정) 같은 종목에 다시 낼 수 있다
    assert r.service.cancel("signal-1").status == ExecutionStatus.OK
    third = r.service.submit(buy("signal-3", quantity=3))
    assert third.order is not None and third.order.state == OrderState.OPEN
    assert r.fake.api_ids().count("kt10000") == 2


def test_order_lifecycle_partial_fill_then_cancel(tmp_path: Path) -> None:
    r = started(tmp_path)
    result = r.service.submit(buy())
    assert result.status == ExecutionStatus.OK
    assert result.order == OrderFact(buy(), OrderState.OPEN, revision=1)
    order_call = next(call for call in r.fake.calls if call.api_id == "kt10000")
    assert order_call.body == {
        "dmst_stex_tp": "KRX",
        "stk_cd": SYMBOL,
        "ord_qty": "5",
        "ord_uv": "70000",
        "trde_tp": "0",
        "cond_uv": "",
    }
    assert r.service.recover().status == ExecutionStatus.OK
    assert fact_of(r.ledger).revision == 1

    r.fake.fill("0000001", 2, 70_000, commission=21)
    assert r.service.recover().status == ExecutionStatus.OK
    partial = fact_of(r.ledger)
    assert (partial.state, partial.revision, partial.filled_quantity) == (OrderState.PARTIAL, 2, 2)
    assert (partial.filled_value_krw, partial.fees_krw) == (140_000, 21)
    assert r.service.recover().status == ExecutionStatus.OK
    assert fact_of(r.ledger).revision == 2

    cancelled = r.service.cancel("signal-1")
    assert cancelled.status == ExecutionStatus.OK
    assert cancelled.order is not None
    assert (cancelled.order.state, cancelled.order.revision) == (OrderState.CANCELLED, 3)
    cancel_call = next(call for call in r.fake.calls if call.api_id == "kt10003")
    assert cancel_call.body["cncl_qty"] == "0"
    assert r.service.recover().status == ExecutionStatus.OK
    assert r.service.plan({SYMBOL: 12}) == {}


def test_sell_fill_with_tax_reconciles(tmp_path: Path) -> None:
    r = started(tmp_path)
    sell = Intent("sell-1", SYMBOL, "sell", 3, 71_000, 1_000)
    assert r.service.submit(sell).status == ExecutionStatus.OK
    r.fake.fill("0000001", 3, 71_000, commission=32, tax=383)
    assert r.service.recover().status == ExecutionStatus.OK
    filled = fact_of(r.ledger, "sell-1")
    assert (filled.state, filled.fees_krw) == (OrderState.FILLED, 415)


def test_revisions_survive_restart(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.fill("0000001", 2, 70_000, commission=21)
    before = next(f for f in r.broker.snapshot().orders if f.intent.intent_id == "signal-1")
    r.broker.close()
    restarted = make_broker(tmp_path, r.clock, r.fake, transmit=True)
    after = next(f for f in restarted.snapshot().orders if f.intent.intent_id == "signal-1")
    assert after == before


def test_token_is_reissued_once_for_invalid_token_queries(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.token = "fake-token-rotated-0000"
    assert r.service.recover().status == ExecutionStatus.OK
    assert r.fake.api_ids().count("au10001") == 2


# --- 전송 직전 기록과 응답 유실 --------------------------------------------------------------


def test_presend_record_failure_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = started(tmp_path)

    def fail(*_: object) -> None:
        raise BrokerError("injected reference write failure")

    monkeypatch.setattr(r.broker._reference, "record_pending", fail)  # pyright: ignore[reportPrivateUsage]  # reason: 쓰기 실패 주입
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert "kt10000" not in r.fake.api_ids()
    assert r.service.recover().status == ExecutionStatus.BLOCKED


def test_lost_ack_rematches_the_single_candidate_without_resending(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "lose"
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert r.service.recover().status == ExecutionStatus.OK
    assert fact_of(r.ledger).state == OrderState.OPEN
    assert r.service.submit(buy()).status == ExecutionStatus.OK
    assert r.fake.api_ids().count("kt10000") == 1


def test_lost_ack_with_two_candidates_halts(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "lose_twice"
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    report = r.service.recover()
    assert report.status == ExecutionStatus.BLOCKED
    assert r.ledger.halted()
    assert r.fake.api_ids().count("kt10000") == 1


def test_lost_request_without_candidate_halts_and_ignores_hts_orders(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "drop"
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    r.fake.manual_order(quantity=5, price=70_000)  # 같은 내용이지만 영웅문 주문이다
    report = r.service.recover()
    assert report.status == ExecutionStatus.BLOCKED
    assert fact_of(r.ledger).state == OrderState.UNKNOWN
    assert r.fake.api_ids().count("kt10000") == 1


def test_kiwoom_error_answer_without_candidate_becomes_rejected(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "error"
    assert r.service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert r.service.recover().status == ExecutionStatus.OK
    assert fact_of(r.ledger).state == OrderState.REJECTED


def test_rejected_answer_is_not_rebound_to_a_later_identical_order(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.order_mode = "error"
    r.service.submit(buy())
    assert r.service.recover().status == ExecutionStatus.OK
    r.fake.add_row(SYMBOL, "현금매수", 5, 70_000, channel="모의투자")
    assert r.service.recover().status == ExecutionStatus.BLOCKED
    assert fact_of(r.ledger).state == OrderState.REJECTED
    assert r.broker.latch() is None


def test_unconfirmed_cancel_stays_unknown(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.confirm_cancel = False
    assert r.service.cancel("signal-1").status == ExecutionStatus.UNKNOWN
    assert fact_of(r.ledger).state == OrderState.OPEN


def test_untracked_manual_order_halts_new_orders(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.fake.manual_order()
    result = r.service.submit(buy())
    assert result.status == ExecutionStatus.BLOCKED
    assert result.reconciliation is not None
    assert any("untracked broker order" in d for d in result.reconciliation.differences)
    assert "kt10000" not in r.fake.api_ids()


# --- 조회 일관성·여러 쪽·간격 ---------------------------------------------------------------


def test_snapshot_rereads_when_a_fill_arrives_midway(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.after["kt00018"] = [lambda: r.fake.fill("0000001", 1, 70_000, commission=10)]
    count = r.fake.api_ids().count("kt00007")
    snapshot = r.broker.snapshot()
    r.fake.after.clear()
    assert r.fake.api_ids().count("kt00007") - count == 4
    assert snapshot.orders[0].filled_quantity == 1


def test_snapshot_gives_up_when_orders_keep_changing(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.every["kt00001"] = [lambda: r.fake.fill("0000001", 1, 70_000)]
    with pytest.raises(BrokerError) as raised:
        r.broker.snapshot()
    assert not isinstance(raised.value, KiwoomBlockedError)
    assert r.broker.latch() is None


def test_paging_reads_every_page(tmp_path: Path) -> None:
    r = started(tmp_path)
    for index, symbol in enumerate(("005930", "000660", "035420")):
        r.service.submit(Intent(f"signal-{index}", symbol, "buy", 5, 70_000 + index * 100, 1_000))
    r.fake.page_size = 1
    snapshot = r.broker.snapshot()
    assert len(snapshot.orders) == 3
    assert any(call.headers.get("cont-yn") == "Y" for call in r.fake.calls)


def test_each_tr_waits_its_interval(tmp_path: Path) -> None:
    r = started(tmp_path)
    reads = [call.at for call in r.fake.calls if call.api_id == "kt00007"]
    assert len(reads) >= 2
    assert all(
        later - earlier >= 1.1 - 1e-9 for earlier, later in zip(reads, reads[1:], strict=False)
    )


# --- 정지 래치·날짜 넘김·수수료 ---------------------------------------------------------------


def test_amended_order_latches_until_cleared(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    amendment = r.fake.row(r.fake.add_row(SYMBOL, "현금매수정정", 5, 69_000, channel="영웅문4"))
    amendment["ori_ord"] = "0000001"
    amendment["mdfy_cncl"] = "정정확인"
    assert r.service.recover().status == ExecutionStatus.ERROR
    latched = r.broker.latch()
    assert latched is not None and "amended" in latched
    count = r.fake.api_ids().count("kt00007")
    assert r.service.recover().status == ExecutionStatus.ERROR
    assert r.fake.api_ids().count("kt00007") == count
    r.fake.rows.remove(amendment)
    r.broker.clear_latch()
    assert r.service.recover().status == ExecutionStatus.OK


def test_day_rollover_expires_the_open_remainder(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.fill("0000001", 2, 70_000, commission=21)
    assert r.service.recover().status == ExecutionStatus.OK
    r.fake.roll_day()
    assert r.service.recover().status == ExecutionStatus.OK
    expired = fact_of(r.ledger)
    assert (expired.state, expired.revision, expired.filled_quantity) == (
        OrderState.CANCELLED,
        3,
        2,
    )
    past_reads = [c for c in r.fake.calls if c.api_id == "kt00007" and c.body["ord_dt"] != ""]
    assert past_reads and past_reads[-1].body["ord_dt"] == "20261021"
    count = len(past_reads)
    assert r.service.recover().status == ExecutionStatus.OK
    assert len([c for c in r.fake.calls if c.api_id == "kt00007" and c.body["ord_dt"]]) == count


def test_fill_after_the_last_same_day_observation_latches(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.fill("0000001", 2, 70_000, commission=21)
    r.fake.roll_day()
    assert r.service.recover().status == ExecutionStatus.ERROR
    latched = r.broker.latch()
    assert latched is not None and "unknown amounts" in latched


def test_lagging_day_journal_is_transient(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy())
    r.fake.fill("0000001", 2, 70_000, commission=21, journal=False)
    assert r.service.recover().status == ExecutionStatus.ERROR
    assert r.broker.latch() is None
    r.fake.catch_up_journal()
    assert r.service.recover().status == ExecutionStatus.OK


# --- 여러 번 나눠 체결된 주문의 금액(2026-10-21 실측) ------------------------------------------


def test_multi_price_fills_take_exact_amounts_from_the_day_journal(tmp_path: Path) -> None:
    # 실측: 122주가 12번 체결되자 kt00007 체결단가(반올림 평균) × 수량이 실제보다 22원 적어 멈췄다.
    r = started(tmp_path)
    r.service.submit(buy(quantity=3))
    r.fake.fill("0000001", 1, 69_900, commission=240)
    r.fake.fill("0000001", 2, 70_000, commission=490)
    assert r.fake.row("0000001")["cntr_uv"] == pad(69_967)  # 209,900 / 3 = 69,966.7
    assert r.service.recover().status == ExecutionStatus.OK
    filled = fact_of(r.ledger)
    assert (filled.state, filled.filled_value_krw, filled.fees_krw) == (
        OrderState.FILLED,
        209_900,
        730,
    )


def test_requote_after_cancel_takes_the_rest_of_the_symbol_total(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy("first", quantity=5))
    r.fake.fill("0000001", 2, 69_900, commission=480)
    assert r.service.cancel("first").status == ExecutionStatus.OK
    assert r.service.submit(buy("second", quantity=3, price=70_100)).status == ExecutionStatus.OK
    second = next(row["ord_no"] for row in r.fake.rows if row["ord_uv"] == pad(70_100))
    r.fake.fill(second, 1, 70_000, commission=240)
    r.fake.fill(second, 2, 70_100, commission=490)
    assert r.service.recover().status == ExecutionStatus.OK
    first, later = fact_of(r.ledger, "first"), fact_of(r.ledger, "second")
    assert (first.filled_value_krw, first.fees_krw) == (139_800, 480)
    assert (later.state, later.filled_value_krw, later.fees_krw) == (
        OrderState.FILLED,
        210_200,
        730,
    )


def test_two_open_orders_with_fills_on_one_symbol_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 전송 전 거부(#596 리뷰)를 우회해 둘이 나간 경우에도 금액을 지어내지 않고 멈추는 방어선
    r = started(tmp_path)
    r.service.submit(buy("a", quantity=2))
    monkeypatch.setattr(r.broker, "_refusal", lambda intent, order_date: None)
    r.service.submit(buy("b", quantity=2, price=70_100))
    r.fake.fill("0000001", 1, 70_000, commission=240)
    r.fake.fill("0000002", 1, 70_100, commission=240)
    assert r.service.recover().status == ExecutionStatus.ERROR
    latched = r.broker.latch()
    assert latched is not None and "several open orders" in latched


def test_untracked_fills_on_the_same_symbol_latch(tmp_path: Path) -> None:
    r = started(tmp_path)
    r.service.submit(buy(quantity=2))
    r.fake.fill("0000001", 1, 70_000, commission=240)
    r.fake.fill(r.fake.manual_order(quantity=1), 1, 70_000, commission=240)
    assert r.service.recover().status == ExecutionStatus.ERROR
    latched = r.broker.latch()
    assert latched is not None and "did not send" in latched


# --- 비밀값과 주소 -------------------------------------------------------------------------


def test_secrets_never_reach_messages_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    r = rig(tmp_path)
    r.fake.echo_secret_in_errors = True
    r.fake.token_error = "8050"
    with pytest.raises(KiwoomBlockedError) as raised:
        r.broker.snapshot()
    r.fake.token_error = None
    r.fake.account = "5099999999"
    with pytest.raises(KiwoomBlockedError) as mismatch:
        r.broker.snapshot()
    texts = [str(raised.value), str(mismatch.value), caplog.text, repr(r.broker)]
    texts.append(repr(KiwoomCredentials(APP_KEY, SECRET, ACCOUNT)))
    for secret in (APP_KEY, SECRET, TOKEN, ACCOUNT, "5099999999"):
        assert all(secret not in text for text in texts), secret


def test_credentials_require_a_ten_digit_account() -> None:
    with pytest.raises(ValueError):
        KiwoomCredentials(APP_KEY, SECRET, "12345")


class _Response:
    status = 200
    headers = None

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"return_code": 0, "return_msg": "ok"}'


def test_urllib_transport_only_targets_the_mock_host(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = UrllibTransport()
    seen: list[str] = []

    def open_(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request.full_url)
        return _Response()

    monkeypatch.setattr(transport._opener, "open", open_)  # pyright: ignore[reportPrivateUsage]  # reason: 네트워크 없이 주소만 확인
    transport.post("/oauth2/token", {"api-id": "au10001"}, {})
    assert seen == [f"{MOCK_BASE_URL}/oauth2/token"]
    assert MOCK_BASE_URL == "https://mockapi.kiwoom.com"
    for path in ("https://api.kiwoom.com/oauth2/token", "//api.kiwoom.com/x", "oauth2/token"):
        with pytest.raises(ValueError):
            transport.post(path, {"api-id": "au10001"}, {})


# --- 설정 화면의 "연결 확인"(②-2) --------------------------------------------------------------


def test_connection_probe_maps_kiwoom_answers_without_ordering() -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    probe = KiwoomConnectionProbe(
        transport=fake, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep
    )
    credentials = BrokerCredentials(APP_KEY, SECRET, ACCOUNT)
    assert probe.verify(credentials).outcome == ConnectionOutcome.CONNECTED
    fake.account = "5099999999"
    assert probe.verify(credentials).outcome == ConnectionOutcome.ACCOUNT_MISMATCH
    fake.account = ACCOUNT
    for code, outcome in (
        ("8030", ConnectionOutcome.MODE_MISMATCH),
        ("8050", ConnectionOutcome.IP_NOT_ALLOWED),
        ("8001", ConnectionOutcome.AUTH_FAILED),
    ):
        fake.token_error = code
        assert probe.verify(credentials).outcome == outcome
    assert set(fake.api_ids()) == {"au10001", "ka00001"}


class _Silent:
    def post(
        self, path: str, headers: Mapping[str, str], body: Mapping[str, object]
    ) -> KiwoomHttpResponse:
        raise KiwoomNoResponseError("injected outage")


def test_connection_probe_reports_no_response() -> None:
    clock = Clock()
    probe = KiwoomConnectionProbe(
        transport=_Silent(), now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep
    )
    result = probe.verify(BrokerCredentials(APP_KEY, SECRET, ACCOUNT))
    assert result.outcome == ConnectionOutcome.NO_RESPONSE
