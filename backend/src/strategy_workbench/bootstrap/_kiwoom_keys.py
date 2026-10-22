"""설정 화면이 저장한 키움 모의 키를 읽는 곳 하나(S06 실측 도구·S07 명령줄).

2026-10-22 리뷰 #594: 비밀 파일 경로·금지 루트·키 변환이 세 곳에 따로 있었다.
"""

from __future__ import annotations

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
)
from strategy_workbench.bootstrap._http import runtime_assistant_settings
from strategy_workbench.bootstrap._secrets import local_secret_store


def saved_kiwoom_credentials() -> KiwoomCredentials | None:
    """저장한 적이 없으면 None이다."""
    store = local_secret_store(runtime_assistant_settings().secrets_path)
    saved = LocalFileBrokerCredentialStore(store).load()
    return None if saved is None else KiwoomCredentials.from_saved(saved)
