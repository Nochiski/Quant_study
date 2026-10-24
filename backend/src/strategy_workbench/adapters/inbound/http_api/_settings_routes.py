"""설정 화면이 보여 줄 저장 위치와 그 파일의 읽기 문제(2026-10-24 사용자 결정, 코드 리뷰 1·2).

AI 공급자와 키움 모의 연결은 내 문서 `auto_trading/.env` 한 파일에 저장된다. 화면이
"어디에 저장되나"를 OS별 실제 경로로 보이게, 서버를 켠 OS가 해석한 절대 경로를 그대로
돌려준다. 이 서버는 사용자의 PC에서
로컬로만 도는 설정 API이고, 경로는 사용자가 직접 찾아가 열어 볼 수 있어야 하는 값이다(오류 문장이
아니다). 파일 내용·키는 싣지 않는다.

파일을 못 읽거나 일부 줄이 빠졌어도 서버는 뜬다. 그 사실을 여기서 사유와 줄 이름으로 알려 사용자가
파일을 고칠 수 있게 한다. 값은 싣지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI
from pydantic import Field

from strategy_workbench.domain.assistant.facade.models import ProviderKind

CREDENTIALS_FILE_PATH = "/api/v1/settings/credentials-file"

ProblemSection = Literal["file", "ai", "kiwoom"]


@dataclass(frozen=True)
class CredentialsFileIssue:
    """bootstrap이 저장 어댑터의 문제를 옮겨 담는 값. 사유 이름과 줄 이름만 있다."""

    section: ProblemSection
    reason: str
    index: int | None = None
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialsFileProblemBody:
    section: ProblemSection
    reason: str
    fields: list[str]
    message: Annotated[
        str, Field(description="사용자에게 그대로 보일 한글 문장. 값은 담지 않는다.")
    ]


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
    problems: list[CredentialsFileProblemBody]


def _message(issue: CredentialsFileIssue) -> str:
    names = ", ".join(issue.fields)
    if issue.section == "file":
        if issue.reason == "not_utf8":
            return "저장 파일을 UTF-8로 읽지 못했습니다. 편집기에서 UTF-8로 다시 저장하세요."
        return "저장 파일을 열지 못했습니다. 파일 권한을 확인하세요."
    if issue.section == "ai":
        where = f"AI 공급자 {issue.index}번 항목" if issue.index is not None else "AI 공급자 항목"
        if issue.reason == "unknown_kind":
            kinds = " 또는 ".join(kind.value for kind in ProviderKind)
            return f"{where}의 종류를 알 수 없습니다. {kinds}로 고치세요: {names}"
        if issue.reason == "bad_time":
            return f"{where}의 등록 시각을 읽지 못했습니다: {names}"
        if issue.reason == "duplicate_id":
            return f"{where}의 ID가 앞 항목과 같아 목록에서 뺐습니다. 한쪽을 지우세요: {names}"
        return f"{where}에 빠진 줄이 있어 목록에서 뺐습니다: {names}"
    return f"키움 모의 연결 세 줄 중 빠진 줄이 있습니다: {names}"


def register_settings_routes(
    app: FastAPI,
    credentials_file_path: Path | None,
    credentials_file_issues: Callable[[], Sequence[CredentialsFileIssue]] | None,
) -> None:
    @app.get(CREDENTIALS_FILE_PATH, operation_id="getCredentialsFile")
    def get_credentials_file() -> CredentialsFileBody:
        issues = credentials_file_issues() if credentials_file_issues is not None else ()
        return CredentialsFileBody(
            path=str(credentials_file_path) if credentials_file_path is not None else None,
            problems=[
                CredentialsFileProblemBody(
                    section=issue.section,
                    reason=issue.reason,
                    fields=list(issue.fields),
                    message=_message(issue),
                )
                for issue in issues
            ],
        )
