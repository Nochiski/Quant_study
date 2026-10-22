"""S07 명령줄과 composition root. 키움은 가짜 서버, 신호·저장소는 메모리 대역이다."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

import pytest

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import (
    CliFactories,
    ServiceFactory,
    render,
    render_telegram,
    run_cli,
)
from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    NoticeError,
    PlanRecord,
    SignalRead,
    SignalReadStatus,
    SignalRecord,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import (
    KiwoomAccountSource,
    build_weekly_rebalance,
)
from strategy_workbench.domain.order_execution.facade.orders import Account as BrokerAccount
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePolicy, plan_rebalance
from tests.application.test_weekly_execution import PLAN, rig
from tests.application.test_weekly_rebalance import NOW, Account, Plans, Signals, snapshot
from tests.test_adapters_kiwoom_mock import ACCOUNT, APP_KEY, SECRET, SYMBOL, Clock, FakeKiwoom

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_cli_prepares_shows_and_approves_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = Plans()

    def factory(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
        return WeeklyRebalanceService(
            Signals(),
            Account(),
            plans,
            now=lambda: NOW,
            policy=RebalancePolicy(slots=3, keep_rank=5),
        )

    factories = CliFactories(factory, _no_execution, _no_recorder, _no_notifier)
    common = ["--workdir", str(tmp_path)]
    prepare = ["prepare", *common, "--model-root", str(tmp_path), "--signal-date", "2026-10-23"]
    assert run_cli(factories, prepare) == 0
    shown = capsys.readouterr().out
    assert "주문안 weekly-2026-10-23" in shown and "[ok]" in shown
    assert shown.count("매수   000") == 3 and "승인: 없음" in shown

    plan = ["--plan", "weekly-2026-10-23"]
    no_trade = ["--approver", "동민", "--no-trade", "00000X"]
    assert run_cli(factories, ["approve", *common, *plan, *no_trade]) == 1  # 주문안에 없는 종목
    keep = ["--approver", "동민", "--no-trade", "000002"]
    assert run_cli(factories, ["approve", *common, *plan, *keep]) == 0
    assert run_cli(factories, ["approve", *common, *plan, "--approver", "동민"]) == 1
    capsys.readouterr()
    assert run_cli(factories, ["show", *common, *plan]) == 0
    shown = capsys.readouterr().out
    assert "승인: 동민" in shown and "이번 주 매매에서 뺀 종목: 000002" in shown
    assert run_cli(factories, ["show", *common, "--plan", "weekly-2026-10-30"]) == 1


def _no_notifier() -> Notices:
    raise AssertionError("this command must not send a notice")


def _no_recorder(workdir: Path, until: time) -> int:
    raise AssertionError("this command must not open the fill recorder")


@contextmanager
def _no_execution(workdir: Path, send: bool, approval: bool) -> Iterator[WeeklyExecutionService]:
    raise AssertionError("planning commands must not open the executor")
    yield  # pragma: no cover


def test_cli_executes_closes_reports_and_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    r = rig()
    opened: list[tuple[bool, bool]] = []

    @contextmanager
    def execution(workdir: Path, send: bool, approval: bool) -> Iterator[WeeklyExecutionService]:
        opened.append((send, approval))
        yield r.service

    def planning(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
        raise AssertionError("execution commands must not open the planner")

    recorded: list[time] = []

    def recorder(workdir: Path, until: time) -> int:
        recorded.append(until)
        return 7

    factories = CliFactories(planning, execution, recorder, _no_notifier)
    common = ["--workdir", str(tmp_path)]
    assert run_cli(factories, ["execute", *common, "--plan", PLAN, "--phase", "open"]) == 0
    assert "보내지 않음" in capsys.readouterr().out and r.gateway.submitted == []
    send = ["execute", *common, "--plan", PLAN, "--phase", "open", "--send"]
    assert run_cli(factories, send) == 0
    assert opened == [(False, True), (True, True)] and len(r.gateway.submitted) == 3
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    capsys.readouterr()
    assert run_cli(factories, ["close", *common]) == 0
    assert "장 마감 기록 2026-10-26" in capsys.readouterr().out
    assert run_cli(factories, ["report", *common]) == 0
    assert "2026-10-26" in capsys.readouterr().out
    assert run_cli(factories, ["export", *common]) == 0
    exports = tmp_path / "exports"
    assert sorted(path.name for path in exports.iterdir()) == [
        "days.csv",
        "events.csv",
        "holdings.csv",
        "orders.csv",
    ]
    with (exports / "orders.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert {(row["symbol"], row["side"], row["planned_qty"]) for row in rows} == {
        ("00000A", "buy", "130"),
        ("00000B", "buy", "130"),
        ("00000E", "sell", "100"),
    }
    with (exports / "events.csv").open(encoding="utf-8-sig") as handle:
        kinds = [row["kind"] for row in csv.DictReader(handle)]
    assert kinds[0] == "initialized" and kinds.count("submit") == 3
    capsys.readouterr()
    assert run_cli(factories, ["record-fills", *common, "--until", "15:35"]) == 0
    assert recorded == [time(15, 35)] and "received=7" in capsys.readouterr().out


def _source(tmp_path: Path, fake: FakeKiwoom) -> KiwoomAccountSource:
    return KiwoomAccountSource(
        tmp_path, KiwoomCredentials(APP_KEY, SECRET, ACCOUNT), transport=fake
    )


def test_account_source_reads_kiwoom_without_a_ledger(tmp_path: Path) -> None:
    fake = FakeKiwoom(Clock())
    state = _source(tmp_path, fake).current()
    assert state == AccountState(10_000_000, {SYMBOL: 10}, None, "실행 원장 없음")
    assert not {"kt10000", "kt10001", "kt10003"} & set(fake.api_ids())


def test_account_source_reconciles_an_existing_ledger(tmp_path: Path) -> None:
    fake = FakeKiwoom(Clock())
    (tmp_path / "ledger").mkdir()
    ledger = ExecutionSqliteLedger(tmp_path / "ledger" / "execution.sqlite")
    ledger.initialize(BrokerAccount(10_000_000, ((SYMBOL, 10),)))
    ledger.close()
    assert _source(tmp_path, fake).current().reconciled is True
    fake.cash -= 1  # 원장에 없는 현금 변화
    mismatched = _source(tmp_path, fake).current()
    assert mismatched.reconciled is False and "cash mismatch" in mismatched.detail


def test_workdir_inside_the_repository_is_refused() -> None:
    with pytest.raises(SystemExit):
        build_weekly_rebalance(REPOSITORY_ROOT / "backend" / ".weekly", None)


@dataclass
class Notices:
    sent: list[str] = field(default_factory=list)
    error: str | None = None

    def send(self, html: str) -> None:
        if self.error is not None:
            raise NoticeError(self.error)
        self.sent.append(html)


def test_telegram_plan_follows_the_confirmed_template() -> None:
    names = {"000001": "알멕", "000002": "대원미디어", "000003": "HMM"}
    names |= {"000004": "에이블씨엔씨", "000005": "한솔<홀딩스>"}
    plan = plan_rebalance(
        RebalancePolicy(slots=3, keep_rank=5),
        ranks={"000003": 1, "000004": 2, "000005": 3, "000002": 7},
        holdings={"000001": 166, "000002": 100, "000003": 300},
        prices=dict.fromkeys(names, 10_000),
        cash_krw=4_340_000,
        blocked={},
        previous_ranks={"000001": 4},
        missing_reasons={"000001": "WISE 추정치 공백"},
    )
    record = PlanRecord(
        "weekly-2026-10-23",
        NOW,
        SignalRecord(
            "scope@1.0", "e_b", "2026-10-23", "ab" * 32, "e_p", (), names=tuple(names.items())
        ),
        AccountState(4_340_000, {}, None),
        plan,
    )
    assert render_telegram(record, date(2026, 10, 26)) == (
        "<b>S07 주문지</b> · 10/26(월) 실행\n"
        "<i>scope_v1.0 · 10/23(금) 장 마감 신호</i>\n\n"
        "<b>▼ 매도 2</b>\n"
        "<blockquote><b>알멕</b> 000001 · 166주 · 166만\n"
        "⚠️ 신호에서 빠짐 · WISE 추정치 공백 (직전 빌드 4위)\n\n"
        "<b>대원미디어</b> 000002 · 100주 · 100만\n"
        "순위 7위</blockquote>\n\n"
        "<b>▲ 매수 2</b>\n"
        "<blockquote><b>에이블씨엔씨</b> 000004 · 325주 · 325만\n"
        "<b>한솔&lt;홀딩스&gt;</b> 000005 · 325주 · 325만</blockquote>\n\n"
        "<b>유지 1</b>\n<blockquote>HMM</blockquote>\n\n"
        "총자산 1,000만 · 종목당 327만\n"
        "<b>⚠️ 확인 1건</b> — 알멕을 이번 주 매매에서 뺄지 승인 때 알려 주세요."
    )


def test_telegram_marks_a_held_name_without_a_signal_day_price() -> None:
    # 리뷰 #603: 신호일 가격이 없어 직전 종가로 평가하고 그대로 둔 보유 종목은 따로 알린다
    names = {"000001": "알멕", "000003": "HMM"}
    plan = plan_rebalance(
        RebalancePolicy(slots=3, keep_rank=5),
        ranks={"000003": 1},
        holdings={"000001": 100, "000003": 100},
        prices=dict.fromkeys(names, 10_000),
        cash_krw=1_000_000,
        blocked={"000001": "신호일 종가 없음(거래정지 등) — 2026-10-21 종가로 평가"},
        stale_prices={"000001"},
    )
    record = PlanRecord(
        "weekly-2026-10-23",
        NOW,
        SignalRecord(
            "scope@2.0", "e_b", "2026-10-23", "ab" * 32, "e_p", (), names=tuple(names.items())
        ),
        AccountState(1_000_000, {}, None),
        plan,
    )
    text = render_telegram(record, date(2026, 10, 26))
    assert "<b>유지 2</b>\n<blockquote>⚠️알멕 · HMM</blockquote>" in text
    assert text.endswith(
        "<b>⚠️ 가격 없음 1건</b> — 알멕은 신호일 종가가 없어(거래정지 등) 이번 주 매매 없이 둡니다."
    )
    assert "이번 주 매매에서 뺄지" not in text
    shown = render(record, None)
    assert "가격 없음 1종목 ['000001']" in shown and "확인 필요" not in shown


def test_approve_prints_the_new_buys_dropped_with_a_kept_sale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans = Plans()
    held = Account(AccountState(2_000_000, {"000009": 100}, None))
    factories = CliFactories(
        _planner(Signals(), held, plans), _no_execution, _no_recorder, _no_notifier
    )
    common = ["--workdir", str(tmp_path)]
    prepare = ["prepare", *common, "--model-root", str(tmp_path), "--signal-date", "2026-10-23"]
    assert run_cli(factories, prepare) == 0
    capsys.readouterr()
    approve = ["approve", *common, "--plan", "weekly-2026-10-23", "--approver", "동민"]
    assert run_cli(factories, [*approve, "--no-trade", "000009"]) == 0
    printed = capsys.readouterr().out
    assert "[ok] plan=weekly-2026-10-23" in printed
    assert "함께 뺀 새 매수(자리를 그대로 차지): 000003" in printed


def _planner(signals: Signals, account: Account, plans: Plans) -> ServiceFactory:
    def factory(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
        return WeeklyRebalanceService(
            signals, account, plans, now=lambda: NOW, policy=RebalancePolicy(slots=3, keep_rank=5)
        )

    return factory


def test_prepare_notify_sends_the_plan_or_why_it_was_not_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = Notices()
    common = ["--workdir", str(tmp_path), "--model-root", str(tmp_path)]
    prepare = ["prepare", *common, "--signal-date", "2026-10-23"]
    notify = ["--notify", "--execute-on", "2026-10-26"]

    plans = Plans()
    sent = CliFactories(
        _planner(Signals(), Account(), plans), _no_execution, _no_recorder, lambda: notices
    )
    assert run_cli(sent, [*prepare, *notify]) == 0
    assert notices.sent[-1].startswith("<b>S07 주문지</b> · 10/26(월) 실행")
    assert "<b>▲ 매수 3</b>" in notices.sent[-1]
    with pytest.raises(SystemExit):
        run_cli(sent, [*prepare, "--notify"])  # 실행일 없이는 보내지 않는다

    suspect = Signals(
        SignalRead(
            SignalReadStatus.OK,
            snapshot(previous_build_id="e_prev", previous_ranks={"000007": 1, "000008": 2}),
        )
    )
    held = CliFactories(
        _planner(suspect, Account(), Plans()), _no_execution, _no_recorder, lambda: notices
    )
    assert run_cli(held, [*prepare, *notify]) == 1
    assert notices.sent[-1].startswith("<b>S07 주문지 보류</b> · 10/23(금) 장 마감 신호")
    assert "상위 30위 중 2종목이 사라짐" in notices.sent[-1]

    broken = Account(error="kiwoom account unavailable — file=/home/someone/x")
    failed = CliFactories(
        _planner(Signals(), broken, Plans()), _no_execution, _no_recorder, lambda: notices
    )
    assert run_cli(failed, [*prepare, *notify]) == 1
    assert "키움 모의 계좌를 읽지 못함 (account_unavailable)" in notices.sent[-1]
    assert "/home" not in notices.sent[-1]  # 원문 진단은 서버 기록에만

    down = Notices(error="telegram unreachable — error=URLError")
    unsent = CliFactories(
        _planner(Signals(), Account(), Plans()), _no_execution, _no_recorder, lambda: down
    )
    capsys.readouterr()
    assert run_cli(unsent, [*prepare, *notify]) == 1  # 주문안은 남고 알림 실패는 드러낸다
    assert "[notice_failed] telegram unreachable" in capsys.readouterr().out


def test_notify_plan_prints_without_sending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = Notices()
    plans = Plans()
    factories = CliFactories(
        _planner(Signals(), Account(), plans), _no_execution, _no_recorder, lambda: notices
    )
    common = ["--workdir", str(tmp_path)]
    assert (
        run_cli(
            factories,
            ["prepare", *common, "--model-root", str(tmp_path), "--signal-date", "2026-10-23"],
        )
        == 0
    )
    capsys.readouterr()
    show = ["notify-plan", *common, "--plan", "weekly-2026-10-23", "--execute-on", "2026-10-26"]
    assert run_cli(factories, [*show, "--print"]) == 0
    assert "<b>S07 주문지</b>" in capsys.readouterr().out and notices.sent == []
    assert run_cli(factories, show) == 0 and len(notices.sent) == 1
    missing = ["notify-plan", *common, "--plan", "weekly-2026-10-30", "--execute-on", "2026-11-02"]
    assert run_cli(factories, missing) == 1
