"""키움 모의 자격증명을 `auto_trading/.env`의 세 이름으로 둔다(2026-10-24 사용자 결정).

    # Quant Workbench 키움 모의투자 — 앱 설정 화면이 고칩니다
    STRATEGY_WORKBENCH_KIWOOM_MOCK_APP_KEY=…
    STRATEGY_WORKBENCH_KIWOOM_MOCK_SECRET_KEY=…
    STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT=…

세 값은 한 번의 쓰기로 함께 바뀐다 — 따로 쓰면 중간 실패 때 키와 계좌가 엇갈린다.
셋 중 일부만 있으면
"깨진 항목"으로 본다. 값은 어떤 예외 문장에도 싣지 않는다.
"""

from __future__ import annotations

from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentials,
    BrokerCredentialsUnreadableError,
)

from ._env_file import CredentialsEnvFile, EnvProblem
from ._errors import SecretStoreStorageError

__all__ = [
    "KIWOOM_IMPORT_NAME",
    "KIWOOM_MOCK_ENV_NAMES",
    "KIWOOM_MOCK_ENV_PREFIX",
    "EnvFileBrokerCredentialStore",
]

KIWOOM_IMPORT_NAME = "KIWOOM_MOCK"

KIWOOM_MOCK_ENV_PREFIX = "STRATEGY_WORKBENCH_KIWOOM_MOCK_"
_HEADER = "Quant Workbench 키움 모의투자 — 앱 설정 화면이 고칩니다"
_APP_KEY = f"{KIWOOM_MOCK_ENV_PREFIX}APP_KEY"
_SECRET_KEY = f"{KIWOOM_MOCK_ENV_PREFIX}SECRET_KEY"
_ACCOUNT = f"{KIWOOM_MOCK_ENV_PREFIX}ACCOUNT"
KIWOOM_MOCK_ENV_NAMES = (_APP_KEY, _SECRET_KEY, _ACCOUNT)


class EnvFileBrokerCredentialStore:
    def __init__(self, file: CredentialsEnvFile) -> None:
        self._file = file

    @property
    def file(self) -> CredentialsEnvFile:
        return self._file

    def problems(self) -> tuple[EnvProblem, ...]:
        """세 줄 중 일부만 있으면 빠진 이름. 파일 전체 문제는 `file_problem`이 따로 말한다."""
        try:
            values = self._file.group(KIWOOM_MOCK_ENV_PREFIX)
        except SecretStoreStorageError:
            return ()
        present = [name for name in KIWOOM_MOCK_ENV_NAMES if values.get(name)]
        if not present or len(present) == len(KIWOOM_MOCK_ENV_NAMES):
            return ()
        missing = tuple(name for name in KIWOOM_MOCK_ENV_NAMES if name not in present)
        return (EnvProblem("kiwoom", "incomplete", None, missing),)

    def load(self) -> BrokerCredentials | None:
        try:
            values = self._file.group(KIWOOM_MOCK_ENV_PREFIX)
        except SecretStoreStorageError as error:
            raise BrokerCredentialsUnreadableError(
                f"credentials env file is unreadable — error={type(error).__name__}"
            ) from error
        present = [name for name in KIWOOM_MOCK_ENV_NAMES if values.get(name)]
        if not present:
            return None
        if len(present) != len(KIWOOM_MOCK_ENV_NAMES):
            missing = [name for name in KIWOOM_MOCK_ENV_NAMES if name not in present]
            # 이름만 싣는다. 값은 싣지 않는다.
            raise BrokerCredentialsUnreadableError(
                f"stored broker credentials are incomplete — missing={missing}"
            )
        return BrokerCredentials(values[_APP_KEY], values[_SECRET_KEY], values[_ACCOUNT])

    def save(self, credentials: BrokerCredentials) -> None:
        self._file.replace_group(
            KIWOOM_MOCK_ENV_PREFIX,
            _HEADER,
            (
                (_APP_KEY, credentials.app_key),
                (_SECRET_KEY, credentials.secret_key),
                (_ACCOUNT, credentials.account_number),
            ),
        )

    def delete(self) -> None:
        self._file.replace_group(KIWOOM_MOCK_ENV_PREFIX, _HEADER, ())
