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
    AI_IMPORT_NAME,
    KIWOOM_IMPORT_NAME,
    CredentialsEnvFile,
    EnvFileBrokerCredentialStore,
    EnvFileProviderStore,
    EnvProblem,
    default_auto_trading_env_path,
)
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
    default_secrets_path,
)
from strategy_workbench.application.assistant_chat.facade.ports import ProviderSecretMissingError
from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerCredentialStore,
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
    """예전 프로파일(SQLite)과 키(`secrets.json`)를 새 파일로 **한 번** 옮긴다.

    옮긴 개수를 돌려준다.


    - 새 파일에 이전 기록(`STRATEGY_WORKBENCH_IMPORTED_AI_PROVIDERS`)이 있으면 다시 가져오지 않는다
      (코드 리뷰 3). 그러지 않으면 화면에서 다 지운 뒤 다른 체크아웃이 켜질 때
      그 체크아웃의 `.local` DB에 남은 옛 프로파일이 되살아난다.
    - 새 파일에 이미 공급자가 있으면 옮기지 않고 기록만 남긴다(사용자가 새 위치를 이미 쓴다).
    - 예전 저장에 아무것도 없으면 기록하지 않는다. 다른 체크아웃의 DB에 남은 것을 그 체크아웃이
      처음 켜질 때 가져갈 수 있게 둔다.
    - 새 파일에 먼저 쓰고 기록한 뒤 예전 것을 지운다. 중간에 실패해도 어느 한쪽에는 남는다.
    - 이전이 끝난 뒤 예전 자리에 남은 것(다른 체크아웃분·지우기 실패분)은 **지우지 않는다**. 읽지도
      않으니 되살아나지 않고, 지우면 되돌릴 수 없다. 개수만 경고 로그에 남긴다(SoT 리뷰 M2·M3).
    - 새 파일이나 예전 키 파일을 읽지 못하면 아무것도 바꾸지 않고 건너뛴다. 서버는 뜬다(리뷰 1).
    """
    file = target.file

    def run() -> tuple[ProviderProfile, ...]:
        """잠금 안에서 다시 판단하고 옮긴다. 옮긴 프로파일, 옮기지 않았으면 빈 튜플."""
        if file.imported(AI_IMPORT_NAME) or file.file_problem() is not None:
            return ()
        if target.has_any():
            file.mark_imported(AI_IMPORT_NAME)
            return ()
        found = legacy_profiles.list()
        if not found:
            return ()
        moved: list[tuple[ProviderProfile, str | None]] = []
        for profile in found:
            try:
                secret: str | None = legacy_secrets.get(profile.profile_id)
            except ProviderSecretMissingError:
                secret = None
            moved.append((profile, secret))
        target.import_profiles(moved)
        file.mark_imported(AI_IMPORT_NAME)
        return found

    try:
        # 할 일이 없으면 잠금(폴더 생성)까지 가지 않는다. 매 기동마다 문서 폴더를 만들지 않게 한다.
        if file.imported(AI_IMPORT_NAME):
            _warn_leftover_providers(legacy_profiles, legacy_secrets)
            return 0
        if file.file_problem() is not None:
            logger.warning("credentials env file is unreadable; skipped moving providers")
            return 0
        if not target.has_any() and not legacy_profiles.list():
            return 0
        profiles = file.update(run)
    except Exception as error:  # noqa: BLE001 — reason: 이전 실패는 무엇이든 기동을 막지 않는다(3차 리뷰 1)
        logger.warning(
            "could not move legacy providers; left everything in place — error=%s",
            type(error).__name__,
        )
        return 0
    if not profiles:
        return 0
    try:
        for profile in profiles:
            legacy_secrets.delete(profile.profile_id)
            legacy_profiles.delete(profile.profile_id)
    except Exception as error:  # noqa: BLE001 — reason: 옮긴 뒤 정리 실패는 기동을 막지 않는다
        logger.warning(
            "moved providers but could not clear the legacy copies — error=%s",
            type(error).__name__,
        )
    logger.info("moved provider profiles to the credentials env file — count=%d", len(profiles))
    return len(profiles)


