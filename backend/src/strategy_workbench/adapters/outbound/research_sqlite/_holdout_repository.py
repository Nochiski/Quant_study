"""기존 연구 DB의 별도 모의 등록·claim 원장. 실제 봉인 권위나 trial 행은 쓰지 않는다."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.holdout_registration.facade.registration import (
    HoldoutSimulationRejectedError,
    HoldoutSimulationStorageError,
)
from strategy_workbench.domain.backtest.facade.holdout import (
    HoldoutRegistration,
    HoldoutSimulationClaim,
    HoldoutSimulationResult,
    SimulationClaimStatus,
)
from strategy_workbench.domain.canonical.facade.encoding import (
    canonical_json_spec_hash,
    canonical_payload_json,
)

from ._errors import ResearchStorageError
from ._schema import migrate_schema

T = TypeVar("T")
_REGISTRATION = TypeAdapter(HoldoutRegistration)
_RESULT = TypeAdapter(HoldoutSimulationResult)


class HoldoutSimulationRepositoryError(HoldoutSimulationStorageError, ResearchStorageError):
    pass


def _encode(adapter: TypeAdapter[T], value: T) -> str:
    return canonical_payload_json(adapter.dump_python(value, mode="json"))


def _decode(adapter: TypeAdapter[T], payload: str, digest: str) -> T:
    try:
        if canonical_json_spec_hash(payload) != digest:
            raise ValueError("simulation payload digest mismatch")
        # 임시 fixture를 포함해 중복 JSON key는 의미 검증 전에 거절한다.
        import json

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate simulation JSON key")
                result[key] = value
            return result

        data = json.loads(payload, object_pairs_hook=pairs)
        if canonical_payload_json(data) != payload:
            raise ValueError("noncanonical simulation payload")
        return adapter.validate_python(data)
    except (ValueError, TypeError, KeyError) as error:
        raise HoldoutSimulationRepositoryError("simulation payload is unreadable") from error


class SQLiteHoldoutSimulationLedger:
    """모의 core의 명시 조립 전용 저장소. 일반 bootstrap은 이 어댑터를 생성하지 않는다."""

    def __init__(self, path: str | Path) -> None:
        if path is None:
            raise ValueError("simulation ledger requires an explicit durable database path")
        try:
            self._database = SqliteDatabase(
                path,
                label="research simulation ledger",
                error=HoldoutSimulationRepositoryError,
                prepare=migrate_schema,
            )
        except ResearchStorageError as error:
            raise HoldoutSimulationRepositoryError(
                "simulation research schema is unreadable"
            ) from error

    def close(self) -> None:
        self._database.close()

    def register(self, registration: HoldoutRegistration) -> None:
        payload = _encode(_REGISTRATION, registration)
        with self._database.transaction(write=True) as connection:
            previous = connection.execute(
                "SELECT payload FROM holdout_simulation_registrations WHERE registration_id=?",
                (registration.registration_id,),
            ).fetchone()
            if previous is not None:
                if previous["payload"] != payload:
                    raise HoldoutSimulationRejectedError("registration identity is already bound")
                return
            connection.execute(
                "INSERT INTO holdout_simulation_registrations VALUES (?,?,?,?)",
                (
                    registration.registration_id,
                    registration.registration_hash,
                    payload,
                    canonical_json_spec_hash(payload),
                ),
            )

    def _registration(
        self, connection: sqlite3.Connection, registration_id: str
    ) -> HoldoutRegistration:
        row = connection.execute(
            "SELECT * FROM holdout_simulation_registrations WHERE registration_id=?",
            (registration_id,),
        ).fetchone()
        if row is None:
            raise HoldoutSimulationRejectedError("simulation registration not found")
        registration = _decode(_REGISTRATION, row["payload"], row["payload_sha256"])
        if (
            registration.registration_id != registration_id
            or registration.registration_hash != row["registration_hash"]
        ):
            raise HoldoutSimulationRepositoryError("simulation registration row identity mismatch")
        return registration

    def registration(self, registration_id: str) -> HoldoutRegistration:
        with self._database.transaction(write=False) as connection:
            return self._registration(connection, registration_id)

    def _claim_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> HoldoutSimulationClaim:
        registration = self._registration(connection, row["registration_id"])
        if registration.registration_hash != row["registration_hash"]:
            raise HoldoutSimulationRepositoryError(
                "simulation claim binding differs from registration"
            )
        try:
            status = SimulationClaimStatus(row["status"])
            result = None
            if status is SimulationClaimStatus.OBSERVED:
                observed = connection.execute(
                    "SELECT * FROM holdout_simulation_results WHERE request_id=?",
                    (row["request_id"],),
                ).fetchone()
                if observed is None:
                    status = SimulationClaimStatus.INTERRUPTED_UNKNOWN
                else:
                    try:
                        result = _decode(_RESULT, observed["payload"], observed["payload_sha256"])
                        if (
                            result.registration_hash != registration.registration_hash
                            or tuple(item.criterion for item in result.outcomes)
                            != registration.criteria
                        ):
                            raise HoldoutSimulationRepositoryError(
                                "simulation result criteria differ"
                            )
                    except HoldoutSimulationRepositoryError:
                        status = SimulationClaimStatus.STORAGE_UNKNOWN
                        result = None
            return HoldoutSimulationClaim(
                row["request_id"],
                row["registration_id"],
                row["registration_hash"],
                datetime.fromisoformat(row["claimed_at"]),
                status,
                None
                if row["completed_at"] is None
                else datetime.fromisoformat(row["completed_at"]),
                result,
                row["namespace"],
                row["window_id"],
            )
        except (ValueError, TypeError) as error:
            raise HoldoutSimulationRepositoryError("simulation claim is unreadable") from error

    def get(self, request_id: str) -> HoldoutSimulationClaim | None:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM holdout_simulation_claims WHERE request_id=?", (request_id,)
            ).fetchone()
            return None if row is None else self._claim_row(connection, row)

    def claim(self, claim: HoldoutSimulationClaim) -> bool:
        if claim.status is not SimulationClaimStatus.CLAIMED:
            raise HoldoutSimulationRejectedError("only a new pending claim can be admitted")
        with self._database.transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT registration_id FROM holdout_simulation_claims WHERE request_id=?",
                (claim.request_id,),
            ).fetchone()
            if existing is not None:
                if existing["registration_id"] != claim.registration_id:
                    raise HoldoutSimulationRejectedError(
                        "request UUID belongs to another registration"
                    )
                return False
            registration = self._registration(connection, claim.registration_id)
            if registration.registration_hash != claim.registration_hash:
                raise HoldoutSimulationRejectedError("claim registration binding differs")
            if (
                connection.execute(
                    "SELECT 1 FROM holdout_simulation_claims WHERE namespace=? AND window_id=?",
                    (claim.namespace, claim.window_id),
                ).fetchone()
                is not None
            ):
                raise HoldoutSimulationRejectedError("simulation window is already consumed")
            connection.execute(
                "INSERT INTO holdout_simulation_claims VALUES (?,?,?,?,?,?,?,?)",
                (
                    claim.request_id,
                    claim.registration_id,
                    claim.registration_hash,
                    claim.namespace,
                    claim.window_id,
                    datetime_text(claim.claimed_at, field="claimed_at"),
                    claim.status.value,
                    None,
                ),
            )
        return True

    def finish(self, claim: HoldoutSimulationClaim) -> None:
        if claim.status not in (
            SimulationClaimStatus.OBSERVED,
            SimulationClaimStatus.INTERRUPTED_UNKNOWN,
        ):
            raise HoldoutSimulationRejectedError("unsupported terminal simulation transition")
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM holdout_simulation_claims WHERE request_id=?", (claim.request_id,)
            ).fetchone()
            if row is None or (row["registration_id"], row["registration_hash"]) != (
                claim.registration_id,
                claim.registration_hash,
            ):
                raise HoldoutSimulationRejectedError("terminal claim identity differs")
            if row["status"] != SimulationClaimStatus.CLAIMED:
                raise HoldoutSimulationRejectedError("simulation claim is already terminal")
            connection.execute(
                "UPDATE holdout_simulation_claims SET status=?,completed_at=? WHERE request_id=?",
                (
                    claim.status.value,
                    None
                    if claim.completed_at is None
                    else datetime_text(claim.completed_at, field="completed_at"),
                    claim.request_id,
                ),
            )
            if claim.result is not None:
                payload = _encode(_RESULT, claim.result)
                connection.execute(
                    "INSERT INTO holdout_simulation_results VALUES (?,?,?)",
                    (claim.request_id, payload, canonical_json_spec_hash(payload)),
                )

    def interrupt_claimed(self, completed_at: datetime) -> None:
        with self._database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE holdout_simulation_claims SET status=?,completed_at=? WHERE status=?",
                (
                    SimulationClaimStatus.INTERRUPTED_UNKNOWN.value,
                    datetime_text(completed_at, field="completed_at"),
                    SimulationClaimStatus.CLAIMED.value,
                ),
            )
