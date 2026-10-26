"""S07 리스크 가드의 한도 파일과 기록 파일. 계좌 사실이 담겨 600으로 만든다.

- 한도: 저장소 `backend/scripts/weekly_rebalance_risk_limits.json`(전략 코드 밖, 바꾸는 길은 PR).
  읽을 때마다 파일 바이트의 SHA-256을 함께 싣는다.
- `<폴더>/risk/verdict-<주문안>.json`: 주문안마다 한 번만 쓰는 리스크 판정.
- `<폴더>/risk/day-YYYY-MM-DD.json`: 그날 장 마감 낙폭 단계와 체결 후 점검(다시 대사하면 갈아
  끼운다).
- `<폴더>/risk/actions.jsonl`: 사람의 명령(초기화·정지 해제)을 한 줄씩 덧붙인다.

읽지 못하거나 형식이 맞지 않으면 `RiskUnavailableError`다 — 판정을 못 하면 주문하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from strategy_workbench.adapters.outbound.rebalance_files._plans import PLAN_ID, write_once
from strategy_workbench.adapters.outbound.rebalance_files._records import replace_file
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    RiskDay,
    RiskUnavailableError,
    RiskVerdict,
)
from strategy_workbench.domain.risk.facade.risk import (
    ActionKind,
    DrawdownState,
    GuardedLine,
    GuardedTargets,
    GuardReason,
    PostTradeReview,
    ReviewedOrder,
    RiskAction,
    RiskLevel,
    RiskLimits,
    parse_limits,
)

_T = TypeVar("_T")
VERDICT_SCHEMA = "s07-risk-verdict/1"
RISK_DAY_SCHEMA = "s07-risk-day/1"


class JsonRiskStore:
    def __init__(self, folder: Path, limits_file: Path) -> None:
        self._folder = folder / "risk"
        self._limits_file = limits_file

    def limits(self) -> RiskLimits:
        try:
            raw = self._limits_file.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("risk limits must be an object")
            return parse_limits(payload, hashlib.sha256(raw).hexdigest())
        except (OSError, ValueError) as error:
            raise RiskUnavailableError(
                f"risk limits unreadable — file={self._limits_file.name} error={error}"
            ) from error

    def actions(self) -> tuple[RiskAction, ...]:
        path = self._folder / "actions.jsonl"
        if not path.exists():
            return ()
        try:
            return tuple(
                _action(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RiskUnavailableError(
                f"risk actions unreadable — file={path.name} error={error}"
            ) from error

    def add_action(self, action: RiskAction) -> None:
        try:
            self._append_action(action)
        except OSError as error:
            raise RiskUnavailableError(
                f"risk action not written — file=actions.jsonl error={error}"
            ) from error

    def _append_action(self, action: RiskAction) -> None:
        self._folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(
            {
                "at": action.at.isoformat(),
                "kind": action.kind.value,
                "operator": action.operator,
                "note": action.note,
                "peak_krw": action.peak_krw,
            },
            ensure_ascii=False,
        )
        fd = os.open(self._folder / "actions.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def save_verdict(self, verdict: RiskVerdict) -> None:
        path = self._verdict_path(verdict.plan_id)
        try:
            write_once(path, _encode_verdict(verdict))
        except OSError as error:
            raise RiskUnavailableError(
                f"risk verdict not written — file={path.name} error={error}"
            ) from error

    def verdict(self, plan_id: str) -> RiskVerdict | None:
        path = self._verdict_path(plan_id)
        if not path.exists():
            return None
        verdict = _decoded(path, VERDICT_SCHEMA, _decode_verdict)
        if verdict.plan_id != plan_id:
            raise RiskUnavailableError(
                f"risk verdict is for another plan — file={path.name} plan_id={verdict.plan_id}"
            )
        return verdict

    def save_day(self, record: RiskDay) -> None:
        path = self._folder / f"day-{record.day.isoformat()}.json"
        try:
            replace_file(path, _encode_day(record))
        except OSError as error:
            raise RiskUnavailableError(
                f"risk day not written — file={path.name} error={error}"
            ) from error

    def days(self) -> tuple[RiskDay, ...]:
        if not self._folder.exists():
            return ()
        return tuple(
            _decoded(path, RISK_DAY_SCHEMA, _decode_day)
            for path in sorted(self._folder.glob("day-*.json"))
        )

    def _verdict_path(self, plan_id: str) -> Path:
        if not PLAN_ID.fullmatch(plan_id):
            raise ValueError(f"invalid plan id — plan_id={plan_id!r}")
        return self._folder / f"verdict-{plan_id}.json"


def _decoded(path: Path, schema: str, decode: Callable[[dict[str, object]], _T]) -> _T:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != schema:
            raise ValueError(f"unexpected schema — expected={schema}")
        return decode(value)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RiskUnavailableError(
            f"risk record unreadable — file={path.name} error={error}"
        ) from error


def _encode_state(state: DrawdownState) -> dict[str, object]:
    return {
        "level": state.level.value,
        "peak_krw": state.peak_krw,
        "peak_day": None if state.peak_day is None else state.peak_day.isoformat(),
        "loss_bp": state.loss_bp,
        "last_day": None if state.last_day is None else state.last_day.isoformat(),
        "last_equity_krw": state.last_equity_krw,
    }


def _decode_state(value: object) -> DrawdownState:
    item = _mapping(value, "state")
    return DrawdownState(
        RiskLevel(item["level"]),
        _int(item["peak_krw"]),
        _day(item["peak_day"]),
        _int(item["loss_bp"]),
        _day(item["last_day"]),
        None if item["last_equity_krw"] is None else _int(item["last_equity_krw"]),
    )


def _encode_verdict(verdict: RiskVerdict) -> dict[str, object]:
    guarded = verdict.guarded
    return {
        "schema": VERDICT_SCHEMA,
        "plan_id": verdict.plan_id,
        "created_at": verdict.created_at.isoformat(),
        "limits_version": verdict.limits_version,
        "limits_sha256": verdict.limits_sha256,
        "state": _encode_state(verdict.state),
        "level": guarded.level.value,
        "exposure_bp": guarded.exposure_bp,
        "lines": [
            {
                "symbol": line.symbol,
                "current_shares": line.current_shares,
                "plan_target": line.plan_target,
                "target": line.target,
                "reasons": [reason.value for reason in line.reasons],
            }
            for line in guarded.lines
        ],
        "violations_since_last": list(verdict.violations_since_last),
        "plan_created_at": None
        if verdict.plan_created_at is None
        else verdict.plan_created_at.isoformat(),
        "close_from_plan": verdict.close_from_plan,
    }


def _decode_verdict(value: dict[str, object]) -> RiskVerdict:
    lines = tuple(
        GuardedLine(
            str(line["symbol"]),
            _int(line["current_shares"]),
            _int(line["plan_target"]),
            _int(line["target"]),
            tuple(GuardReason(reason) for reason in _sequence(line["reasons"])),
        )
        for line in (_mapping(item, "line") for item in _sequence(value["lines"]))
    )
    return RiskVerdict(
        str(value["plan_id"]),
        datetime.fromisoformat(str(value["created_at"])),
        _int(value["limits_version"]),
        str(value["limits_sha256"]),
        _decode_state(value["state"]),
        GuardedTargets(RiskLevel(value["level"]), _int(value["exposure_bp"]), lines),
        tuple(str(item) for item in _sequence(value["violations_since_last"])),
        plan_created_at=None
        if value["plan_created_at"] is None
        else datetime.fromisoformat(str(value["plan_created_at"])),
        close_from_plan=bool(value["close_from_plan"]),
    )


def _encode_day(record: RiskDay) -> dict[str, object]:
    review = record.review
    return {
        "schema": RISK_DAY_SCHEMA,
        "day": record.day.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "limits_version": record.limits_version,
        "limits_sha256": record.limits_sha256,
        "previous_level": record.previous_level.value,
        "state": _encode_state(record.state),
        "review": None
        if review is None
        else {
            "orders": [
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "plan_quantity": order.plan_quantity,
                    "guarded_quantity": order.guarded_quantity,
                    "filled_quantity": order.filled_quantity,
                    "reduced_by_risk": order.reduced_by_risk,
                    "unfilled_bp": order.unfilled_bp,
                    "shortfall_bp": order.shortfall_bp,
                }
                for order in review.orders
            ],
            "violations": list(review.violations),
            "notes": list(review.notes),
        },
    }


def _decode_day(value: dict[str, object]) -> RiskDay:
    review = value["review"]
    decoded = None
    if review is not None:
        item = _mapping(review, "review")
        decoded = PostTradeReview(
            tuple(
                ReviewedOrder(
                    str(order["symbol"]),
                    str(order["side"]),
                    _int(order["plan_quantity"]),
                    _int(order["guarded_quantity"]),
                    _int(order["filled_quantity"]),
                    _int(order["reduced_by_risk"]),
                    None if order["unfilled_bp"] is None else _int(order["unfilled_bp"]),
                    None if order["shortfall_bp"] is None else _int(order["shortfall_bp"]),
                )
                for order in (_mapping(entry, "order") for entry in _sequence(item["orders"]))
            ),
            tuple(str(entry) for entry in _sequence(item["violations"])),
            tuple(str(entry) for entry in _sequence(item["notes"])),
        )
    return RiskDay(
        date.fromisoformat(str(value["day"])),
        datetime.fromisoformat(str(value["recorded_at"])),
        _int(value["limits_version"]),
        str(value["limits_sha256"]),
        RiskLevel(value["previous_level"]),
        _decode_state(value["state"]),
        decoded,
    )


def _action(value: object) -> RiskAction:
    item = _mapping(value, "action")
    peak = item.get("peak_krw")
    return RiskAction(
        datetime.fromisoformat(str(item["at"])),
        ActionKind(item["kind"]),
        str(item["operator"]),
        str(item.get("note", "")),
        None if peak is None else _int(peak),
    )


def _day(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"record field must be an integer — value={value!r}")
    return value


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"record field must be an object — field={field}")
    return value


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"record field must be a list — value={value!r}")
    return value
