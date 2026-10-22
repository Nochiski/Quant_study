"""주간 리밸런싱(S07) 명령줄. 입력 해석과 사람이 읽을 출력만 맡는다.

    prepare  --workdir <폴더> --model-root <모델 DB> --signal-date YYYY-MM-DD [--accept-anomalies]
    show     --workdir <폴더> --plan weekly-YYYY-MM-DD
    approve  --workdir <폴더> --plan weekly-YYYY-MM-DD --approver <이름> [--note <메모>]
             [--no-trade <종목> ...]   # 이번 주 매매에서 뺄 종목
    execute  --workdir <폴더> --plan weekly-YYYY-MM-DD --phase open|chase [--send]
             [--without-approval]
    close    --workdir <폴더> [--plan weekly-YYYY-MM-DD]
    report   --workdir <폴더>
    export   --workdir <폴더>   # <작업 폴더>/exports 에 분석·복기용 CSV 4개
    record-fills --workdir <폴더> --until HH:MM   # 장중 실시간 체결 알림 기록(한 곳에서만)
    notify-plan --workdir <폴더> --plan weekly-YYYY-MM-DD --execute-on YYYY-MM-DD [--print]
    signal-status --workdir <폴더> --model-root <모델 DB> --signal-date YYYY-MM-DD
             # 장 마감 빌드가 준비됐나. 0 = 준비, 3 = 아직(기다린다), 1 = 다른 이유로 못 쓴다
    ledger-status --workdir <폴더>   # 정지 래치·실행 정지·응답을 잃은 주문과 후보 주문번호
    ledger-bind --workdir <폴더> --intent <의도> --order-no <주문번호> --operator <이름> [--note]
    ledger-reject --workdir <폴더> --intent <의도> --operator <이름> [--note]
    ledger-clear-latch --workdir <폴더> --operator <이름> [--note]
    ledger-resume --workdir <폴더> --operator <이름> [--note]   # 다시 대사하고 일치하면 정지를 푼다

원장 고치기(ledger-*)는 원장 파일을 손으로 고치지 않게 하는 명령이다(2026-10-22 리뷰 #593).
고친 일은 실행 사건 기록에 남고, 고친 뒤 다시 대사해 일치하면 신규 주문 정지가 풀린다.

`prepare --notify --execute-on <실행일>`은 만든 주문지를 텔레그램으로 보낸다. 만들지 못했으면
보류·실패 알림을 보낸다(주문지 흐름의 알림만 보내고 로그성 알림은 보내지 않는다).

`execute`는 `--send`가 있어야 주문을 보낸다. 없으면 무엇을 보낼지만 보인다. 승인한 주문안만
보낸다(매주 승인). 주문안은 신호 날짜마다 한 번만 쓰인다.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    Approval,
    ApproveStatus,
    CloseStatus,
    DayRecord,
    ExecuteResult,
    ExecuteStatus,
    ExecutionEvent,
    LedgerRepairService,
    NoticeError,
    NoticeSender,
    Phase,
    PlanRecord,
    PrepareStatus,
    RepairError,
    RepairStatus,
    SignalReadStatus,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    LineCause,
    RebalanceAction,
    RebalanceLine,
)

ServiceFactory = Callable[[Path, Path | None], WeeklyRebalanceService]
# (작업 폴더, 보내는가, 승인이 필요한가) → 실행기. 끝나면 원장·연결을 닫는다.
ExecutionFactory = Callable[[Path, bool, bool], AbstractContextManager[WeeklyExecutionService]]


@dataclass(frozen=True)
class FillRecording:
    """받은 체결·접수 알림 건수. `refused`가 있으면 로그인이 거절돼 그날 기록을 멈췄다."""

    received: int
    refused: str | None = None


# (작업 폴더, 끝낼 시각) → 받은 알림 건수와 로그인 거절 사유
FillRecorder = Callable[[Path, time], FillRecording]
# 주문지 알림을 보낼 곳. 보낼 때만 만든다(설정이 없으면 `NoticeError`).
NoticeFactory = Callable[[], NoticeSender]
# 작업 폴더 → 원장 고치기. 끝나면 원장·연결을 닫는다.
RepairFactory = Callable[[Path], AbstractContextManager[LedgerRepairService]]
# `signal-status`가 "아직 빌드가 없다(기다린다)"를 알리는 종료 코드. 예약 스크립트가 읽는다.
SIGNAL_NOT_READY_EXIT = 3


@dataclass(frozen=True)
class CliFactories:
    planning: ServiceFactory
    execution: ExecutionFactory
    recorder: FillRecorder
    notifier: NoticeFactory
    repair: RepairFactory | None = None


_ACTION_LABELS = {
    RebalanceAction.SELL: "매도",
    RebalanceAction.RESIZE: "조정",
    RebalanceAction.BUY: "매수",
    RebalanceAction.HOLD: "유지",
    RebalanceAction.SKIP: "건너뜀",
}
_SIDE_LABELS = {"buy": "매수", "sell": "매도"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="주간 리밸런싱(S07) 주문안·실행·기록")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="장 마감 신호로 주문안을 만들어 남긴다")
    prepare.add_argument("--model-root", type=Path, required=True, help="모델 DB 폴더(model_db)")
    prepare.add_argument("--signal-date", type=date.fromisoformat, required=True)
    prepare.add_argument(
        "--accept-anomalies",
        action="store_true",
        help="직전 빌드와 견준 데이터 누락 의심을 사람이 확인했을 때만",
    )
    prepare.add_argument(
        "--notify", action="store_true", help="주문지(또는 보류·실패)를 텔레그램으로 보낸다"
    )
    prepare.add_argument("--execute-on", type=date.fromisoformat, help="주문지에 적을 실행일")
    signal = commands.add_parser("signal-status", help="그 날짜 장 마감 빌드가 준비됐나 본다")
    signal.add_argument("--model-root", type=Path, required=True)
    signal.add_argument("--signal-date", type=date.fromisoformat, required=True)
    show = commands.add_parser("show", help="남긴 주문안과 승인 여부를 보인다")
    show.add_argument("--plan", required=True)
    approve = commands.add_parser("approve", help="주문안을 승인한다(한 번만)")
    approve.add_argument("--plan", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--note", default="")
    approve.add_argument("--no-trade", action="append", default=[], help="이번 주 매매에서 뺄 종목")
    execute = commands.add_parser("execute", help="주문안을 낸다(--send가 있어야 보낸다)")
    execute.add_argument("--plan", required=True)
    execute.add_argument("--phase", choices=[phase.value for phase in Phase], required=True)
    execute.add_argument("--send", action="store_true")
    execute.add_argument("--without-approval", action="store_true")
    close = commands.add_parser("close", help="장 마감 대사와 하루 기록")
    close.add_argument("--plan", default=None, help="생략하면 가장 최근 주문안")
    report = commands.add_parser("report", help="하루 기록을 모아 보인다")
    export = commands.add_parser("export", help="분석·복기용 CSV를 <작업 폴더>/exports에 쓴다")
    fills = commands.add_parser("record-fills", help="장중 실시간 체결 알림을 기록한다")
    fills.add_argument("--until", type=time.fromisoformat, required=True, help="끝낼 시각 HH:MM")
    notify = commands.add_parser("notify-plan", help="남긴 주문지를 텔레그램으로 보낸다")
    notify.add_argument("--plan", required=True)
    notify.add_argument("--execute-on", type=date.fromisoformat, required=True)
    notify.add_argument("--print", action="store_true", help="보내지 않고 보낼 글만 보인다")
    status = commands.add_parser("ledger-status", help="래치·정지·응답을 잃은 주문을 보인다")
    bind = commands.add_parser("ledger-bind", help="응답을 잃은 주문을 키움 주문번호에 묶는다")
    bind.add_argument("--intent", required=True)
    bind.add_argument("--order-no", required=True)
    reject = commands.add_parser("ledger-reject", help="키움에 없는 주문을 거부로 확정한다")
    reject.add_argument("--intent", required=True)
    clear = commands.add_parser("ledger-clear-latch", help="정지 래치를 풀고 다시 대사한다")
    resume = commands.add_parser("ledger-resume", help="다시 대사하고 일치하면 정지를 푼다")
    for command in (bind, reject, clear, resume):
        command.add_argument("--operator", required=True, help="고친 사람(실행 기록에 남는다)")
        command.add_argument("--note", default="")
    planning = (prepare, signal, show, approve, notify)
    running = (execute, close, report, export, fills)
    for command in (*planning, *running, status, bind, reject, clear, resume):
        command.add_argument(
            "--workdir", type=Path, required=True, help="원장·주문안 폴더(저장소 밖)"
        )
    return parser


def run_cli(factories: CliFactories, argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    # 따옴표로 감싼 '~' 도 같은 폴더로 푼다. 조립(저장소 밖 검사)과 내보내기가 같은 경로를 쓴다.
    args.workdir = args.workdir.expanduser()
    if args.command == "prepare" and args.notify and args.execute_on is None:
        parser.error("--notify에는 --execute-on이 필요합니다")
    if args.command == "record-fills":
        recording = factories.recorder(args.workdir, args.until)
        if recording.refused is not None:
            print(f"[login_refused] received={recording.received} {recording.refused}".rstrip())
            return 1
        print(f"[ok] received={recording.received}")
        return 0
    if args.command.startswith("ledger-"):
        if factories.repair is None:
            print("[unavailable] 원장 고치기를 이 명령줄에 연결하지 않았다")
            return 1
        with factories.repair(args.workdir) as repairs:
            return _run_repair(repairs, args)
    if args.command in ("execute", "close", "report", "export"):
        send = bool(getattr(args, "send", False))
        require_approval = not getattr(args, "without_approval", False)
        with factories.execution(args.workdir, send, require_approval) as service:
            return _run_execution(service, args)
    planning = factories.planning(args.workdir, getattr(args, "model_root", None))
    if args.command == "prepare":
        result = planning.prepare(args.signal_date, accept_anomalies=args.accept_anomalies)
        if result.record is not None:
            print(render(result.record, planning.plan(result.record.plan_id)[1]))
        print(f"[{result.status.value}] {result.detail}".rstrip())
        made = result.status in (PrepareStatus.OK, PrepareStatus.EXISTS)
        if args.notify:
            text = (
                render_telegram(result.record, args.execute_on)
                if made and result.record is not None
                else render_telegram_notice(result.status, args.signal_date, result.detail)
            )
            if not _send(factories, text):
                return 1
        return 0 if made else 1
    if args.command == "signal-status":
        read = planning.signal_status(args.signal_date)
        print(f"[{read.status.value}] {read.detail}".rstrip())
        if read.ok:
            return 0
        return SIGNAL_NOT_READY_EXIT if read.status is SignalReadStatus.NOT_READY else 1
    if args.command == "notify-plan":
        record, _ = planning.plan(args.plan)
        if record is None:
            print(f"[not_found] 주문안이 없습니다 — plan={args.plan}")
            return 1
        text = render_telegram(record, args.execute_on)
        if args.print:
            print(text)
            return 0
        return 0 if _send(factories, text) else 1
    if args.command == "show":
        record, approval = planning.plan(args.plan)
        if record is None:
            print(f"[not_found] 주문안이 없습니다 — plan={args.plan}")
            return 1
        print(render(record, approval))
        return 0
    result = planning.approve(args.plan, args.approver, args.note, tuple(args.no_trade))
    print(f"[{result.status.value}] plan={args.plan} {result.detail}".rstrip())
    if result.dropped_buys:
        print(f"함께 뺀 새 매수(자리를 그대로 차지): {', '.join(result.dropped_buys)}")
    return 0 if result.status is ApproveStatus.OK else 1


def _run_repair(repairs: LedgerRepairService, args: argparse.Namespace) -> int:
    if args.command == "ledger-status":
        try:
            state = repairs.state()
        except RepairError as error:
            print(f"[unavailable] {error}")
            return 1
        halted = {None: "원장 없음", True: "정지", False: "정상"}[state.halted]
        print(f"정지 래치: {state.latch or '없음'}")
        print(f"실행 원장 신규 주문: {halted}")
        if not state.unresolved:
            print("응답을 잃은 주문: 없음")
        for order in state.unresolved:
            candidates = ", ".join(order.candidates) or "없음(키움에 같은 내용 주문 없음)"
            print(
                f"응답을 잃은 주문 {order.intent_id} — {order.symbol} {order.side} "
                f"{order.quantity:,}주 {order.limit_price_krw:,}원 · {order.order_date} "
                f"{order.sent_at} · {order.status} {order.note}".rstrip()
            )
            print(f"  후보 주문번호: {candidates}")
        return 0
    if args.command == "ledger-bind":
        result = repairs.bind(args.intent, args.order_no, args.operator, args.note)
    elif args.command == "ledger-reject":
        result = repairs.reject(args.intent, args.operator, args.note)
    elif args.command == "ledger-clear-latch":
        result = repairs.clear_latch(args.operator, args.note)
    else:
        result = repairs.resume(args.operator, args.note)
    print(f"[{result.status.value}] {result.detail}")
    return 0 if result.status is RepairStatus.OK else 1


def _send(factories: CliFactories, text: str) -> bool:
    try:
        factories.notifier().send(text)
    except NoticeError as error:
        print(f"[notice_failed] {error}")
        return False
    print("[notice] 텔레그램으로 보냄")
    return True


def _run_execution(service: WeeklyExecutionService, args: argparse.Namespace) -> int:
    if args.command == "execute":
        result = service.execute(args.plan, Phase(args.phase), send=args.send)
        print(render_execution(args.plan, args.phase, result))
        return 0 if result.status in (ExecuteStatus.OK, ExecuteStatus.PREVIEW) else 1
    if args.command == "close":
        closed = service.close(args.plan)
        if closed.record is not None:
            print(render_day(closed.record))
        print(f"[{closed.status.value}] {closed.detail}".rstrip())
        return 0 if closed.status is CloseStatus.OK else 1
    if args.command == "export":
        for path in export_csv(args.workdir / "exports", service.days(), service.events()):
            print(f"[export] {path}")
        return 0
    print(render_report(service.days()))
    return 0


def export_csv(
    folder: Path, days: Sequence[DayRecord], events: Sequence[ExecutionEvent]
) -> list[Path]:
    """엑셀에서 바로 열리게 UTF-8(BOM)로 쓴다. 계좌 사실이 담겨 600이다."""
    tables: dict[str, tuple[list[str], list[list[object]]]] = {
        "days.csv": (
            [
                "date",
                "plan_id",
                "matched",
                "cash_krw",
                "holdings_value_krw",
                "equity_krw",
                "traded_krw",
                "n_holdings",
            ],
            [
                [
                    d.day.isoformat(),
                    d.plan_id,
                    d.matched,
                    d.cash_krw,
                    d.equity_krw - d.cash_krw,
                    d.equity_krw,
                    d.traded_krw,
                    len(d.holdings),
                ]
                for d in days
            ],
        ),
        "orders.csv": (
            [
                "date",
                "plan_id",
                "symbol",
                "name",
                "side",
                "planned_qty",
                "filled_qty",
                "filled_value_krw",
                "avg_price_krw",
                "fees_krw",
                "reference_price_krw",
                "close_price_krw",
                "shortfall_bp",
                "missed_krw",
                "state",
                "intent_ids",
            ],
            [
                [
                    d.day.isoformat(),
                    d.plan_id,
                    o.symbol,
                    o.name,
                    o.side,
                    o.quantity,
                    o.filled_quantity,
                    o.filled_value_krw,
                    round(o.filled_value_krw / o.filled_quantity, 2) if o.filled_quantity else "",
                    o.fees_krw,
                    o.reference_price_krw,
                    o.close_price_krw,
                    "" if o.shortfall_bp is None else o.shortfall_bp,
                    o.missed_krw,
                    o.state,
                    o.intent_id,
                ]
                for d in days
                for o in d.orders
            ],
        ),
        "holdings.csv": (
            ["date", "symbol", "shares", "close_price_krw", "value_krw"],
            [
                [d.day.isoformat(), symbol, shares, price, shares * price]
                for d in days
                for symbol, shares, price in d.holdings
            ],
        ),
        "events.csv": (
            [
                "at",
                "plan_id",
                "phase",
                "kind",
                "symbol",
                "intent_id",
                "side",
                "quantity",
                "limit_price_krw",
                "reference_price_krw",
                "asks",
                "bids",
                "status",
                "detail",
                "preview",
            ],
            [
                [
                    e.at.isoformat(),
                    e.plan_id,
                    e.phase,
                    e.kind,
                    e.symbol,
                    e.intent_id,
                    e.side,
                    e.quantity,
                    e.limit_price_krw,
                    e.reference_price_krw,
                    " ".join(map(str, e.asks)),
                    " ".join(map(str, e.bids)),
                    e.status,
                    e.detail,
                    e.preview,
                ]
                for e in events
            ],
        ),
    }
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths: list[Path] = []
    for name, (header, rows) in tables.items():
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        paths.append(path)
    return paths


def render_execution(plan_id: str, phase: str, result: ExecuteResult) -> str:
    sent = "보냄" if result.status is ExecuteStatus.OK else "보내지 않음"
    lines = [
        f"실행 {plan_id} 단계 {phase} ({sent})",
        f"{'방향':<4} {'종목':<6} {'수량':>7} {'지정가':>9}  결과  내용",
    ]
    for line in result.lines:
        lines.append(
            f"{_SIDE_LABELS.get(line.side, line.side):<4} {line.symbol:<6} {line.quantity:>7,} "
            f"{line.limit_price_krw:>9,}  {line.outcome}  {line.detail}".rstrip()
        )
    lines.append(f"[{result.status.value}] {result.detail}".rstrip())
    return "\n".join(lines)


def render_day(record: DayRecord) -> str:
    held = sum(shares * price for _, shares, price in record.holdings)
    lines = [
        f"장 마감 기록 {record.day.isoformat()} · 주문안 {record.plan_id} · 대사 "
        f"{'일치' if record.matched else '불일치'}",
        f"현금 {record.cash_krw:,}원 · 보유 평가 {held:,}원 · 총자산 {record.equity_krw:,}원 · "
        f"주문안 체결 금액 {record.traded_krw:,}원",
        "",
        f"{'방향':<4} {'종목':<6} {'계획':>7} {'체결':>7} {'평균가':>9} {'기준가':>9} {'종가':>9} "
        f"{'차이bp':>7} {'기회비용':>11}  상태  이름",
    ]
    for order in record.orders:
        average = order.filled_value_krw // order.filled_quantity if order.filled_quantity else 0
        shortfall = "-" if order.shortfall_bp is None else f"{order.shortfall_bp:+d}"
        lines.append(
            f"{_SIDE_LABELS.get(order.side, order.side):<4} {order.symbol:<6} "
            f"{order.quantity:>7,} {order.filled_quantity:>7,} {average:>9,} "
            f"{order.reference_price_krw:>9,} {order.close_price_krw:>9,} {shortfall:>7} "
            f"{order.missed_krw:>+11,}  {order.state}  {order.name}"
        )
    return "\n".join(lines)


def render_report(days: Sequence[DayRecord]) -> str:
    lines = [
        f"{'날짜':<10} {'총자산':>14} {'현금':>13} {'보유':>4} {'체결 금액':>13}  대사  주문안"
    ]
    for record in days:
        lines.append(
            f"{record.day.isoformat():<10} {record.equity_krw:>14,} {record.cash_krw:>13,} "
            f"{len(record.holdings):>4} {record.traded_krw:>13,}  "
            f"{'일치' if record.matched else '불일치'}  {record.plan_id}"
        )
    return "\n".join(lines)


def render(record: PlanRecord, approval: Approval | None) -> str:
    plan, signal = record.plan, record.signal
    lines = [
        f"주문안 {record.plan_id} (만든 시각 {record.created_at.isoformat(timespec='seconds')})",
        f"신호 {signal.model_id} 빌드 {signal.build_id} 신호일 {signal.signal_date} "
        f"점수 지문 {signal.scores_sha256[:12]}",
        f"계좌 현금 {plan.cash_krw:,}원 · 총자산 {plan.equity_krw:,}원 · 종목당 목표 "
        f"{plan.target_value_krw:,}원 · 원장 대사 {_reconciled(record.account.reconciled)}",
        *(f"⚠ 데이터 누락 의심을 확인하고 만든 주문안: {reason}" for reason in signal.anomalies),
        *(
            [f"빌드 점검 건너뜀 — {signal.gap_check_skipped}"]
            if signal.gap_check_skipped is not None
            else []
        ),
        "",
        f"{'구분':<4} {'종목':<6} {'순위':>4} {'현재':>7} {'목표':>7} {'증감':>7} "
        f"{'기준가':>9} {'증감 금액':>13}  사유",
    ]
    buy_krw = sell_krw = 0
    for line in plan.lines:
        price = line.reference_price_krw or 0
        amount = line.delta_shares * price
        if line.action is not RebalanceAction.SKIP:
            buy_krw += max(amount, 0)
            sell_krw += max(-amount, 0)
        lines.append(
            f"{_ACTION_LABELS[line.action]:<4} {line.symbol:<6} "
            f"{'-' if line.rank is None else line.rank:>4} {line.current_shares:>7,} "
            f"{line.target_shares:>7,} {line.delta_shares:>+7,} {price:>9,} {amount:>+13,}  "
            f"{'⚠확인 ' if line.review else ''}{line.reason}"
        )
    lines += [
        "",
        f"기준가로 계산한 매수 {buy_krw:,}원 · 매도 {sell_krw:,}원 (수수료·세금·체결가 차이 제외)",
        "승인: "
        + (
            "없음"
            if approval is None
            else f"{approval.approver} {approval.approved_at.isoformat(timespec='seconds')} "
            f"{approval.note}".rstrip()
        ),
    ]
    if approval is not None and approval.no_trade:
        lines.append(f"이번 주 매매에서 뺀 종목: {', '.join(approval.no_trade)}")
    reviews = [line.symbol for line in plan.lines if _flagged_sale(line)]
    if reviews and approval is None:
        lines.append(
            f"⚠ 확인 필요 {len(reviews)}종목 {reviews} — 신호에서 빠졌다. 데이터 누락이면 "
            "승인할 때 --no-trade 로 이번 주 매매에서 뺀다(그만큼 순위 낮은 새 매수도 빠진다)"
        )
    unpriced = [line.symbol for line in plan.lines if _unpriced_hold(line)]
    if unpriced:
        lines.append(
            f"⚠ 가격 없음 {len(unpriced)}종목 {unpriced} — 신호일 종가가 없어 직전 종가로 평가하고 "
            "이번 주 매매 없이 둔다(거래정지 여부 확인)"
        )
    return "\n".join(lines)


def _flagged_sale(line: RebalanceLine) -> bool:
    """신호에서 빠져 파는 보유 종목(데이터 누락일 수 있어 사람이 확인한다)."""
    return line.review and line.action is not RebalanceAction.HOLD


def _unpriced_hold(line: RebalanceLine) -> bool:
    """신호일 가격이 없어 직전 종가로 평가하고 그대로 둔 보유 종목."""
    return line.review and line.action is RebalanceAction.HOLD


def _reconciled(value: bool | None) -> str:
    return "원장 없음" if value is None else ("일치" if value else "불일치")


_WEEKDAYS = "월화수목금토일"
_NOTICE_REASONS = {
    PrepareStatus.SIGNAL_UNAVAILABLE: "장 마감 신호를 읽지 못함",
    PrepareStatus.ACCOUNT_UNAVAILABLE: "키움 모의 계좌를 읽지 못함",
    PrepareStatus.NOT_RECONCILED: "실행 원장과 키움 계좌가 맞지 않음",
    PrepareStatus.INVALID_INPUT: "주문안 계산 입력이 맞지 않음",
}


def render_telegram(record: PlanRecord, execute_on: date) -> str:
    """텔레그램 주문지(HTML, 2026-10-21 사용자 확정 양식). 굵게·인용 상자만 쓴다."""
    plan, signal = record.plan, record.signal
    names = dict(signal.names)
    sells: list[str] = []
    buys: list[str] = []
    skips: list[str] = []
    holds: list[str] = []
    for line in plan.lines:
        name = _html(names.get(line.symbol, line.symbol))
        head = f"<b>{name}</b> {line.symbol}"
        if line.action is RebalanceAction.HOLD:
            holds.append(f"⚠️{name}" if line.review else name)
            continue
        if line.action is RebalanceAction.SKIP:
            skips.append(f"{head}\n{_html(line.reason)}")
            continue
        amount = abs(line.delta_shares) * (line.reference_price_krw or 0)
        item = f"{head} · {abs(line.delta_shares):,}주 · {_won(amount)}"
        note = _telegram_note(line)
        (sells if line.delta_shares < 0 else buys).append(f"{item}\n{note}" if note else item)
    parts = [
        f"<b>S07 주문지</b> · {_day(execute_on)} 실행\n"
        f"<i>{_html(signal.model_id.replace('@', '_v'))} · "
        f"{_day(date.fromisoformat(signal.signal_date))} 장 마감 신호</i>"
    ]
    parts += _section(f"▼ 매도 {len(sells)}", sells)
    parts += _section(f"▲ 매수 {len(buys)}", buys)
    parts += _section(f"건너뜀 {len(skips)}", skips)
    if holds:
        parts.append(f"<b>유지 {len(holds)}</b>\n<blockquote>{' · '.join(holds)}</blockquote>")
    if not sells and not buys:
        parts.append("이번 주 매매 없음")
    footer = [f"총자산 {_won(plan.equity_krw)} · 종목당 {_won(plan.target_value_krw)}"]
    if signal.anomalies:
        footer.append("<i>데이터 누락 의심을 확인하고 만든 주문지</i>")
    if signal.gap_check_skipped is not None:
        footer.append(f"<i>직전 빌드 점검 건너뜀 — {_html(signal.gap_check_skipped)}</i>")
    reviews = [names.get(line.symbol, line.symbol) for line in plan.lines if _flagged_sale(line)]
    if reviews:
        footer.append(
            f"<b>⚠️ 확인 {len(reviews)}건</b> — {_html(', '.join(reviews))}"
            f"{_object_particle(reviews[-1])} 이번 주 매매에서 뺄지 승인 때 알려 주세요."
        )
    unpriced = [names.get(line.symbol, line.symbol) for line in plan.lines if _unpriced_hold(line)]
    if unpriced:
        footer.append(
            f"<b>⚠️ 가격 없음 {len(unpriced)}건</b> — {_html(', '.join(unpriced))}"
            f"{_topic_particle(unpriced[-1])} 신호일 종가가 없어(거래정지 등) "
            "이번 주 매매 없이 둡니다."
        )
    parts.append("\n".join(footer))
    return "\n\n".join(parts)


def render_telegram_notice(status: PrepareStatus, signal_date: date, detail: str) -> str:
    """주문지를 만들지 않았을 때의 알림. 원문 진단은 서버 기록에만 둔다(경로가 담길 수 있다)."""
    when = f"{_day(signal_date)} 장 마감 신호"
    if status is PrepareStatus.SIGNAL_SUSPECT:
        return (
            f"<b>S07 주문지 보류</b> · {when}\n"
            f"<blockquote>데이터 누락 의심 — {_html(detail)}</blockquote>\n"
            "확인 뒤 다시 만듭니다."
        )
    reason = _NOTICE_REASONS.get(status, status.value)
    return (
        f"<b>S07 주문지 못 만듦</b> · {when}\n"
        f"<blockquote>{reason} ({status.value})</blockquote>\n"
        "자세한 내용은 서버 기록에 있습니다."
    )


def _telegram_note(line: RebalanceLine) -> str:
    """주문지 줄 아래 한 줄. 도메인이 넘긴 원인 칸으로 고른다(사유 문장을 쪼개지 않는다,
    2026-10-22 리뷰 #607). 이 칸이 없는 옛 주문안은 사유 문장을 그대로 싣는다."""
    if line.cause is LineCause.SIGNAL_MISSING:
        return f"⚠️ 신호에서 빠짐 · {_html(line.missing_why)} ({_html(line.last_seen)})"
    if line.cause is LineCause.RESIZED:
        return "비중 조정"
    if line.cause is LineCause.OUTSIDE_BUFFER:
        return f"순위 {line.rank}위"
    if line.cause is LineCause.NEW_TOP:
        return ""
    return _html(line.reason)


def _section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    joiner = "\n\n" if any("\n" in item for item in items) else "\n"
    return [f"<b>{title}</b>\n<blockquote>{joiner.join(items)}</blockquote>"]


def _won(amount_krw: int) -> str:
    """만 원 단위로 반올림해 `1억 96만`·`963만`처럼 쓴다."""
    man = (amount_krw + 5_000) // 10_000
    eok, rest = divmod(man, 10_000)
    if not eok:
        return f"{rest:,}만"
    return f"{eok:,}억 {rest:,}만" if rest else f"{eok:,}억"


def _day(day: date) -> str:
    return f"{day.month}/{day.day}({_WEEKDAYS[day.weekday()]})"


def _object_particle(word: str) -> str:
    """마지막 글자에 받침이 있으면 `을`, 없으면 `를`. 한글이 아니면 `을(를)`."""
    return _particle(word, "을", "를")


def _topic_particle(word: str) -> str:
    """마지막 글자에 받침이 있으면 `은`, 없으면 `는`. 한글이 아니면 `은(는)`."""
    return _particle(word, "은", "는")


def _particle(word: str, after_final: str, after_vowel: str) -> str:
    last = word[-1:]
    if not "가" <= last <= "힣":
        return f"{after_final}({after_vowel})"
    return after_final if (ord(last) - ord("가")) % 28 else after_vowel


def _html(text: str) -> str:
    return html.escape(text, quote=False)