def _warn_leftover_providers(
    legacy_profiles: SQLiteProviderProfileRepository,
    legacy_secrets: LocalFileProviderSecretStore,
) -> None:
    """이전이 끝난 뒤 예전 자리에 남은 공급자 수만 알린다. 이름·값은 싣지 않는다."""
    try:
        profiles = legacy_profiles.list()
        keys = 0
        for profile in profiles:
            try:
                legacy_secrets.get(profile.profile_id)
            except ProviderSecretMissingError:
                continue
            keys += 1
    except Exception as error:  # noqa: BLE001 — reason: 경고용 집계 실패는 기동을 막지 않는다
        logger.warning("could not count legacy providers — error=%s", type(error).__name__)
        return
    if profiles:
        logger.warning(
            "legacy providers remain after the move and are no longer read — profiles=%d keys=%d",
            len(profiles),
            keys,
        )


def migrate_kiwoom_credentials(
    legacy: LocalFileBrokerCredentialStore, target: EnvFileBrokerCredentialStore
) -> bool:
    """예전 `secrets.json`의 키움 모의 자격증명을 새 파일로 **한 번** 옮긴다. 옮겼으면 True.

    공급자와 같은 규칙이다: 이전 기록(`STRATEGY_WORKBENCH_IMPORTED_KIWOOM_MOCK`)이 있으면 다시 읽지
    않고(옛 버전 서버가 예전 파일에 다시 써도 되살아나지 않는다), 새 파일에 이미 있으면
    기록만 남기며,
    예전 항목이 깨졌거나 새 파일을 읽지 못하면 아무것도 바꾸지 않는다. 남은 예전 항목은 지우지 않고
    개수만 경고한다.
    """
    file = target.file

    def run() -> bool:
        """잠금 안에서 다시 판단하고 옮긴다. 옮겼으면 True."""
        if file.imported(KIWOOM_IMPORT_NAME) or file.file_problem() is not None:
            return False
        if target.problems():
            return False
        if target.load() is not None:
            file.mark_imported(KIWOOM_IMPORT_NAME)
            return False
        saved = legacy.load()
        if saved is None:
            return False
        target.save(saved)
        file.mark_imported(KIWOOM_IMPORT_NAME)
        return True

    try:
        # 할 일이 없으면 잠금(폴더 생성)까지 가지 않는다.
        if file.imported(KIWOOM_IMPORT_NAME):
            _warn_leftover_kiwoom(legacy)
            return False
        if file.file_problem() is not None or target.problems():
            logger.warning("credentials env file is unreadable; skipped moving kiwoom keys")
            return False
        if target.load() is None and legacy.load() is None:
            return False
        if not file.update(run):
            return False
    except Exception as error:  # noqa: BLE001 — reason: 이전 실패는 무엇이든 기동을 막지 않는다(3차 리뷰 1)
        logger.warning(
            "could not move legacy kiwoom keys; left everything in place — error=%s",
            type(error).__name__,
        )
        return False
    try:
        legacy.delete()
    except Exception as error:  # noqa: BLE001 — reason: 옮긴 뒤 정리 실패는 기동을 막지 않는다
        logger.warning(
            "moved kiwoom keys but could not clear the legacy copy — error=%s",
            type(error).__name__,
        )
    logger.info("moved kiwoom mock credentials to the credentials env file")
    return True


def _warn_leftover_kiwoom(legacy: LocalFileBrokerCredentialStore) -> None:
    try:
        left = legacy.load() is not None
    except Exception:  # noqa: BLE001 — reason: 깨진 예전 항목도 "남아 있음"이다
        left = True
    if left:
        logger.warning(
            "legacy kiwoom mock keys remain after the move and are no longer read — count=1"
        )


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


def credentials_file_problems(file: CredentialsEnvFile) -> tuple[EnvProblem, ...]:
    """설정 화면이 보일 저장 파일 문제. 파일 전체를 못 읽으면 그 하나만, 아니면 묶음별 문제."""
    whole = file.file_problem()
    if whole is not None:
        return (whole,)
    return (
        *EnvFileProviderStore(file).problems(),
        *EnvFileBrokerCredentialStore(file).problems(),
    )
