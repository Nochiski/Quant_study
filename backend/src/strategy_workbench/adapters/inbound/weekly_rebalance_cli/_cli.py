"""주간 리밸런싱(S07) 명령줄. 입력 해석과 사람이 읽을 출력만 맡는다.

    prepare  --workdir <폴더> --model-root <모델 DB> --signal-date YYYY-MM-DD
    show     --workdir <폴더> --plan weekly-YYYY-MM-DD
    approve  --workdir <폴더> --plan weekly-YYYY-MM-DD --approver <이름> [--note <메모>]
    execute  --workdir <폴더> --plan weekly-YYYY-MM-DD --phase open|chase [--send]
             [--without-approval]
    close    --workdir <폴더> [--plan weekly-YYYY-MM-DD]
    report   --workdir <폴더>
    export   --workdir <폴더>   # <작업 폴더>/exports 에 분석·복기용 CSV 4개
    record-fills --workdir <폴더> --until HH:MM   # 장중 실시간 체결 알림 기록(한 곳에서만)

`execute`는 `--send`가 있어야 주문을 보낸다. 없으면 무엇을 보낼지만 보인다. 첫 4주는 승인한
주문안만 보낸다(`--without-approval`은 그 뒤에 쓴다). 주문안은 신호 날짜마다 한 번만 쓰인다.
"""

from __future__ import annotations

import argparse
import csv
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
    Phase,
    PlanRecord,
    PrepareStatus,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalanceAction

ServiceFactory = Callable[[Path, Path | None], WeeklyRebalanceService]
# (작업 폴더, 보내는가, 승인이 필요한가) → 실행기. 끝나면 원장·연결을 닫는다.
ExecutionFactory = Callable[[Path, bool, bool], AbstractContextManager[WeeklyExecutionService]]
# (작업 폴더, 끝낼 시각) → 받은 체결·접수 알림 건수
FillRecorder = Callable[[Path, time], int]


@dataclass(frozen=True)
class CliFactories:
    planning: ServiceFactory
    execution: ExecutionFactory
    recorder: FillRecorder


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
    show = commands.add_parser("show", help="남긴 주문안과 승인 여부를 보인다")
    show.add_argument("--plan", required=True)
    approve = commands.add_parser("approve", help="주문안을 승인한다(한 번만)")
    approve.add_argument("--plan", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--note", default="")
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
    for command in (prepare, show, approve, execute, close, report, export, fills):
        command.add_argument(
            "--workdir", type=Path, required=True, help="원장·주문안 폴더(저장소 밖)"
        )
    return parser


def run_cli(factories: CliFactories, argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "record-fills":
        received = factories.recorder(args.workdir, args.until)
        print(f"[ok] received={received}")
        return 0
    if args.command in ("execute", "close", "report", "export"):
        send = bool(getattr(args, "send", False))
        require_approval = not getattr(args, "without_approval", False)
        with factories.execution(args.workdir, send, require_approval) as service:
            return _run_execution(service, args)
    planning = factories.planning(args.workdir, getattr(args, "model_root", None))
    if args.command == "prepare":
        result = planning.prepare(args.signal_date)
        if result.record is not None:
            print(render(result.record, planning.plan(result.record.plan_id)[1]))
        print(f"[{result.status.value}] {result.detail}".rstrip())
        return 0 if result.status in (PrepareStatus.OK, PrepareStatus.EXISTS) else 1
    if args.command == "show":
        record, approval = planning.plan(args.plan)
        if record is None:
            print(f"[not_found] 주문안이 없습니다 — plan={args.plan}")
            return 1
        print(render(record, approval))
        return 0
    result = planning.approve(args.plan, args.approver, args.note)
    print(f"[{result.status.value}] plan={args.plan}")
    return 0 if result.status is ApproveStatus.OK else 1


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
            f"{line.reason}"
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
    return "\n".join(lines)


def _reconciled(value: bool | None) -> str:
    return "원장 없음" if value is None else ("일치" if value else "불일치")
