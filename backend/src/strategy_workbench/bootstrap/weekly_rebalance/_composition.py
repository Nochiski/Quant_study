"""주간 리밸런싱(S07) 명령줄의 composition root.

키는 설정 화면이 저장한 사용자 설정 폴더의 `secrets.json`에서 읽는다(명령줄·환경 변수로
받지 않는다).
작업 폴더는 저장소 밖이어야 한다. 원장·주문안에 계좌 사실이 담긴다.

    <작업 폴더>/ledger/execution.sqlite        S05 실행 원장(실행기가 만든다)
    <작업 폴더>/ledger/kiwoom-reference.sqlite 키움 모의 참조 원장
    <작업 폴더>/plans/                         주문안·승인
    <작업 폴더>/records/                       실행 사건·하루 기록
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from datetime import time as clock_time
from pathlib import Path

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import (
    CliFactories,
    run_cli,
)
from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import (
    KiwoomCredentials,
    KiwoomMockBroker,
    KiwoomTransport,
    system_trust_transport,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.market import KiwoomMarket
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.stream import (
    mock_connect,
    record_fills,
)
from strategy_workbench.adapters.outbound.rebalance_files.facade.plans import (
    JsonExecutionLog,
    JsonPlanStore,
)
from strategy_workbench.adapters.outbound.scope_signal.facade.source import ScopeSignalSource
from strategy_workbench.application.offline_execution.facade.execution import (
    BrokerError,
    ExecutionStatus,
    OfflineExecutionService,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    AccountUnavailableError,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from strategy_workbench.bootstrap.weekly_rebalance._credentials import (
    load_credentials,
    telegram_notice,
)
from strategy_workbench.bootstrap.weekly_rebalance._gateway import OfflineOrderGateway

KST = timezone(timedelta(hours=9))
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
START_PAUSE_SECONDS = 1.5


class KiwoomAccountSource:
    """주문안용 계좌. 실행 원장이 있으면 먼저 대사하고, 없으면 키움 조회만 쓴다. 주문하지 않는다."""

    def __init__(
        self,
        workdir: Path,
        credentials: KiwoomCredentials | None = None,
        *,
        transport: KiwoomTransport | None = None,
    ) -> None:
        self._ledger_dir = workdir / "ledger"
        self._credentials = credentials
        self._transport = transport

    def current(self) -> AccountState:
        self._ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        broker = KiwoomMockBroker(
            self._credentials or saved_credentials(),
            self._ledger_dir / "kiwoom-reference.sqlite",
            transport=self._transport or system_trust_transport(),
        )
        try:
            reconciled: bool | None = None
            detail = "실행 원장 없음"
            ledger_path = self._ledger_dir / "execution.sqlite"
            if ledger_path.exists():
                ledger = ExecutionSqliteLedger(ledger_path)
                try:
                    result = OfflineExecutionService(ledger, broker).recover()
                finally:
                    ledger.close()
                reconciled = result.status is ExecutionStatus.OK
                detail = f"{result.status.value} {result.detail} {result.reconciliation}".strip()
            account = broker.snapshot().account
        except BrokerError as error:
            raise AccountUnavailableError(f"kiwoom account unavailable — {error}") from error
        finally:
            broker.close()
        return AccountState(account.cash_krw, dict(account.holdings), reconciled, detail)


def saved_credentials() -> KiwoomCredentials:
    return load_credentials(REPOSITORY_ROOT)


def outside_repository(workdir: Path) -> Path:
    resolved = workdir.expanduser().resolve()
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise SystemExit("--workdir는 저장소 밖이어야 합니다(원장·주문안에 계좌 사실이 담긴다).")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    return resolved


def build_weekly_rebalance(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
    root = outside_repository(workdir)
    return WeeklyRebalanceService(
        # show·approve는 신호를 읽지 않는다. prepare는 --model-root를 반드시 받는다.
        ScopeSignalSource((model_root or root / "model_db").expanduser()),
        KiwoomAccountSource(root),
        JsonPlanStore(root),
        now=lambda: datetime.now(KST),
    )


@contextmanager
def open_weekly_execution(
    workdir: Path, send: bool, require_approval: bool
) -> Iterator[WeeklyExecutionService]:
    """실행기. `send`가 거짓이면 어댑터도 보내지 않는 기본값(`transmit=False`)으로 연다."""
    root = outside_repository(workdir)
    (root / "ledger").mkdir(parents=True, exist_ok=True, mode=0o700)
    broker = KiwoomMockBroker(
        saved_credentials(),
        root / "ledger" / "kiwoom-reference.sqlite",
        transport=system_trust_transport(),
        transmit=send,
    )
    gateway = OfflineOrderGateway(root / "ledger", broker)
    try:
        yield WeeklyExecutionService(
            JsonPlanStore(root),
            gateway,
            KiwoomMarket(broker),
            JsonExecutionLog(root),
            now=lambda: datetime.now(KST),
            sleep=time.sleep,
            require_approval=require_approval,
        )
    finally:
        gateway.close()


def record_today_fills(workdir: Path, until: clock_time) -> int:
    """오늘 `until`(KST)까지 실시간 체결 알림을 `records/fills-<날짜>.jsonl`에 덧붙인다.

    주문하지 않는다.
    """
    root = outside_repository(workdir)
    (root / "ledger").mkdir(parents=True, exist_ok=True, mode=0o700)
    transport = system_trust_transport()
    broker = KiwoomMockBroker(
        saved_credentials(), root / "ledger" / "kiwoom-reference.sqlite", transport=transport
    )
    today = datetime.now(KST)
    try:
        return asyncio.run(
            record_fills(
                broker.client.session_token,
                mock_connect(transport.ssl_context),
                root / "records" / f"fills-{today.date().isoformat()}.jsonl",
                datetime.combine(today.date(), until, KST),
                lambda: datetime.now(KST),
            )
        )
    finally:
        broker.close()


def main(argv: list[str] | None = None) -> int:
    # 앞 명령이 같은 TR을 막 불렀으면 1초 한도(1700)에 걸린다. 프로세스 사이 간격을 둔다.
    time.sleep(START_PAUSE_SECONDS)
    return run_cli(
        CliFactories(
            build_weekly_rebalance, open_weekly_execution, record_today_fills, telegram_notice
        ),
        argv,
    )
