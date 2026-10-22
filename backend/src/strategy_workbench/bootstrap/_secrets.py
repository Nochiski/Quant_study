"""사용자 설정 폴더의 비밀 파일(`secrets.json`)을 여는 곳 하나(2026-10-22 리뷰 #594).

HTTP 서버(AI 공급자 키·키움 연결 설정), S07 명령줄, 키움 실측 도구가 같은 경로 규칙(설정값이 없으면
OS 기본 위치)과 같은 금지 루트(저장소 트리)로 연다.
"""

from __future__ import annotations

from pathlib import Path

from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileProviderSecretStore,
    default_secrets_path,
)


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
