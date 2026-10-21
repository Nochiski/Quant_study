"""키움 모의 실측 도구 (S06 ③).

②-1 키움 모의 어댑터와 S05 오프라인 실행 서비스로, 사용자가 정한 범위 안에서만 모의 주문·취소를 하고
조회 원본을 남긴다. 제품 진입점이 아니라 사람이 손으로 돌리는 측정 도구다.

## 안전장치

- 키는 설정 화면이 저장한 사용자 설정 폴더의 `secrets.json`에서 읽는다(명령줄·환경 변수로
  받지 않는다).
- 주소는 모의 도메인뿐이다(어댑터가 고정). 실전 키·다른 계좌·IP 미등록이면 주문 전에 멈춘다.
- 주문·취소는 `--send`가 있어야 보낸다. 없으면 무엇을 보낼지만 출력하고 원장도 바꾸지 않는다.
- 주문 종목·수량은 사용자가 정한 `--symbol`·`--quantity` 그대로이고, 원장의 주문 수가
  `--max-orders`(사용자가 정한 횟수)에 닿으면 더 보내지 않는다.
- 원장·참조 원장·조회 원본은 `--out` 폴더(저장소 밖)에 둔다. 키·토큰·계좌번호는 출력하지 않는다.

## 실행 (backend/에서)

    uv run python scripts/kiwoom_mock_probe.py --out ~/quant-data/kiwoom-mock/measure/<날짜> status
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> init
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> quote --symbol 005930
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> buy --intent buy-1 --price 210000 \\
        --symbol 005930 --quantity 1 --max-orders 3 --send
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> cancel --intent buy-1 --send
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> recover
    uv run python scripts/kiwoom_mock_probe.py --out <폴더> resume   # 대사가 맞을 때만 정지를 푼다

같은 TR을 1초 안에 두 번 부르면 키움이 1700으로 거절하므로, 명령 사이에 1.5초를 쉰다.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategy_workbench.adapters.outbound.execution_sqlite.facade.ledger import (
    ExecutionSqliteLedger,
)
from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import (
    KiwoomCredentials,
    KiwoomMockBroker,
    KiwoomTransport,
    UrllibTransport,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
    default_secrets_path,
)
from strategy_workbench.application.offline_execution.facade.execution import (
    OfflineExecutionService,
)
from strategy_workbench.bootstrap.facade.http import runtime_assistant_settings
from strategy_workbench.domain.order_execution.facade.orders import Intent

KST = timezone(timedelta(hours=9))
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCOUNT_PATH = "/api/dostk/acnt"
START_PAUSE_SECONDS = 1.5


@dataclass(frozen=True)
class Probe:
    broker: KiwoomMockBroker
    service: OfflineExecutionService
    ledger: ExecutionSqliteLedger
    out: Path

    def close(self) -> None:
        self.broker.close()
        self.ledger.close()


def _transport() -> KiwoomTransport:
    """python.org macOS 빌드는 기본 인증서 목록이 비어 있어 OS 신뢰 저장소를 쓴다."""
    try:
        import truststore
    except ImportError:
        return UrllibTransport()
    return UrllibTransport(ssl_context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def _credentials() -> KiwoomCredentials:
    configured = runtime_assistant_settings().secrets_path
    store = LocalFileProviderSecretStore(
        configured if configured is not None else default_secrets_path(),
        forbidden_roots=(REPOSITORY_ROOT,),
    )
    saved = LocalFileBrokerCredentialStore(store).load()
    if saved is None:
        raise SystemExit("저장된 키움 모의 키가 없습니다. 설정 화면에서 먼저 저장하세요.")
    return KiwoomCredentials(saved.app_key, saved.secret_key, saved.account_number)


def open_probe(
    out: Path,
    *,
    send: bool,
    transport: KiwoomTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Probe:
    resolved = out.expanduser().resolve()
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise SystemExit("--out은 저장소 밖이어야 합니다(원장·조회 원본에 계좌 사실이 담긴다).")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    broker = KiwoomMockBroker(
        _credentials(),
        resolved / "kiwoom-reference.sqlite",
        transport=transport or _transport(),
        transmit=send,
        now=now,
        monotonic=monotonic,
        sleep=sleep,
    )
    ledger = ExecutionSqliteLedger(resolved / "execution.sqlite")
    return Probe(broker, OfflineExecutionService(ledger, broker), ledger, resolved)


def _private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def dump(probe: Probe, tag: str) -> Path:
    """조회 원본을 남긴다. 계좌번호 필드가 있는 TR은 부르지 않는다."""
    raw = probe.broker.raw_reads()
    path = probe.out / f"raw-{datetime.now(KST).strftime('%H%M%S')}-{tag}.json"
    _private_write(path, json.dumps(raw, ensure_ascii=False, indent=1))
    orders, journal = raw["kt00007"], raw["ka10170"]
    counts = [len(rows) if isinstance(rows, list) else 0 for rows in (orders, journal)]
    print(f"[원본] {path.name} — 주문행 {counts[0]} 매매일지 종목 {counts[1]}")
    return path


def show(probe: Probe) -> None:
    snapshot = probe.broker.snapshot()
    account = snapshot.account
    print(
        f"[계좌] 현금(d2_entra) {account.cash_krw:,} 예약 {account.reserved_cash_krw:,} "
        f"보유 종목 {len(account.holdings)}개"
    )
    for symbol, quantity in account.holdings:
        print(f"[보유] {symbol} {quantity}주")
    for fact in snapshot.orders:
        print(
            f"[주문] {fact.intent.intent_id} {fact.intent.side} {fact.intent.quantity}주 "
            f"@{fact.intent.limit_price_krw:,} → {fact.state} rev{fact.revision} "
            f"체결 {fact.filled_quantity}주 {fact.filled_value_krw:,}원 비용 {fact.fees_krw:,}원"
        )
    print(f"[S05] 정지={probe.ledger.halted()} 래치={probe.broker.latch()}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="키움 모의 실측 도구 (S06 ③)")
    parser.add_argument("--out", type=Path, required=True, help="원장·조회 원본 폴더(저장소 밖)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "init", "recover", "resume"):
        commands.add_parser(name)
    quote = commands.add_parser("quote")
    quote.add_argument("--symbol", required=True)
    for side in ("buy", "sell"):
        order = commands.add_parser(side)
        order.add_argument("--intent", required=True)
        order.add_argument("--price", type=int, required=True)
        order.add_argument("--symbol", required=True, help="사용자가 정한 종목")
        order.add_argument("--quantity", type=int, required=True, help="사용자가 정한 수량")
        order.add_argument("--max-orders", type=int, required=True, help="사용자가 정한 주문 횟수")
        order.add_argument("--fee-budget", type=int, default=3_000, help="수수료·세금 상한(원)")
        order.add_argument("--send", action="store_true")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("--intent", required=True)
    cancel.add_argument("--send", action="store_true")
    return parser


def run(
    argv: Sequence[str],
    *,
    transport: KiwoomTransport | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    send = bool(getattr(args, "send", False))
    if args.command in ("buy", "sell") and not send:
        print(
            f"[미리보기] 보내지 않음 — {args.command} {args.symbol} {args.quantity}주 "
            f"@{args.price:,} intent={args.intent}. 보내려면 --send를 붙이세요."
        )
        return 0
    if args.command == "cancel" and not send:
        print(f"[미리보기] 보내지 않음 — cancel intent={args.intent}. 보내려면 --send를 붙이세요.")
        return 0
    sleep(START_PAUSE_SECONDS)
    probe = open_probe(
        args.out, send=send, transport=transport, now=now, monotonic=monotonic, sleep=sleep
    )
    try:
        if args.command == "status":
            show(probe)
            dump(probe, "status")
        elif args.command == "init":
            result = probe.service.initialize()
            print(result.status, result.detail)
            dump(probe, "init")
            return 0 if result.status == "ok" else 1
        elif args.command in ("recover", "resume"):
            result = (
                probe.service.recover() if args.command == "recover" else probe.service.resume()
            )
            print(result.status, result.detail, result.reconciliation)
            show(probe)
            return 0 if result.status == "ok" else 1
        elif args.command == "quote":
            body = probe.broker._client.call(  # pyright: ignore[reportPrivateUsage]  # reason: 위와 같음
                "ka10004", "/api/dostk/mrkcond", {"stk_cd": args.symbol}
            ).body
            print(
                f"[호가] {args.symbol} 매도1 {abs(int(str(body['sel_fpr_bid']))):,} / "
                f"매수1 {abs(int(str(body['buy_fpr_bid']))):,} (기준 {body.get('bid_req_base_tm')})"
            )
        elif args.command in ("buy", "sell"):
            return _order(probe, args)
        elif args.command == "cancel":
            result = probe.service.cancel(args.intent)
            print(result.status, result.detail, result.order)
            dump(probe, f"after-cancel-{args.intent}")
            return 0 if result.status == "ok" else 1
        return 0
    finally:
        probe.close()


def _order(probe: Probe, args: argparse.Namespace) -> int:
    used = len(probe.ledger.orders())
    if used >= args.max_orders:
        print(f"[거부] 지시 범위 초과 — 이미 {used}건, 범위 {args.max_orders}건")
        return 2
    intent = Intent(
        args.intent, args.symbol, args.command, args.quantity, args.price, args.fee_budget
    )
    result = probe.service.submit(intent)
    print(result.status, result.detail, result.order, result.reconciliation)
    dump(probe, f"after-{args.command}-{args.intent}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
