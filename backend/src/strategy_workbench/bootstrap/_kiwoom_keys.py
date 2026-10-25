"""설정 화면이 저장한 키움 모의 키를 읽는 곳 하나(S06 실측 도구·S07 명령줄).

2026-10-22 리뷰 #594: 비밀 파일 경로·금지 루트·키 변환이 세 곳에 따로 있었다.
2026-10-24 사용자 결정: 저장 정본은 내 문서 `auto_trading/.env`다. 예전 `secrets.json`에 남은 키는
여기서 열 때도 서버와 같은 규칙으로 한 번 옮긴다.
"""

from __future__ import annotations

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.bootstrap._http import runtime_assistant_settings
from strategy_workbench.bootstrap._secrets import (
    credentials_env_file,
    kiwoom_credential_store,
    local_secret_store,
)


def saved_kiwoom_credentials() -> KiwoomCredentials | None:
    """저장한 적이 없으면 None이다."""
    settings = runtime_assistant_settings()
    store = kiwoom_credential_store(
        secrets=local_secret_store(settings.secrets_path),
        credentials_file=(
            credentials_env_file(settings.credentials_env_path)
            if settings.credentials_env_path is not None
            else None
        ),
    )
    saved = store.load()
    return None if saved is None else KiwoomCredentials.from_saved(saved)
