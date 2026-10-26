"""주문안·승인을 JSON 파일로 한 번만 쓴다(S07).

`<폴더>/plans/<plan_id>.json`과 `<plan_id>.approval.json`. 임시 파일에 다 쓴 뒤 `os.link`로
제자리에 붙이므로, 이미 있는 파일을 덮어쓰지 않고 반쯤 쓴 파일이 보이지 않는다. 계좌 사실이 담겨
600으로 만든다.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    Approval,
    PlanExistsError,
    PlanRecord,
    SignalBasis,
    SignalRecord,
)
from strategy_workbench.domain.rebalance.facade.rebalance import (
    LineCause,
    RebalanceAction,
    RebalanceLine,
    RebalancePlan,
    RebalancePolicy,
)

PLAN_SCHEMA = "weekly-rebalance-plan/1"
APPROVAL_SCHEMA = "weekly-rebalance-approval/1"
_PLAN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class JsonPlanStore:
    def __init__(self, folder: Path) -> None:
        self._folder = folder / "plans"

    def save(self, record: PlanRecord) -> None:
        self._write_once(self._path(record.plan_id, ""), _encode_plan(record))

    def load(self, plan_id: str) -> PlanRecord | None:
        path = self._path(plan_id, "")
        if not path.exists():
            return None
        return _decode_plan(_read(path, PLAN_SCHEMA))

    def save_approval(self, plan_id: str, approval: Approval) -> None:
        self._write_once(
            self._path(plan_id, ".approval"),
            {
                "schema": APPROVAL_SCHEMA,
                "approved_at": approval.approved_at.isoformat(),
                "approver": approval.approver,
                "note": approval.note,
                "no_trade": list(approval.no_trade),
            },
        )

    def approval(self, plan_id: str) -> Approval | None:
        path = self._path(plan_id, ".approval")
        if not path.exists():
            return None
        value = _read(path, APPROVAL_SCHEMA)
        return Approval(
            datetime.fromisoformat(str(value["approved_at"])),
            str(value["approver"]),
            str(value.get("note", "")),
            _strings(value.get("no_trade")),
        )

    def plan_ids(self) -> tuple[str, ...]:
        if not self._folder.exists():
            return ()
        return tuple(
            sorted(
                path.stem
                for path in self._folder.glob("*.json")
                if not path.stem.endswith(".approval")
            )
        )

    def _path(self, plan_id: str, suffix: str) -> Path:
        if not _PLAN_ID.fullmatch(plan_id):
            raise ValueError(f"invalid plan id — plan_id={plan_id!r}")
        return self._folder / f"{plan_id}{suffix}.json"

    def _write_once(self, path: Path, value: dict[str, object]) -> None:
        write_once(path, value)


def write_once(path: Path, value: dict[str, object]) -> None:
    """임시 파일에 다 쓴 뒤 `os.link`로 붙인다. 이미 있으면 `PlanExistsError`(덮어쓰지 않는다)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        raise PlanExistsError(f"already recorded — file={path.name}")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise PlanExistsError(f"already recorded — file={path.name}") from error
    finally:
        os.unlink(temporary)


