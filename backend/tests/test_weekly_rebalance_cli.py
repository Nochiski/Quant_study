"""S07 명령줄과 composition root. 키움은 가짜 서버, 신호·저장소는 메모리 대역이다."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import time
from pathlib import Path

import pytest

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import (
    CliFactories,
    run_cli,
)
from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import (
    KiwoomAccountSource,
    build_weekly_rebalance,
)
from strategy_workbench.domain.order_execution.facade.orders import Account as BrokerAccount
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePolicy
from tests.application.test_weekly_execution import PLAN, rig
from tests.application.test_weekly_rebalance import NOW, Account, Plans, Signals
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

    factories = CliFactories(factory, _no_execution, _no_recorder)
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

    factories = CliFactories(planning, execution, recorder)
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
