"""사용자 비밀 파일을 여는 곳 하나(2026-10-22 리뷰 #594, 2026-10-24 사용자 결정).

- 내 문서 `auto_trading/.env`: AI 공급자 프로파일·키와 키움 모의 자격증명의 저장 정본. 서버를 켠
  OS의 "문서" 폴더라 맥·리눅스·윈도우 어디서든 사용자가 같은 자리에서 찾는다.
- 사용자 설정 폴더 `secrets.json`: 예전 저장 위치. 새 파일로 한 번 옮긴 뒤에는 읽지 않는다.

HTTP 서버(설정 화면), S07 명령줄, 키움 실측 도구가 같은 경로 규칙과 같은 금지 루트(저장소 트리)로
연다. 옮기기(이전)도 여기 하나에서 한다 — 어느 입구로 먼저 켜도 같은 결과가 나와야 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from strategy_workbench.adapters.outbound.assistant_sqlite.facade.repository import (
    SQLiteProviderProfileRepository,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.env_store import (
    CredentialsEnvFile,
    EnvFileBrokerCredentialStore,
    EnvFileProviderStore,
    default_auto_trading_env_path,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
    SecretStoreStorageError,
    default_secrets_path,
)
from strategy_workbench.application.assistant_chat.facade.ports import ProviderSecretMissingError
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentialStore,
    BrokerCredentialsUnreadableError,
)
from strategy_workbench.domain.assistant.facade.models import ProviderProfile

logger = logging.getLogger(__name__)


def repository_root() -> Path:
    """**소스 체크아웃 기준** 저장소 루트. 비밀 파일이 들어가면 안 되는 트리다.

    `backend/src/strategy_workbench/bootstrap/`에서 네 단계 위다. 같은 파일의 `.local` 경로
    계산(`_http.py`의 `parents[3]`)과 같은 관례이고, 이 앱은 editable 설치(`uv sync`)로만
    돌기 때문에 성립한다. 비 editable 설치에서는 관련 없는 디렉터리를 가리키므로, 그런 배포가
    생기면 저장소 루트를 설정으로 받아야 한다.
    """
    return Path(__file__).resolve().parents[4]


def local_secret_store(configured: str | Path | None) -> LocalFileProviderSecretStore:
    """`configured`(실행 설정 `STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH`)가 없으면 OS 기본 위치를
    쓴다. 저장소 트리 안은 거부한다."""
    return LocalFileProviderSecretStore(
        configured if configured is not None else default_secrets_path(),
        forbidden_roots=(repository_root(),),
    )


def default_credentials_env_path() -> Path:
    """OS가 정한 "문서" 폴더의 `auto_trading/.env`. 계산은 어댑터가 소유한다."""
    return default_auto_trading_env_path()


def credentials_env_file(path: str | Path) -> CredentialsEnvFile:
    """`auto_trading/.env` 파일 객체. 저장소 트리 안은 거부한다."""
    return CredentialsEnvFile(path, forbidden_roots=(repository_root(),))


def migrate_provider_profiles(
    legacy_profiles: SQLiteProviderProfileRepository,
    legacy_secrets: LocalFileProviderSecretStore,
    target: EnvFileProviderStore,
) -> int:
    """예전 프로파일(SQLite)과 키(`secrets.json`)를 새 파일로 한 번 옮기고 옮긴 개수를 돌려준다.

    새 파일에 이미 공급자가 있으면 옮기지 않는다(사용자가 새 위치를 이미 쓰고 있다).
    새 파일에 먼저 쓰고 나서 예전 것을 지운다 — 중간에 실패해도 어느 한쪽에는 남는다.
    예전 키 파일을 읽지 못하면 아무것도
    지우지 않고 건너뛴다.
    """
    if target.list():
        return 0
    profiles = legacy_profiles.list()
    if not profiles:
        return 0
    moved: list[tuple[ProviderProfile, str | None]] = []
    try:
        for profile in profiles:
            try:
                secret: str | None = legacy_secrets.get(profile.profile_id)
            except ProviderSecretMissingError:
                secret = None
            moved.append((profile, secret))
    except SecretStoreStorageError as error:
        logger.warning(
            "legacy provider secrets are unreadable; skipped moving them — error=%s",
            type(error).__name__,
        )
        return 0
    target.import_profiles(moved)
    for profile in profiles:
        legacy_secrets.delete(profile.profile_id)
        legacy_profiles.delete(profile.profile_id)
    logger.info("moved provider profiles to the credentials env file — count=%d", len(profiles))
    return len(profiles)


def migrate_kiwoom_credentials(
    legacy: LocalFileBrokerCredentialStore, target: EnvFileBrokerCredentialStore
) -> bool:
    """예전 `secrets.json`의 키움 모의 자격증명을 새 파일로 한 번 옮긴다. 옮겼으면 True.

    새 파일에 이미 있으면 옮기지 않는다. 새 파일에 먼저 쓰고 예전 항목을 지운다.
    예전 항목이 깨졌으면 아무것도 지우지 않고 건너뛴다(설정 화면에서 다시 저장하면 새 파일에
    들어간다).
    """
    if target.load() is not None:
        return False
    try:
        saved = legacy.load()
    except BrokerCredentialsUnreadableError as error:
        logger.warning(
            "legacy kiwoom mock credentials are unreadable; skipped moving them — error=%s",
            type(error).__name__,
        )
        return False
    if saved is None:
        return False
    target.save(saved)
    legacy.delete()
    logger.info("moved kiwoom mock credentials to the credentials env file")
    return True


def kiwoom_credential_store(
    *,
    secrets: LocalFileProviderSecretStore,
    credentials_file: CredentialsEnvFile | None,
) -> BrokerCredentialStore:
    """키움 모의 자격증명 저장소.

    `.env`가 있으면 그쪽(예전 것을 옮긴 뒤), 없으면 예전 `secrets.json`이다.

    설정 화면(HTTP), S07 명령줄, 실측 도구가 모두 이 함수로 연다. 어느 입구로 먼저 켜도 같은
    이전이 일어난다.
    """
    legacy = LocalFileBrokerCredentialStore(secrets)
    if credentials_file is None:
        return legacy
    target = EnvFileBrokerCredentialStore(credentials_file)
    migrate_kiwoom_credentials(legacy, target)
    return target
