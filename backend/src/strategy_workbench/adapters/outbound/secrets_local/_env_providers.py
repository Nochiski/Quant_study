"""AI 공급자 프로파일과 그 API 키를 `auto_trading/.env` 한 묶음에 둔다(2026-10-24 사용자 결정).

`ProviderProfileRepository`와 `ProviderSecretStore` 두 포트를 한 객체가 구현한다.
메타데이터(SQLite)와
키(`secrets.json`)가 갈라져 있으면 체크아웃마다 다른 `.local` DB 때문에 "키는 있는데 연결은 없음"이
생겼다. 이제 둘이 같은 파일의 같은 묶음에 있어 어느 체크아웃·OS에서 켜도 같은 연결이 보인다.

묶음 모양(번호는 등록 순서, 1부터):

    # Quant Workbench AI 공급자 — 앱 설정 화면이 고칩니다
    STRATEGY_WORKBENCH_AI_ACTIVE=<profile_id>
    STRATEGY_WORKBENCH_AI_1_ID=<profile_id>
    STRATEGY_WORKBENCH_AI_1_KIND=anthropic
    STRATEGY_WORKBENCH_AI_1_LABEL="내 Claude"
    STRATEGY_WORKBENCH_AI_1_MODEL=claude-…
    STRATEGY_WORKBENCH_AI_1_BASE_URL=https://…      (없으면 줄이 없다)
    STRATEGY_WORKBENCH_AI_1_CREATED_AT=2026-10-24T05:00:00+00:00
    STRATEGY_WORKBENCH_AI_1_API_KEY=sk-…            (키가 없으면 줄이 없다)

"활성은 최대 하나"는 `ACTIVE` 한 줄이 집행한다. 값·경로는 어떤 예외 문장에도 싣지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from strategy_workbench.application.assistant_chat.facade.ports import (
    ProviderProfileNotFoundError,
    ProviderSecretMissingError,
    ProviderStorageUnavailableError,
)
from strategy_workbench.domain.assistant.facade.models import ProviderKind, ProviderProfile

from ._env_file import CredentialsEnvFile, EnvProblem
from ._errors import SecretStoreStorageError

__all__ = [
    "AI_ENV_PREFIX",
    "AI_IMPORT_NAME",
    "EnvFileProviderSecrets",
    "EnvFileProviderStore",
    "ProviderStoreConflictError",
]

AI_ENV_PREFIX = "STRATEGY_WORKBENCH_AI_"
_HEADER = "Quant Workbench AI 공급자 — 앱 설정 화면이 고칩니다"
_ACTIVE = f"{AI_ENV_PREFIX}ACTIVE"
# 번호는 원문 문자열 그대로 묶는다(`_01_`과 `_1_`은 다른 항목, 3차 리뷰 7). 접미어는 우리가 모르는
# 소문자·숫자도 그 항목에 붙여 다시 번호를 매길 때 함께 옮긴다.
_INDEXED = re.compile(rf"{AI_ENV_PREFIX}(\d+)_([A-Za-z0-9_]+)")
_FIELDS = ("ID", "KIND", "LABEL", "MODEL", "BASE_URL", "CREATED_AT", "API_KEY")
_REQUIRED = ("ID", "KIND", "MODEL", "CREATED_AT")


@dataclass(frozen=True)
class _Entry:
    profile: ProviderProfile
    secret: str | None
    # 이 저장소가 모르는 같은 번호의 줄(예: 사용자가 붙인 `_NOTE`).
    # 쓸 때 그대로 다시 적는다(SoT 리뷰 M4).
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Loaded:
    """한 번 읽은 묶음.

    `broken`은 읽지 못한 항목의 줄을 값 그대로 들고 있다(쓸 때 다시 적는다).
    """

    entries: list[_Entry]
    broken: list[dict[str, str]]
    problems: tuple[EnvProblem, ...]
    # 번호도 `ACTIVE`도 아닌 우리 접두어 줄. 이름·값 그대로 다시 적는다(SoT 리뷰 M4).
    loose: tuple[tuple[str, str], ...] = ()
    # `ACTIVE`가 읽지 못한 항목을 가리키면 그 id. 쓸 때 그대로 둔다(재리뷰 2).
    broken_active: str | None = None


AI_IMPORT_NAME = "AI_PROVIDERS"


class ProviderStoreConflictError(RuntimeError):
    """저장소 상태와 맞지 않는 요청(이미 있는 id, 비어 있지 않은 묶음으로의 이전 등).

    파일 I/O·권한 실패가 아니라서 "저장 불가"로 바꾸지 않는다(SoT 리뷰 경미 1). 사용자에게 파일을
    고치라고 안내하면 틀린 조치가 된다.
    """


class EnvFileProviderStore:
    """프로파일 저장소이자 비밀 저장소. 모든 연산이 파일을 한 번 읽고(쓰면 한 번 쓰고) 끝난다.

    **읽지 못한 부분이 있어도 서버는 뜬다**(리뷰 1·2). 읽기는 멀쩡한 항목만 보이고(파일 전체를 못
    읽으면 공급자가 없는 것처럼), 빠진 줄이 있는 항목은 `problems()`가 사유를 알린다. 쓰기는 그런
    항목의 줄을 값 그대로 다시 적어 사용자가 손으로 고칠 때까지 잃지 않는다. 파일 전체를 못 읽으면
    덮어쓰지 않고 실패한다.
    """

    def __init__(self, file: CredentialsEnvFile) -> None:
        self._file = file
        # 같은 스냅숏을 다시 파싱하지 않는다. 목록 + 키마다 조회(summaries)가 한 번 파싱으로 끝난다
        # (마지막 리뷰 9).
        self._parsed: tuple[object, _Loaded] | None = None

    @property
    def file(self) -> CredentialsEnvFile:
        return self._file

    def problems(self) -> tuple[EnvProblem, ...]:
        """설정 화면이 보일 읽기 문제.

        파일 전체 문제는 `CredentialsEnvFile.file_problem`이 따로 말한다.
        """
        try:
            return self._load().problems
        except SecretStoreStorageError:
            return ()

    # -- ProviderProfileRepository ------------------------------------------------------------

    def ensure_writable(self) -> None:
        """연결 테스트 전에 부른다. 파일을 읽을 수 없거나 폴더·잠금 파일에 쓸 수 없으면 거절한다.

        실제로 쓰지는 않는다(마지막 리뷰 2).
        """
        problem = self._file.writable_problem()
        if problem is not None:
            raise ProviderStorageUnavailableError(problem.reason)

    def list(self) -> tuple[ProviderProfile, ...]:
        return tuple(entry.profile for entry in self._readable_entries())

    def get(self, profile_id: str) -> ProviderProfile:
        """하나를 콕 집는 조회는 파일을 못 읽으면 404가 아니라 저장 불가로 알린다(마지막 리뷰 3)."""
        return self._find(self._writable_entries(), profile_id).profile

    def add(self, profile: ProviderProfile) -> None:
        """맨 뒤에 붙인다.

        활성으로 들어왔는데 이미 활성이 있으면(동시 생성 등) 거절하지
        않고 비활성으로 넣는다 — 서비스는
        저장소가 실제로 남긴 값을 돌려준다(마지막 리뷰 5).
        """

        def run() -> None:
            loaded = self._load()
            entries = loaded.entries
            incoming = profile
            if any(entry.profile.profile_id == incoming.profile_id for entry in entries):
                raise ProviderStoreConflictError(
                    f"provider profile already exists — profile_id={incoming.profile_id!r}"
                )
            if incoming.active and loaded.broken_active is not None:
                # 활성이 읽지 못한 항목에 있다. 그 표시를 빼앗지 않고 새 항목은 비활성으로 둔다
                # (재리뷰 2). 사용자는 파일을 고치거나 화면에서 새 항목을 골라 활성으로 바꾼다.
                incoming = replace(incoming, active=False)
            if incoming.active and any(entry.profile.active for entry in entries):
                incoming = replace(incoming, active=False)
            self._write(
                [*entries, _Entry(incoming, None)],
                loaded.broken,
                loaded.loose,
                loaded.broken_active,
            )

        self._mutate(run)

    def delete(self, profile_id: str) -> None:
        """없으면 `ProviderProfileNotFoundError`. 남은 키가 있으면 같이 지운다.

        주인 없는 키를 남기지 않는다.
        """
        # 없는 항목이면 잠금(폴더 생성) 전에 알린다(리뷰 6). 파일을 못 읽으면 404로 숨기지 않는다.
        self._find(self._writable_entries(), profile_id)

        def run() -> None:
            loaded = self._load()
            entry = self._find(loaded.entries, profile_id)
            self._write(
                [item for item in loaded.entries if item is not entry],
                loaded.broken,
                loaded.loose,
                loaded.broken_active,
            )

        self._mutate(run)

    def set_active(self, profile_id: str) -> None:
        self._find(self._writable_entries(), profile_id)

        def run() -> None:
            loaded = self._load()
            self._find(loaded.entries, profile_id)
            self._write(
                [
                    replace(
                        item,
                        profile=replace(item.profile, active=item.profile.profile_id == profile_id),
                    )
                    for item in loaded.entries
                ],
                loaded.broken,
                loaded.loose,
                None,  # 사용자가 고른 활성이 읽지 못한 항목의 활성을 대신한다.
            )

        self._mutate(run)

    def import_profiles(self, entries: Sequence[tuple[ProviderProfile, str | None]]) -> None:
        """예전 저장소에서 옮겨 온 프로파일·키를 한 번의 쓰기로 넣는다. 묶음이 비어 있을 때만."""

        def run() -> None:
            loaded = self._load()
            if loaded.entries or loaded.broken:
                raise ProviderStoreConflictError(
                    "the credentials env file already has provider profiles — "
                    f"incoming={len(entries)}"
                )
            if sum(1 for profile, _ in entries if profile.active) > 1:
                raise ProviderStoreConflictError(
                    f"more than one incoming provider profile is active — incoming={len(entries)}"
                )
            self._write([_Entry(profile, secret) for profile, secret in entries], [], loaded.loose)

        self._mutate(run)

    def has_any(self) -> bool:
        """읽은 항목이든 읽지 못한 항목이든 하나라도 있나. 파일을 못 읽으면 예외다."""
        loaded = self._load()
        return bool(loaded.entries or loaded.broken)

    # -- ProviderSecretStore ------------------------------------------------------------------

    def get_secret(self, profile_id: str) -> str:
        for entry in self._readable_entries():
            if entry.profile.profile_id == profile_id and entry.secret is not None:
                return entry.secret
        raise ProviderSecretMissingError(profile_id)

    def put_secret(self, profile_id: str, secret: str) -> None:
        self._find(self._writable_entries(), profile_id)

        def run() -> None:
            loaded = self._load()
            entry = self._find(loaded.entries, profile_id)
            self._write(
                [
                    replace(item, secret=secret) if item is entry else item
                    for item in loaded.entries
                ],
                loaded.broken,
                loaded.loose,
                loaded.broken_active,
            )

        self._mutate(run)

    def delete_secret(self, profile_id: str) -> None:
        """멱등이다. 프로파일이 없거나 키가 없으면 아무것도 하지 않는다(잠금도 잡지 않는다)."""
        if not any(
            item.profile.profile_id == profile_id and item.secret is not None
            for item in self._writable_entries()
        ):
            return

        def run() -> None:
            loaded = self._load()
            if not any(
                item.profile.profile_id == profile_id and item.secret is not None
                for item in loaded.entries
            ):
                return
            self._write(
                [
                    replace(item, secret=None) if item.profile.profile_id == profile_id else item
                    for item in loaded.entries
                ],
                loaded.broken,
                loaded.loose,
                loaded.broken_active,
            )

        self._mutate(run)

    def secrets(self) -> EnvFileProviderSecrets:
        """`ProviderSecretStore` 포트 모양(get·put·delete)으로 본 같은 저장소."""
        return EnvFileProviderSecrets(self)

    # -- 파일 ↔ 값 ----------------------------------------------------------------------------

    def _mutate(self, run: Callable[[], None]) -> None:
        """잠금 안에서 고친다. 쓰기 쪽 저장 실패(읽기·폴더·임시 파일·권한·교체)는 모두 포트 오류로
        바꿔 HTTP가 사용자 문장(422)을 보내게 한다(마지막 리뷰 1).
        """
        try:
            self._file.update(run)
        except SecretStoreStorageError as error:
            raise ProviderStorageUnavailableError(error.reason or "not_writable") from error

    def _writable_entries(self) -> list[_Entry]:
        """고치기 전 확인용.

        파일을 못 읽으면 빈 목록으로 숨기지 않고 저장 불가로 알린다(마지막 리뷰 3).
        """
        try:
            return self._load().entries
        except SecretStoreStorageError as error:
            raise ProviderStorageUnavailableError(error.reason or "unreadable") from error

    def _readable_entries(self) -> list[_Entry]:
        """읽기 전용 조회. 파일 전체를 못 읽으면 공급자가 없는 것처럼 본다(채팅은 "공급자 없음")."""
        try:
            return self._load().entries
        except SecretStoreStorageError:
            return []

    def _load(self) -> _Loaded:
        snapshot = self._file.snapshot()
        cached = self._parsed
        if cached is not None and cached[0] is snapshot:
            return cached[1]
        loaded = self._parse(snapshot.values)
        self._parsed = (snapshot, loaded)
        return loaded

    def _parse(self, all_values: Mapping[str, str]) -> _Loaded:
        values = {
            name: value for name, value in all_values.items() if name.startswith(AI_ENV_PREFIX)
        }
        active = values.get(_ACTIVE)
        grouped: dict[str, dict[str, str]] = {}
        loose: list[tuple[str, str]] = []
        for name, value in values.items():
            matched = _INDEXED.fullmatch(name)
            if matched is None:
                if name != _ACTIVE:
                    loose.append((name, value))
                continue
            grouped.setdefault(matched.group(1), {})[matched.group(2)] = value
        entries: list[_Entry] = []
        broken: list[dict[str, str]] = []
        problems: list[EnvProblem] = []
        seen: set[str] = set()
        for raw in sorted(grouped, key=lambda text: (int(text), text)):
            fields = grouped[raw]
            index = int(raw)
            missing = tuple(field for field in _REQUIRED if not fields.get(field))
            if missing:
                broken.append(fields)
                problems.append(
                    EnvProblem(
                        "ai",
                        "incomplete",
                        index,
                        tuple(f"{AI_ENV_PREFIX}{raw}_{field}" for field in missing),
                    )
                )
                continue
            if fields["ID"] in seen:
                # 같은 id가 두 번이면 둘째부터 읽지 않는다. 어느 쪽을 지울지
                # 고를 수 없다(3차 리뷰 6).
                broken.append(fields)
                problems.append(
                    EnvProblem("ai", "duplicate_id", index, (f"{AI_ENV_PREFIX}{raw}_ID",))
                )
                continue
            try:
                profile = _profile(index, fields, active)
            except SecretStoreStorageError as error:
                broken.append(fields)
                field = "KIND" if error.reason == "unknown_kind" else "CREATED_AT"
                problems.append(
                    EnvProblem(
                        "ai",
                        error.reason or "incomplete",
                        index,
                        (f"{AI_ENV_PREFIX}{raw}_{field}",),
                    )
                )
                continue
            seen.add(profile.profile_id)
            extra = tuple((field, value) for field, value in fields.items() if field not in _FIELDS)
            entries.append(_Entry(profile, fields.get("API_KEY") or None, extra))
        broken_ids = {fields.get("ID") for fields in broken}
        broken_active = (
            active if active is not None and active in broken_ids and active not in seen else None
        )
        return _Loaded(entries, broken, tuple(problems), tuple(loose), broken_active)

    def _write(
        self,
        entries: list[_Entry],
        broken: list[dict[str, str]],
        loose: tuple[tuple[str, str], ...] = (),
        broken_active: str | None = None,
    ) -> None:
        lines: list[tuple[str, str]] = []
        active = next(
            (entry.profile.profile_id for entry in entries if entry.profile.active), broken_active
        )
        if active is not None:
            lines.append((_ACTIVE, active))
        for index, entry in enumerate(entries, start=1):
            profile = entry.profile
            prefix = f"{AI_ENV_PREFIX}{index}_"
            lines.extend(
                [
                    (f"{prefix}ID", profile.profile_id),
                    (f"{prefix}KIND", profile.kind.value),
                    (f"{prefix}LABEL", profile.label),
                    (f"{prefix}MODEL", profile.model),
                ]
            )
            if profile.base_url is not None:
                lines.append((f"{prefix}BASE_URL", profile.base_url))
            lines.append((f"{prefix}CREATED_AT", profile.created_at.isoformat()))
            if entry.secret is not None:
                lines.append((f"{prefix}API_KEY", entry.secret))
            lines.extend((f"{prefix}{field}", value) for field, value in entry.extra)
        # 읽지 못한 항목은 뒤에 번호만 새로 붙여 값 그대로 다시 적는다.
        # 사용자가 고칠 때까지 잃지 않는다.
        for offset, fields in enumerate(broken, start=len(entries) + 1):
            prefix = f"{AI_ENV_PREFIX}{offset}_"
            lines.extend((f"{prefix}{field}", value) for field, value in fields.items())
        lines.extend(loose)
        self._file.replace_group(AI_ENV_PREFIX, _HEADER, lines)

    @staticmethod
    def _find(entries: list[_Entry], profile_id: str) -> _Entry:
        for entry in entries:
            if entry.profile.profile_id == profile_id:
                return entry
        raise ProviderProfileNotFoundError(profile_id)


class EnvFileProviderSecrets:
    """`ProviderSecretStore` 포트. 프로파일 저장소와 메서드 이름(`delete`)이 겹쳐 따로 감싼다."""

    def __init__(self, store: EnvFileProviderStore) -> None:
        self._store = store

    def get(self, profile_id: str) -> str:
        return self._store.get_secret(profile_id)

    def put(self, profile_id: str, secret: str) -> None:
        self._store.put_secret(profile_id, secret)

    def delete(self, profile_id: str) -> None:
        self._store.delete_secret(profile_id)


def _profile(index: int, fields: dict[str, str], active: str | None) -> ProviderProfile:
    try:
        kind = ProviderKind(fields["KIND"])
    except ValueError as error:
        raise SecretStoreStorageError(
            f"a provider profile in the credentials env file has an unknown kind — index={index}",
            reason="unknown_kind",
        ) from error
    try:
        created_at = datetime.fromisoformat(fields["CREATED_AT"])
    except ValueError as error:
        raise SecretStoreStorageError(
            "a provider profile in the credentials env file has an unreadable time — "
            f"index={index} field=CREATED_AT",
            reason="bad_time",
        ) from error
    if created_at.tzinfo is None:
        raise SecretStoreStorageError(
            "a provider profile in the credentials env file has a time without a zone — "
            f"index={index} field=CREATED_AT",
            reason="bad_time",
        )
    return ProviderProfile(
        profile_id=fields["ID"],
        kind=kind,
        label=fields.get("LABEL", ""),
        model=fields["MODEL"],
        base_url=fields.get("BASE_URL") or None,
        created_at=created_at,
        active=fields["ID"] == active,
    )
