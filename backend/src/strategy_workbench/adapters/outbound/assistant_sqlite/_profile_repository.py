"""`ProviderProfileRepository`의 SQLite 구현 (설계 spec D5, D9).

비밀은 여기 없다. 프로파일 행에는 키가 들어가지 않고, 키는 `secrets_local`이 사용자 설정
디렉터리 파일에 따로 가진다(완료 정의 3).

"활성은 최대 하나"는 코드가 아니라 **부분 유니크 인덱스**가 집행한다. 서비스가 순서를 잘못
밟거나 두 프로세스가 동시에 활성을 바꿔도 DB가 두 번째를 거부한다.
"""

from __future__ import annotations

import sqlite3

from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderProfileNotFoundError,
)
from strategy_workbench.domain.assistant.facade.models import ProviderKind, ProviderProfile

from ._database import (
    AssistantDatabase,
    datetime_text,
    datetime_value,
    int_value,
    optional_text_value,
    text_value,
)
from ._errors import AssistantStorageError

_SELECT = """
SELECT profile_id, ordinal, kind, label, model, base_url, created_at, active
FROM provider_profiles
"""


class SQLiteProviderProfileRepository:
    """등록 순서를 `ordinal`로 명시해 보관하는 프로파일 저장소."""

    def __init__(self, database: AssistantDatabase) -> None:
        self._database = database

    def list(self) -> tuple[ProviderProfile, ...]:
        with self._database.transaction(write=False) as connection:
            rows = connection.execute(f"{_SELECT} ORDER BY ordinal ASC").fetchall()
        return tuple(_decode(row) for row in rows)

    def get(self, profile_id: str) -> ProviderProfile:
        with self._database.transaction(write=False) as connection:
            row = self._row(connection, profile_id)
        return _decode(row)

    def add(self, profile: ProviderProfile) -> None:
        """등록 순서 맨 뒤에 붙인다.

        `active=True`인데 이미 활성이 있으면 부분 유니크 인덱스가 거부한다. 활성 전환은
        `set_active`의 일이고 `add`가 조용히 남을 끌어내리지 않는다(놀람 최소화).
        """
        values = _encode(profile)
        with self._database.transaction(write=True) as connection:
            next_ordinal = self._next_ordinal(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO provider_profiles (
                        profile_id, ordinal, kind, label, model, base_url, created_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (values[0], next_ordinal, *values[1:]),
                )
            except sqlite3.IntegrityError as error:
                raise AssistantStorageError(
                    "could not store the provider profile — "
                    f"profile_id={profile.profile_id!r} active={profile.active} "
                    f"ordinal={next_ordinal} ({error})"
                ) from error

    def delete(self, profile_id: str) -> None:
        with self._database.transaction(write=True) as connection:
            self._row(connection, profile_id)
            connection.execute("DELETE FROM provider_profiles WHERE profile_id = ?", (profile_id,))

    def set_active(self, profile_id: str) -> None:
        """한 트랜잭션에서 전부 내리고 하나만 올린다.

        순서가 중요하다. 먼저 올리면 활성이 둘인 순간이 생겨 부분 유니크 인덱스가 막는다.
        """
        with self._database.transaction(write=True) as connection:
            self._row(connection, profile_id)
            connection.execute("UPDATE provider_profiles SET active = 0 WHERE active = 1")
            connection.execute(
                "UPDATE provider_profiles SET active = 1 WHERE profile_id = ?", (profile_id,)
            )

    @staticmethod
    def _row(connection: sqlite3.Connection, profile_id: str) -> sqlite3.Row:
        row = connection.execute(f"{_SELECT} WHERE profile_id = ?", (profile_id,)).fetchone()
        if row is None:
            raise ProviderProfileNotFoundError(profile_id)
        return row

    @staticmethod
    def _next_ordinal(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT MAX(ordinal) FROM provider_profiles").fetchone()
        current = row[0] if row is not None else None
        if current is None:
            return 0
        if not isinstance(current, int) or isinstance(current, bool):
            raise AssistantStorageError(
                f"stored provider profile ordinal is not an integer — type={type(current).__name__}"
            )
        return current + 1


def _encode(profile: ProviderProfile) -> tuple[object, ...]:
    return (
        profile.profile_id,
        profile.kind.value,
        profile.label,
        profile.model,
        profile.base_url,
        datetime_text(profile.created_at, field="provider profile created_at"),
        1 if profile.active else 0,
    )


def _decode(row: sqlite3.Row) -> ProviderProfile:
    raw_kind = text_value(row, "kind")
    try:
        kind = ProviderKind(raw_kind)
    except ValueError as error:
        raise AssistantStorageError(
            f"stored provider profile has an unknown kind — "
            f"profile_id={text_value(row, 'profile_id')!r} kind={raw_kind!r}"
        ) from error
    active = int_value(row, "active")
    return ProviderProfile(
        profile_id=text_value(row, "profile_id"),
        kind=kind,
        label=text_value(row, "label"),
        model=text_value(row, "model"),
        base_url=optional_text_value(row, "base_url"),
        created_at=datetime_value(row["created_at"], field="provider profile created_at"),
        active=active == 1,
    )
