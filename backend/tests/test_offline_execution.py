"""직접 만든 오프라인 계좌와 임시 원장으로 주문 장애 경로를 검증한다."""

from dataclasses import replace
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.fake_broker.facade.broker import FakeBroker
from strategy_workbench.application.offline_execution.facade.execution import (
    ExecutionStatus,
    LedgerError,
    OfflineExecutionService,
)
from strategy_workbench.domain.order_execution.facade.orders import (
    Account,
    Intent,
    OrderFact,
    OrderState,
    Snapshot,
)


def setup(tmp_path: Path) -> tuple[ExecutionSqliteLedger, FakeBroker, OfflineExecutionService]:
    ledger = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    broker = FakeBroker(Account(10_000, (("A", 10),)))
    service = OfflineExecutionService(ledger, broker)
    assert service.initialize().status == ExecutionStatus.OK
    return ledger, broker, service


def buy(identity: str = "signal-1", quantity: int = 5) -> Intent:
    return Intent(identity, "A", "buy", quantity, 100, 10)


def test_target_accounts_for_actual_holdings_pending_and_reserved_cash(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    assert service.plan({"A": 15}) == {"A": 5}
    assert service.submit(buy()).status == ExecutionStatus.OK
    assert service.plan({"A": 15}) == {}
    assert service.submit(Intent("unrelated", "B", "buy", 94, 100, 0)).status == ExecutionStatus.OK
    assert broker.snapshot().account.reserved_cash_krw == 9_910
    assert service.submit(buy("over-budget", 1)).status == ExecutionStatus.BLOCKED
    partial = broker.fill("signal-1", quantity=2, price_krw=99, fee_krw=2)
    ledger.apply(partial)
    assert service.recover().status == ExecutionStatus.OK
    assert service.plan({"A": 15, "B": 94}) == {}
    assert broker.snapshot().account.cash_krw == 9_800
    assert ledger.baseline().cash_krw == 10_000
    assert ledger.baseline().holdings == (("A", 10),)


def test_ack_loss_restart_does_not_resubmit_and_counts_fill_once(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    broker.lose_next_ack = True
    assert service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert ledger.orders()[0].state == OrderState.UNKNOWN
    ledger.close()
    restarted = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    service = OfflineExecutionService(restarted, broker)
    assert service.recover().status == ExecutionStatus.OK
    assert service.submit(buy()).status == ExecutionStatus.OK
    assert broker.submit_calls == 1
    filled = broker.fill("signal-1", quantity=5, price_krw=100, fee_krw=3)
    restarted.apply(filled)
    restarted.apply(filled)
    assert service.recover().status == ExecutionStatus.OK
    assert broker.snapshot().account.cash_krw == 9_497
    assert service.plan({"A": 15}) == {}


def test_storage_failure_prevents_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, broker, service = setup(tmp_path)

    def fail(intent: Intent) -> OrderFact:
        raise LedgerError(f"injected save failure — intent_id={intent.intent_id}")

    monkeypatch.setattr(ledger, "save_intent", fail)
    assert service.submit(buy()).status == ExecutionStatus.ERROR
    assert broker.submit_calls == 0


def test_claim_commit_crash_absence_stays_unknown(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    ledger.save_intent(buy())
    assert ledger.claim("signal-1")
    ledger.close()
    reopened = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    recovered = OfflineExecutionService(reopened, broker)
    assert recovered.recover().status == ExecutionStatus.BLOCKED
    assert recovered.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert recovered.submit(buy("new")).status == ExecutionStatus.BLOCKED
    assert broker.submit_calls == 0
    assert not reopened.claim("signal-1")


def test_ready_commit_crash_can_safely_send_once(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    ledger.save_intent(buy())
    ledger.close()
    reopened = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    recovered = OfflineExecutionService(reopened, broker)
    assert recovered.submit(buy()).status == ExecutionStatus.OK
    assert recovered.submit(buy()).status == ExecutionStatus.OK
    assert broker.submit_calls == 1


def test_ack_record_failure_reconciles_without_resending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, broker, service = setup(tmp_path)
    apply = ledger.apply

    def fail(fact: OrderFact) -> None:
        raise LedgerError(f"injected ack write failure — intent_id={fact.intent.intent_id}")

    monkeypatch.setattr(ledger, "apply", fail)
    assert service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert ledger.orders()[0].state == OrderState.UNKNOWN
    monkeypatch.setattr(ledger, "apply", apply)
    assert service.recover().status == ExecutionStatus.OK
    assert service.submit(buy()).status == ExecutionStatus.OK
    assert broker.submit_calls == 1


def test_partial_cancel_duplicate_reverse_conflict(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    assert service.submit(buy()).status == ExecutionStatus.OK
    opened = ledger.orders()[0]
    partial = broker.fill("signal-1", quantity=2, price_krw=100, fee_krw=1)
    ledger.apply(partial)
    ledger.apply(opened)
    ledger.apply(partial)
    assert ledger.orders()[0] == partial
    with pytest.raises(LedgerError, match="same-revision"):
        ledger.apply(replace(partial, filled_value_krw=199))
    service.stop()
    assert broker.snapshot().account.reserved_cash_krw == 309
    assert service.submit(buy("stopped")).status == ExecutionStatus.BLOCKED
    assert service.cancel("signal-1").status == ExecutionStatus.OK
    assert broker.snapshot().account.reserved_cash_krw == 0
    assert service.recover().status == ExecutionStatus.OK
    assert ledger.halted()
    assert service.resume().status == ExecutionStatus.OK
    assert service.plan({"A": 15}) == {"A": 3}
    assert service.submit(buy("remaining", 3)).status == ExecutionStatus.OK


def test_reject_and_lost_cancel_ack(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    broker.reject_next = True
    assert service.submit(buy()).order == OrderFact(buy(), OrderState.REJECTED, 1)
    assert service.recover().status == ExecutionStatus.OK
    assert service.submit(buy("second")).status == ExecutionStatus.OK
    broker.lose_next_cancel_ack = True
    assert service.cancel("second").status == ExecutionStatus.UNKNOWN
    assert service.recover().status == ExecutionStatus.OK
    assert ledger.orders()[0].state == OrderState.CANCELLED
    assert broker.submit_calls == 2


def test_manual_change_detected_and_halt_persists_across_restart(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    broker.manual_account_change(cash_change_krw=100, symbol="A", quantity_change=1)
    result = service.recover()
    assert result.status == ExecutionStatus.BLOCKED
    assert result.reconciliation is not None
    assert len(result.reconciliation.differences) == 2
    assert service.submit(buy()).status == ExecutionStatus.BLOCKED
    ledger.close()
    reopened = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    assert reopened.halted()
    assert OfflineExecutionService(reopened, broker).initialize().status == ExecutionStatus.ERROR
    assert broker.submit_calls == 0


def test_single_executor_across_instances_and_claim_uniqueness(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    second = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    with ledger.executor():
        assert OfflineExecutionService(second, broker).submit(buy()).status == ExecutionStatus.ERROR
    ledger.save_intent(buy())
    assert ledger.claim("signal-1")
    assert not second.claim("signal-1")
    assert broker.submit_calls == 0


def test_sells_reserve_pending_shares_and_fill_proceeds(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    sell = Intent("sell", "A", "sell", 8, 100, 2)
    assert service.submit(sell).status == ExecutionStatus.OK
    assert service.submit(Intent("oversell", "A", "sell", 3, 100)).status == ExecutionStatus.BLOCKED
    broker.fill("sell", quantity=8, price_krw=101, fee_krw=2)
    assert service.recover().status == ExecutionStatus.OK
    assert broker.snapshot().account.cash_krw == 10_806
    assert service.plan({"A": 2}) == {}


@pytest.mark.parametrize("quantity", [True, 0, -1, 1.5, 10**16])
def test_invalid_order_units_fail_closed(quantity: int) -> None:
    with pytest.raises(ValueError, match="invalid offline integer"):
        Intent("invalid", "A", "buy", quantity, 100)


def test_wrong_database_owner_refused(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(LedgerError, match="non-empty"):
        ExecutionSqliteLedger(path)


def test_old_revision_conflict_is_detected_after_further_fills(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    service.submit(buy())
    partial = broker.fill("signal-1", quantity=2, price_krw=100)
    ledger.apply(partial)
    ledger.apply(broker.fill("signal-1", quantity=3, price_krw=100))
    with pytest.raises(LedgerError, match="same-revision"):
        ledger.apply(replace(partial, filled_value_krw=199))
    assert service.recover().status == ExecutionStatus.OK


@pytest.mark.parametrize(
    "target,value",
    [
        ("baseline", "{broken"),
        ("baseline", '{"cash_krw": -1}'),
        ("order", "{broken"),
        ("order", "{}"),
    ],
)
def test_corrupt_ledger_fails_closed(tmp_path: Path, target: str, value: str) -> None:
    import sqlite3

    ledger, broker, service = setup(tmp_path)
    ledger.save_intent(buy())
    with sqlite3.connect(tmp_path / "execution.sqlite") as connection:
        if target == "baseline":
            connection.execute(
                "UPDATE execution_meta SET value = ? WHERE key = ?", (value, "baseline")
            )
        else:
            connection.execute("UPDATE order_intents SET fact = ?", (value,))
    assert service.recover().status == ExecutionStatus.ERROR
    assert service.submit(buy("new")).status == ExecutionStatus.ERROR
    assert broker.submit_calls == 0


def test_submit_reuses_one_reconciled_snapshot(tmp_path: Path) -> None:
    class SnapshotCountingBroker(FakeBroker):
        snapshot_calls = 0

        def snapshot(self) -> Snapshot:
            self.snapshot_calls += 1
            return super().snapshot()

    ledger = ExecutionSqliteLedger(tmp_path / "execution.sqlite")
    broker = SnapshotCountingBroker(Account(10_000))
    service = OfflineExecutionService(ledger, broker)
    service.initialize()
    broker.snapshot_calls = 0
    assert service.submit(buy()).status == ExecutionStatus.OK
    # 유스케이스 조회 한 번과 fake submit의 최종 가용 잔고 검증 한 번이다.
    assert broker.snapshot_calls == 2


def test_executor_os_lock_released_after_process_exit(tmp_path: Path) -> None:
    import subprocess
    import sys

    ledger, broker, service = setup(tmp_path)
    ledger.save_intent(buy())
    script = """
import os
import sys
from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
ledger = ExecutionSqliteLedger(sys.argv[1])
with ledger.executor():
    ledger.claim('signal-1')
    os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "execution.sqlite")], check=True, timeout=10
    )
    assert service.recover().status == ExecutionStatus.BLOCKED
    assert service.submit(buy()).status == ExecutionStatus.UNKNOWN
    assert broker.submit_calls == 0


def test_fake_does_not_deduplicate_for_the_application(tmp_path: Path) -> None:
    ledger, broker, service = setup(tmp_path)
    assert service.submit(buy()).status == ExecutionStatus.OK
    broker.submit(buy())
    assert broker.submit_calls == 2
    assert broker.snapshot().account.reserved_cash_krw == 1_020
    report = service.recover()
    assert report.status == ExecutionStatus.BLOCKED
    assert report.reconciliation is not None
    assert "duplicate broker intent identity" in report.reconciliation.differences
    assert ledger.halted()


@pytest.mark.parametrize(
    "side,first_price_krw,invalid_price_krw", [("buy", 50, 150), ("sell", 150, 50)]
)
def test_fake_rejects_incremental_limit_violation_atomically(
    tmp_path: Path, side: str, first_price_krw: int, invalid_price_krw: int
) -> None:
    ledger, broker, service = setup(tmp_path)
    intent = Intent("limit", "A", side, 2, 100, 2)
    assert service.submit(intent).status == ExecutionStatus.OK
    first = broker.fill("limit", quantity=1, price_krw=first_price_krw, fee_krw=1)
    before = broker.snapshot()
    # 중간 복구 없이 두 번째 증분의 위반을 fake 자체에서 거절해야 한다.
    with pytest.raises(ValueError, match="incremental fill violates limit"):
        broker.fill("limit", quantity=1, price_krw=invalid_price_krw, fee_krw=1)
    assert broker.snapshot() == before
    assert before.orders == (first,)
    assert ledger.orders()[0].state == OrderState.OPEN
    assert service.recover().status == ExecutionStatus.OK
    assert ledger.orders() == (first,)
    final = broker.fill("limit", quantity=1, price_krw=100, fee_krw=1)
    assert final.revision == first.revision + 1
    assert service.recover().status == ExecutionStatus.OK


@pytest.mark.parametrize("side,prices_krw", [("buy", (90, 80)), ("sell", (110, 120))])
def test_fake_accepts_each_price_improvement_without_intermediate_recovery(
    tmp_path: Path, side: str, prices_krw: tuple[int, int]
) -> None:
    ledger, broker, service = setup(tmp_path)
    assert service.submit(Intent("improved", "A", side, 2, 100, 2)).status == ExecutionStatus.OK
    broker.fill("improved", quantity=1, price_krw=prices_krw[0], fee_krw=1)
    final = broker.fill("improved", quantity=1, price_krw=prices_krw[1], fee_krw=1)
    assert final.state == OrderState.FILLED
    assert final.revision == 3
    assert final.filled_value_krw == sum(prices_krw)
    assert final.fees_krw == 2
    snapshot = broker.snapshot()
    signed_quantity = 2 if side == "buy" else -2
    cash_change_krw = -sum(prices_krw) if side == "buy" else sum(prices_krw)
    assert snapshot.account == Account(10_000 + cash_change_krw - 2, (("A", 10 + signed_quantity),))
    assert service.recover().status == ExecutionStatus.OK
    assert ledger.orders() == (final,)


@pytest.mark.parametrize(
    "quantity,price_krw,fee_krw,error",
    [
        (2, 100, 0, "overfill"),
        (1, 100, 2, "fee budget exceeded"),
        (True, 100, 0, "invalid fake fill"),
        (1, 0, 0, "invalid offline integer"),
        (1, 100, -1, "invalid offline integer"),
        (1, 10**16, 0, "invalid offline integer"),
    ],
)
def test_fake_invalid_fill_preserves_order_and_account(
    tmp_path: Path, quantity: int, price_krw: int, fee_krw: int, error: str
) -> None:
    _, broker, service = setup(tmp_path)
    assert service.submit(Intent("invalid", "A", "buy", 2, 100, 2)).status == ExecutionStatus.OK
    broker.fill("invalid", quantity=1, price_krw=100, fee_krw=1)
    before = broker.snapshot()
    with pytest.raises(ValueError, match=error):
        broker.fill("invalid", quantity=quantity, price_krw=price_krw, fee_krw=fee_krw)
    assert broker.snapshot() == before


@pytest.mark.parametrize(
    "terminal_state", [OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED]
)
def test_fake_rejects_fill_after_terminal_state(
    tmp_path: Path, terminal_state: OrderState
) -> None:
    _, broker, service = setup(tmp_path)
    broker.reject_next = terminal_state == OrderState.REJECTED
    assert service.submit(Intent("terminal", "A", "buy", 1, 100)).status == ExecutionStatus.OK
    if terminal_state == OrderState.FILLED:
        broker.fill("terminal", quantity=1, price_krw=100)
    elif terminal_state == OrderState.CANCELLED:
        broker.cancel("terminal")
    before = broker.snapshot()
    assert before.orders[0].state == terminal_state
    with pytest.raises(ValueError, match="invalid fake fill"):
        broker.fill("terminal", quantity=1, price_krw=100)
    assert broker.snapshot() == before
