"""S07 명령줄과 composition root. 키움은 가짜 서버, 신호·저장소는 메모리 대역이다."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import run_cli
from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    WeeklyRebalanceService,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import (
    KiwoomAccountSource,
    build_weekly_rebalance,
)
from strategy_workbench.domain.order_execution.facade.orders import Account as BrokerAccount
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePolicy
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

    common = ["--workdir", str(tmp_path)]
    prepare = ["prepare", *common, "--model-root", str(tmp_path), "--signal-date", "2026-10-23"]
    assert run_cli(factory, prepare) == 0
    shown = capsys.readouterr().out
    assert "주문안 weekly-2026-10-23" in shown and "[ok]" in shown
    assert shown.count("매수   000") == 3 and "승인: 없음" in shown

    plan = ["--plan", "weekly-2026-10-23"]
    assert run_cli(factory, ["approve", *common, *plan, "--approver", "동민"]) == 0
    assert run_cli(factory, ["approve", *common, *plan, "--approver", "동민"]) == 1
    capsys.readouterr()
    assert run_cli(factory, ["show", *common, *plan]) == 0
    assert "승인: 동민" in capsys.readouterr().out
    assert run_cli(factory, ["show", *common, "--plan", "weekly-2026-10-30"]) == 1


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
