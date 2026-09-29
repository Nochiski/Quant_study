"""SQLite 파일 claim·application_id·DDL manifest 대조 (검증 랩 spec D3).

전략 revision·어시스턴트·연구 기록 DB는 수명이 달라 파일을 나누지만 여는 규칙은 같다. 이 모듈이 그
규칙의 단일 정본이다.

- 빈 파일(application_id 0, user_version 0, 스키마 객체 없음)만 claim한다. SQLite가 남기는 내부
  객체(`sqlite_sequence`)도 빈 파일의 증거로 보지 않는다.
- 다른 application_id를 만나면 거부한다. 남의 파일을 이 스키마로 덮어쓰지 않는다.
- 이 서버보다 새 버전 파일은 거부한다.
- 옛 버전은 그 버전의 manifest를 먼저 확인하고 한 트랜잭션에서 올린다. 중간에 실패하면 원래 버전
  그대로 남는다.
- 저장된 DDL은 선언과 글자 단위로 같아야 한다. CHECK·부분 인덱스가 불변식을 집행하므로 손으로
  완화한 파일을 그대로 열면 불변식이 조용히 사라진다. 따옴표 안의 대소문자·공백은 데이터라
  정규화하지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

# (객체 종류, 이름, DDL). 이미 사용자 디스크에 쓰인 버전의 선언은 고치지 않는다 — manifest 대조가
# 글자 단위라 고치면 옛 파일이 올리기 전에 거부된다.
SchemaObjects: TypeAlias = tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SchemaUpgrade:
    """`version` 파일을 한 판 올리는 단계. `objects`는 그 버전의 선언이고 올리기 전에 대조한다.

    `apply`는 호출자 트랜잭션 안에서 돈다. `user_version`은 `ensure_schema`가 올린다.
    """

    version: int
    objects: SchemaObjects
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class SchemaContract:
    """한 어댑터 DB 파일의 판본 계약.

    `label`은 메시지에 쓰는 이름이고 `error`는 그 어댑터의 저장 오류 타입이다. `verify`는 커밋 전에
    도는 추가 검사다(예: 외래 키를 끄고 올린 뒤의 고아 행 검사).
    """

    label: str
    application_id: int
    version: int
    objects: SchemaObjects
    error: type[Exception]
    upgrades: tuple[SchemaUpgrade, ...] = ()
    verify: Callable[[sqlite3.Connection], None] | None = None


def ensure_schema(connection: sqlite3.Connection, contract: SchemaContract) -> None:
    """빈 파일은 claim해 지금 버전을 만들고, 옛 파일은 올리고, 남의 파일·미래 버전은 거부한다."""
    error = contract.error
    try:
        connection.execute("BEGIN IMMEDIATE")
        application_id = _pragma_int(connection, "application_id", contract)
        version = _pragma_int(connection, "user_version", contract)

        if application_id == 0:
            footprint = tuple(_objects(connection, contract))
            if version != 0 or footprint:
                raise error(
                    "refusing to claim a non-empty SQLite database without this "
                    f"application id — label={contract.label} user_version={version} "
                    f"objects={footprint}"
                )
            for _object_type, _name, statement in contract.objects:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {contract.application_id}")
            version = contract.version
            connection.execute(f"PRAGMA user_version = {version}")
        elif application_id != contract.application_id:
            raise error(
                "SQLite file belongs to another application — "
                f"label={contract.label} application_id={application_id} "
                f"expected={contract.application_id}"
            )

        if version > contract.version:
            raise error(
                f"SQLite {contract.label} schema is newer than this server — "
                f"stored={version} supported={contract.version}"
            )
        for upgrade in contract.upgrades:
            if version == upgrade.version:
                _validate_manifest(connection, contract, upgrade.objects, version=version)
                upgrade.apply(connection)
                version += 1
                connection.execute(f"PRAGMA user_version = {version}")
        if version != contract.version:
            raise error(
                f"SQLite {contract.label} schema has no migration path — "
                f"stored={version} supported={contract.version}"
            )
        _validate_manifest(connection, contract, contract.objects, version=version)
        if contract.verify is not None:
            contract.verify(connection)
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _pragma_int(connection: sqlite3.Connection, name: str, contract: SchemaContract) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or not isinstance(row[0], int):  # pragma: no cover - SQLite 불변식
        raise contract.error(
            f"SQLite PRAGMA {name} returned an invalid value — label={contract.label} row={row!r}"
        )
    return row[0]


def _objects(
    connection: sqlite3.Connection, contract: SchemaContract
) -> dict[tuple[str, str], str | None]:
    """파일에 남아 있는 모든 스키마 객체와 그 DDL(바깥 공백만 뗀 것)."""
    rows = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_schema
        ORDER BY type COLLATE BINARY, name COLLATE BINARY
        """
    ).fetchall()
    objects: dict[tuple[str, str], str | None] = {}
    for object_type, name, sql in rows:
        if not isinstance(object_type, str) or not isinstance(name, str):
            raise contract.error(
                f"SQLite {contract.label} schema contains an invalid object identity — "
                f"type={object_type!r} name={name!r}"
            )
        if sql is not None and not isinstance(sql, str):  # pragma: no cover - SQLite 불변식
            raise contract.error(
                f"SQLite {contract.label} schema contains an object with invalid SQL — name={name}"
            )
        objects[(object_type, name)] = None if sql is None else sql.strip()
    return objects


def _validate_manifest(
    connection: sqlite3.Connection,
    contract: SchemaContract,
    manifest: SchemaObjects,
    *,
    version: int,
) -> None:
    expected = {(object_type, name): statement.strip() for object_type, name, statement in manifest}
    actual = _objects(connection, contract)
    if actual == expected:
        return
    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    incompatible = sorted(
        key for key in actual.keys() & expected.keys() if actual[key] != expected[key]
    )
    raise contract.error(
        f"SQLite {contract.label} schema does not match version {version} — "
        f"missing={missing} unexpected={unexpected} incompatible={incompatible}"
    )
