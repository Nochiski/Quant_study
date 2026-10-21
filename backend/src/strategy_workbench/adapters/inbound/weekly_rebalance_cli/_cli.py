"""주간 리밸런싱(S07) 명령줄. 입력 해석과 사람이 읽을 출력만 맡는다.

    prepare  --workdir <폴더> --model-root <모델 DB> --signal-date YYYY-MM-DD
    show     --workdir <폴더> --plan weekly-YYYY-MM-DD
    approve  --workdir <폴더> --plan weekly-YYYY-MM-DD --approver <이름> [--note <메모>]

주문을 보내지 않는다. 주문안은 신호 날짜마다 한 번만 쓰이고 바뀌지 않는다.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    Approval,
    ApproveStatus,
    PlanRecord,
    PrepareStatus,
    WeeklyRebalanceService,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalanceAction

ServiceFactory = Callable[[Path, Path | None], WeeklyRebalanceService]

_ACTION_LABELS = {
    RebalanceAction.SELL: "매도",
    RebalanceAction.RESIZE: "조정",
    RebalanceAction.BUY: "매수",
    RebalanceAction.HOLD: "유지",
    RebalanceAction.SKIP: "건너뜀",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="주간 리밸런싱 주문안(S07). 주문은 보내지 않는다.")
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
    for command in (prepare, show, approve):
        command.add_argument(
            "--workdir", type=Path, required=True, help="원장·주문안 폴더(저장소 밖)"
        )
    return parser


def run_cli(factory: ServiceFactory, argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = factory(args.workdir, getattr(args, "model_root", None))
    if args.command == "prepare":
        result = service.prepare(args.signal_date)
        if result.record is not None:
            print(render(result.record, service.plan(result.record.plan_id)[1]))
        print(f"[{result.status.value}] {result.detail}".rstrip())
        return 0 if result.status in (PrepareStatus.OK, PrepareStatus.EXISTS) else 1
    if args.command == "show":
        record, approval = service.plan(args.plan)
        if record is None:
            print(f"[not_found] 주문안이 없습니다 — plan={args.plan}")
            return 1
        print(render(record, approval))
        return 0
    result = service.approve(args.plan, args.approver, args.note)
    print(f"[{result.status.value}] plan={args.plan}")
    return 0 if result.status is ApproveStatus.OK else 1


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
