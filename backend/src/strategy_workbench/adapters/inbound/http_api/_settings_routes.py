"""설정 화면이 보여 줄 저장 위치(2026-10-24 사용자 결정).

AI 공급자와 키움 모의 연결은 내 문서 `auto_trading/.env` 한 파일에 저장된다. 화면이
"어디에 저장되나"를 OS별 실제 경로로 보이게, 서버를 켠 OS가 해석한 절대 경로를 그대로
돌려준다. 이 서버는 사용자의 PC에서
로컬로만 도는 설정 API이고, 경로는 사용자가 직접 찾아가 열어 볼 수 있어야 하는 값이다(오류 문장이
아니다). 파일 내용·키는 싣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI
from pydantic import Field

CREDENTIALS_FILE_PATH = "/api/v1/settings/credentials-file"


@dataclass(frozen=True)
class CredentialsFileBody:
    path: Annotated[
        str | None,
        Field(
            description=(
                "AI 공급자·키움 모의 연결을 저장하는 `.env`의 절대 경로. "
                "예전 저장(사용자 설정 폴더)을 쓰는 서버는 null이다."
            )
        ),
    ]


def register_settings_routes(app: FastAPI, credentials_file_path: Path | None) -> None:
    @app.get(CREDENTIALS_FILE_PATH, operation_id="getCredentialsFile")
    def get_credentials_file() -> CredentialsFileBody:
        return CredentialsFileBody(
            path=str(credentials_file_path) if credentials_file_path is not None else None
        )
