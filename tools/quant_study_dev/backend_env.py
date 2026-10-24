"""루트 개발 명령이 backend uv 프로젝트를 부를 때 쓰는 명령·환경 규칙을 한 곳에 둔다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def backend_uv_command(uv: str, backend: Path, *arguments: str) -> tuple[str, ...]:
    """설치된 backend 환경을 그대로 쓴다. 의존성을 설치하거나 동기화하지 않는다."""
    return (uv, "run", "--no-sync", "--project", str(backend), *arguments)


def backend_process_environment(environ: Mapping[str, str]) -> dict[str, str]:
    environment = dict(environ)
    # 루트 uv 환경을 backend 프로젝트 환경으로 오인하지 않게 한다. DB·adapter 설정은 보존한다.
    environment.pop("VIRTUAL_ENV", None)
    return environment


def backend_venv(backend: Path, environ: Mapping[str, str]) -> Path:
    """uv와 같은 규칙으로 backend 가상환경 위치를 정한다(UV_PROJECT_ENVIRONMENT 우선)."""
    configured = environ.get("UV_PROJECT_ENVIRONMENT", "").strip()
    if not configured:
        return backend / ".venv"
    path = Path(os.path.expanduser(configured))
    # 상대 경로는 uv처럼 프로젝트 디렉터리 기준으로 해석한다.
    return path if path.is_absolute() else backend / path
