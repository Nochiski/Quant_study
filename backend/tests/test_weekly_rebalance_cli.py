"""S07 명령줄과 composition root. 키움은 가짜 서버, 신호·저장소는 메모리 대역이다."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path

import pytest

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import (
    CliFactories,
    FillRecording,
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
    LedgerRepairService,
    NoticeError,
    PlanRecord,
    RiskGuardService,
    RiskVerdict,
    SignalBasis,
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
from strategy_workbench.domain.risk.facade.risk import (
    DrawdownState,
    GuardedLine,
    GuardedTargets,
    GuardReason,
    RiskLevel,
)
from tests.application.test_ledger_repair import Repair
from tests.application.test_weekly_execution import PLAN, rig
from tests.application.test_weekly_rebalance import (
    NOW,
    Account,
    Log,
    Plans,
    Risk,
    Signals,
    close_record,
    snapshot,
)
from tests.test_adapters_kiwoom_mock import ACCOUNT, APP_KEY, SECRET, SYMBOL, Clock, FakeKiwoom

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_cli_prepares_shows_and_approves_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plans, risk = Plans(), Risk()

    def factory(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
        return WeeklyRebalanceService(
            Signals(),
            Account(),
            plans,
            risk=risk,
            log=Log(),
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
    assert "리스크 판정 · 단계 정상" in shown
    assert run_cli(factories, ["show", *common, "--plan", "weekly-2026-10-30"]) == 1


def _no_notifier() -> Notices:
    raise AssertionError("this command must not send a notice")


def _no_recorder(workdir: Path, until: time) -> FillRecording:
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

    def recorder(workdir: Path, until: time) -> FillRecording:
        recorded.append(until)
        return FillRecording(7) if len(recorded) == 1 else FillRecording(0, "return_code=1 거절")

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
    # 리뷰 #605 P3: 로그인 거절은 [ok] 가 아니라 실패로 끝난다
    assert run_cli(factories, ["record-fills", *common, "--until", "15:35"]) == 1
    assert "[login_refused] received=0 return_code=1 거절" in capsys.readouterr().out


def _source(tmp_path: Path, fake: FakeKiwoom) -> KiwoomAccountSource:
    return KiwoomAccountSource(
        tmp_path, KiwoomCredentials(APP_KEY, SECRET, ACCOUNT), transport=fake
    )


def test_a_quoted_home_workdir_is_expanded_before_exporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 리뷰 #604 P3: 따옴표로 감싼 '~' 를 받으면 계좌 CSV 가 현재 폴더 아래 '~/…' 에 쓰였다
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    r = rig()
    seen: list[Path] = []

    @contextmanager
    def execution(workdir: Path, send: bool, approval: bool) -> Iterator[WeeklyExecutionService]:
        seen.append(workdir)
        yield r.service

    factories = CliFactories(_unused_planner, execution, _no_recorder, _no_notifier)
    assert run_cli(factories, ["export", "--workdir", "~/s07"]) == 0
    assert seen == [tmp_path / "s07"]
    assert (tmp_path / "s07" / "exports" / "days.csv").exists()
    assert not (tmp_path / "~").exists()


def _unused_planner(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
    raise AssertionError("execution commands must not open the planner")


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


def test_telegram_says_when_the_gap_check_was_skipped() -> None:
    # 리뷰 #606 P3: 빌드 점검을 건너뛴 판도 주문지에서 '점검 통과'처럼 보였다
    names = {"000003": "HMM"}
    plan = plan_rebalance(
        RebalancePolicy(slots=3, keep_rank=5),
        ranks={"000003": 1},
        holdings={},
        prices={"000003": 10_000},
        cash_krw=1_000_000,
        blocked={},
    )
    signal = SignalRecord(
        "scope@2.0",
        "e_b",
        "2026-10-23",
        "ab" * 32,
        "e_p",
        (),
        "e_prev",
        names=tuple(names.items()),
        gap_check_skipped="직전 빌드 e_prev 순위를 쓸 수 없음(읽기 실패)",
    )
    record = PlanRecord("weekly-2026-10-23", NOW, signal, AccountState(1_000_000, {}, None), plan)
    text = render_telegram(record, date(2026, 10, 26))
    assert "<i>직전 빌드 점검 건너뜀 — 직전 빌드 e_prev 순위를 쓸 수 없음" in text
    assert "빌드 점검 건너뜀 — 직전 빌드 e_prev" in render(record, None)


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


def test_ledger_commands_show_and_repair_with_an_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repair = Repair(latch="snapshot — amended order")
    log = Log()

    @contextmanager
    def repairs(workdir: Path) -> Iterator[LedgerRepairService]:
        yield LedgerRepairService(repair, log, now=lambda: NOW)

    planner = _planner(Signals(), Account(), Plans())
    factories = CliFactories(planner, _no_execution, _no_recorder, _no_notifier, repairs)
    common = ["--workdir", str(tmp_path)]
    assert run_cli(factories, ["ledger-status", *common]) == 0
    shown = capsys.readouterr().out
    assert "정지 래치: snapshot — amended order" in shown and "실행 원장 신규 주문: 정지" in shown
    assert "후보 주문번호: 0000007" in shown
    bind = ["ledger-bind", *common, "--intent", "weekly-2026-10-23-005930-buy"]
    with pytest.raises(SystemExit):  # 고친 사람(--operator)은 반드시 받는다
        run_cli(factories, [*bind, "--order-no", "0000007"])
    capsys.readouterr()
    assert run_cli(factories, [*bind, "--order-no", "0000007", "--operator", "동민"]) == 0
    assert "[ok]" in capsys.readouterr().out
    assert run_cli(factories, ["ledger-clear-latch", *common, "--operator", "동민"]) == 0
    assert [event.kind for event in log.events_] == ["ledger_bind", "ledger_clear_latch"]
    no_repair = CliFactories(planner, _no_execution, _no_recorder, _no_notifier)
    assert run_cli(no_repair, ["ledger-status", *common]) == 1


def test_signal_status_tells_the_schedule_whether_to_keep_waiting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 리뷰 #605 P3: 빌드 준비 판정이 셸과 신호 어댑터에 갈라져 있었다. 셸은 이 명령만 묻는다
    folders = ["--workdir", str(tmp_path), "--model-root", str(tmp_path)]
    common = [*folders, "--signal-date", "2026-10-23"]
    cases = [
        (SignalRead(SignalReadStatus.OK, snapshot()), 0),
        (SignalRead(SignalReadStatus.NOT_READY, detail="other date"), 3),
        (SignalRead(SignalReadStatus.GATE_FAILED, detail="MG1 fail"), 1),
    ]
    for read, code in cases:
        planner = _planner(Signals(read), Account(), Plans())
        factories = CliFactories(planner, _no_execution, _no_recorder, _no_notifier)
        assert run_cli(factories, ["signal-status", *common]) == code
        assert f"[{read.status.value}]" in capsys.readouterr().out


def _planner(
    signals: Signals,
    account: Account,
    plans: Plans,
    risk: Risk | None = None,
    log: Log | None = None,
) -> ServiceFactory:
    def factory(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
        return WeeklyRebalanceService(
            signals,
            account,
            plans,
            risk=risk or Risk(),
            log=log or Log(),
            now=lambda: NOW,
            policy=RebalancePolicy(slots=3, keep_rank=5),
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
    assert "상위 5위 중 2종목이 사라짐" in notices.sent[-1]

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


def test_basis_picks_the_build_and_the_notice_names_it(tmp_path: Path) -> None:
    # 서버 예약 실행은 휴장 첫날 주말 보강판으로 만든다(2026-10-23 사용자 결정)
    notices = Notices()
    signals = Signals()
    factories = CliFactories(
        _planner(signals, Account(), Plans()), _no_execution, _no_recorder, lambda: notices
    )
    common = ["--workdir", str(tmp_path), "--model-root", str(tmp_path)]
    weekend = [*common, "--signal-date", "2026-10-23", "--basis", "supplement"]
    notify = ["--notify", "--execute-on", "2026-10-26"]
    assert run_cli(factories, ["signal-status", *weekend]) == 0
    assert run_cli(factories, ["prepare", *weekend, *notify]) == 0
    assert signals.bases == [SignalBasis.SUPPLEMENT, SignalBasis.SUPPLEMENT]
    assert "<i>scope_v1.0 · 10/23(금) 주말 보강 신호</i>" in notices.sent[-1]

    waiting = Signals(SignalRead(SignalReadStatus.NOT_READY, detail="latest supplement build"))
    failed = CliFactories(
        _planner(waiting, Account(), Plans()), _no_execution, _no_recorder, lambda: notices
    )
    assert run_cli(failed, ["prepare", *weekend, *notify]) == 1
    assert notices.sent[-1].startswith("<b>S07 주문지 못 만듦</b> · 10/23(금) 주말 보강 신호")
    assert "모델 신호를 읽지 못함 (signal_unavailable)" in notices.sent[-1]


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


# --- 리스크 가드 -------------------------------------------------------------------------------


def _telegram_record() -> PlanRecord:
    names = {"000003": "HMM", "000004": "에이블씨엔씨", "000005": "한솔홀딩스"}
    plan = plan_rebalance(
        RebalancePolicy(slots=3, keep_rank=5),
        ranks={"000003": 1, "000004": 2, "000005": 3},
        holdings={"000003": 300},
        prices=dict.fromkeys(names, 10_000),
        cash_krw=7_000_000,
        blocked={},
    )
    signal = SignalRecord(
        "scope@2.0", "e_b", "2026-10-23", "ab" * 32, "e_p", (), names=tuple(names.items())
    )
    return PlanRecord("weekly-2026-10-23", NOW, signal, AccountState(7_000_000, {}, None), plan)


def _verdict(
    level: RiskLevel, loss_bp: int, lines: tuple[GuardedLine, ...], violations: tuple[str, ...] = ()
) -> RiskVerdict:
    state = DrawdownState(level, 100_000_000, None, loss_bp, date(2026, 10, 23), 0)
    exposure = 5_000 if level in (RiskLevel.REDUCE, RiskLevel.HALT) else 10_000
    return RiskVerdict(
        "weekly-2026-10-23",
        NOW,
        1,
        "ab" * 32,
        state,
        GuardedTargets(level, exposure, lines),
        violations,
    )


def test_telegram_is_unchanged_when_risk_changed_nothing() -> None:
    record = _telegram_record()
    plain = _verdict(
        RiskLevel.NORMAL,
        467,
        tuple(
            GuardedLine(symbol, 0, target, target, ())
            for symbol, target in record.plan.target_shares().items()
        ),
    )
    assert render_telegram(record, date(2026, 10, 26), plain) == render_telegram(
        record, date(2026, 10, 26)
    )


def test_telegram_ends_with_one_risk_line_when_risk_changed_the_week() -> None:
    record = _telegram_record()
    lines = (
        GuardedLine("000003", 300, 326, 163, (GuardReason.EXPOSURE,)),
        GuardedLine("000004", 0, 326, 100, (GuardReason.EXPOSURE, GuardReason.ADV)),
        GuardedLine("000005", 0, 326, 0, (GuardReason.EXPOSURE, GuardReason.BLOCKED)),
    )
    text = render_telegram(
        record,
        date(2026, 10, 26),
        _verdict(RiskLevel.REDUCE, 1_620, lines, ("리스크가 허용한 수량보다 더 샀다",)),
    )
    assert text.endswith(
        "\n<b>리스크</b> — 고점 대비 −16.2%로 보유·매수 절반 · 매수 줄임 1종목(에이블씨엔씨) · "
        "매수 안 함 1종목(한솔홀딩스) · 지난 주문 뒤 한도 위반 1건"
    )
    halted = render_telegram(record, date(2026, 10, 26), _verdict(RiskLevel.HALT, 2_050, lines))
    assert "고점 대비 −20.5%로 신규 매수 정지·보유 절반" in halted
    warned = render_telegram(record, date(2026, 10, 26), _verdict(RiskLevel.WARN, 1_100, ()))
    assert warned.endswith("\n<b>리스크</b> — 고점 대비 −11.0% 경고")


def test_prepare_prints_the_risk_verdict_and_tells_when_it_could_not_judge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = Log()
    log.save_day(close_record(date(2026, 10, 23), 84_000_000))  # −16%
    notices = Notices()
    factories = CliFactories(
        _planner(Signals(), Account(), Plans(), log=log),
        _no_execution,
        _no_recorder,
        lambda: notices,
    )
    common = ["--workdir", str(tmp_path), "--model-root", str(tmp_path)]
    prepare = ["prepare", *common, "--signal-date", "2026-10-23"]
    notify = ["--notify", "--execute-on", "2026-10-26"]
    assert run_cli(factories, [*prepare, *notify]) == 0
    shown = capsys.readouterr().out
    assert "리스크 판정 · 단계 축소 · 고점 대비 −16.00%" in shown
    assert "000001 목표 97 → 48 (exposure)" in shown
    assert "보유·매수 절반" in notices.sent[-1]

    unjudged = CliFactories(
        _planner(Signals(), Account(), Plans(), risk=Risk(limits_=None)),
        _no_execution,
        _no_recorder,
        lambda: notices,
    )
    assert run_cli(unjudged, [*prepare, *notify]) == 1
    assert "리스크 판정을 못 함 (risk_unavailable)" in notices.sent[-1]


def test_risk_commands_show_reset_and_resume_with_an_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log, risk = Log(), Risk()
    log.save_day(close_record(date(2026, 10, 30), 79_000_000))  # −21% → 정지

    def guard(workdir: Path) -> RiskGuardService:
        # 명령은 그 장 마감 기록 뒤에 남는다(일요일 오전)
        return RiskGuardService(risk, log, now=lambda: NOW + timedelta(days=9))

    planner = _planner(Signals(), Account(), Plans())
    factories = CliFactories(planner, _no_execution, _no_recorder, _no_notifier, risk=guard)
    common = ["--workdir", str(tmp_path)]
    assert run_cli(factories, ["risk-status", *common]) == 0
    shown = capsys.readouterr().out
    assert "단계 정지 · 고점 100,000,000원 · 마지막 장 마감 2026-10-30 79,000,000원" in shown
    assert "고점 대비 −21.00%" in shown and "한도 판 1" in shown
    with pytest.raises(SystemExit):  # 해제한 사람은 반드시 받는다
        run_cli(factories, ["risk-resume", *common])
    capsys.readouterr()
    assert run_cli(factories, ["risk-resume", *common, "--operator", "동민"]) == 0
    resumed = capsys.readouterr().out
    assert "[ok]" in resumed and "단계 축소" in resumed
    assert run_cli(factories, ["risk-resume", *common, "--operator", "동민"]) == 1  # 이제 정지 아님
    assert run_cli(factories, ["risk-reset", *common, "--operator", "동민", "--note", "입금"]) == 0
    assert "고점 79,000,000원" in capsys.readouterr().out
    assert [action.kind.value for action in risk.actions_] == ["resume", "reset"]
    no_risk = CliFactories(planner, _no_execution, _no_recorder, _no_notifier)
    assert run_cli(no_risk, ["risk-status", *common]) == 1


def test_close_prints_the_risk_check_of_the_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    r = rig(level=RiskLevel.REDUCE)

    @contextmanager
    def execution(workdir: Path, send: bool, approval: bool) -> Iterator[WeeklyExecutionService]:
        yield r.service

    factories = CliFactories(
        _planner(Signals(), Account(), Plans()), execution, _no_recorder, _no_notifier
    )
    common = ["--workdir", str(tmp_path)]
    assert (
        run_cli(factories, ["execute", *common, "--plan", PLAN, "--phase", "open", "--send"]) == 0
    )
    r.market.closes = {"00000A": 10_100, "00000B": 10_000, "00000D": 10_050, "00000E": 9_900}
    capsys.readouterr()
    assert run_cli(factories, ["close", *common]) == 0
    shown = capsys.readouterr().out
    assert "리스크 점검 2026-10-26 · 단계 정지(전날 정상)" in shown
    assert "한도 위반 없음" in shown