def _read(path: Path, schema: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise ValueError(f"unexpected record schema — file={path.name} expected={schema}")
    return value


def _encode_plan(record: PlanRecord) -> dict[str, object]:
    plan, policy, signal, account = record.plan, record.plan.policy, record.signal, record.account
    return {
        "schema": PLAN_SCHEMA,
        "plan_id": record.plan_id,
        "created_at": record.created_at.isoformat(),
        "signal": {
            "model_id": signal.model_id,
            "build_id": signal.build_id,
            "signal_date": signal.signal_date,
            "scores_sha256": signal.scores_sha256,
            "prices_build_id": signal.prices_build_id,
            "ranks": [[symbol, rank] for symbol, rank in signal.ranks],
            "previous_build_id": signal.previous_build_id,
            "anomalies": list(signal.anomalies),
            "names": dict(signal.names),
            "gap_check_skipped": signal.gap_check_skipped,
            "basis": signal.basis.value,
        },
        "account": {
            "cash_krw": account.cash_krw,
            "holdings": dict(sorted(account.holdings.items())),
            "reconciled": account.reconciled,
            "detail": account.detail,
        },
        "plan": {
            "policy": {
                "slots": policy.slots,
                "keep_rank": policy.keep_rank,
                "cash_reserve_bp": policy.cash_reserve_bp,
                "resize_band_bp": policy.resize_band_bp,
                "fee_rate_bp": policy.fee_rate_bp,
            },
            "cash_krw": plan.cash_krw,
            "equity_krw": plan.equity_krw,
            "target_value_krw": plan.target_value_krw,
            "lines": [
                {
                    "symbol": line.symbol,
                    "action": line.action.value,
                    "rank": line.rank,
                    "current_shares": line.current_shares,
                    "target_shares": line.target_shares,
                    "reference_price_krw": line.reference_price_krw,
                    "reason": line.reason,
                    "review": line.review,
                    "cause": None if line.cause is None else line.cause.value,
                    "missing_why": line.missing_why,
                    "last_seen": line.last_seen,
                }
                for line in plan.lines
            ],
        },
    }


def _decode_plan(value: dict[str, object]) -> PlanRecord:
    signal, account, plan = (_object(value, key) for key in ("signal", "account", "plan"))
    policy = _object(plan, "policy")
    reconciled = account["reconciled"]
    return PlanRecord(
        str(value["plan_id"]),
        datetime.fromisoformat(str(value["created_at"])),
        SignalRecord(
            str(signal["model_id"]),
            str(signal["build_id"]),
            str(signal["signal_date"]),
            str(signal["scores_sha256"]),
            str(signal["prices_build_id"]),
            tuple((str(pair[0]), _int(pair[1], "rank")) for pair in _list(signal, "ranks")),
            None if signal.get("previous_build_id") is None else str(signal["previous_build_id"]),
            _strings(signal.get("anomalies")),
            _names(signal.get("names")),
            None if signal.get("gap_check_skipped") is None else str(signal["gap_check_skipped"]),
            SignalBasis(str(signal.get("basis", SignalBasis.EVENING.value))),
        ),
        AccountState(
            _int(account["cash_krw"], "cash_krw"),
            {
                str(symbol): _int(shares, "holdings")
                for symbol, shares in _object(account, "holdings").items()
            },
            None if reconciled is None else bool(reconciled),
            str(account.get("detail", "")),
        ),
        RebalancePlan(
            RebalancePolicy(**{key: _int(item, key) for key, item in policy.items()}),
            _int(plan["cash_krw"], "cash_krw"),
            _int(plan["equity_krw"], "equity_krw"),
            _int(plan["target_value_krw"], "target_value_krw"),
            tuple(_line(line) for line in _list(plan, "lines")),
        ),
    )


def _line(value: object) -> RebalanceLine:
    if not isinstance(value, dict):
        raise ValueError("plan line must be an object")
    rank, price = value.get("rank"), value.get("reference_price_krw")
    return RebalanceLine(
        str(value["symbol"]),
        RebalanceAction(value["action"]),
        None if rank is None else _int(rank, "rank"),
        _int(value["current_shares"], "current_shares"),
        _int(value["target_shares"], "target_shares"),
        None if price is None else _int(price, "reference_price_krw"),
        str(value["reason"]),
        bool(value.get("review", False)),
        None if value.get("cause") is None else LineCause(value["cause"]),
        str(value.get("missing_why", "")),
        str(value.get("last_seen", "")),
    )


def _names(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(symbol), str(name)) for symbol, name in value.items())


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"record field must be an integer — field={field} value={value!r}")
    return value


def _object(value: dict[str, object], key: str) -> dict[str, object]:
    item = value[key]
    if not isinstance(item, dict):
        raise ValueError(f"record field must be an object — key={key}")
    return item


def _list(value: dict[str, object], key: str) -> list[list[object]]:
    item = value[key]
    if not isinstance(item, list):
        raise ValueError(f"record field must be a list — key={key}")
    return item
