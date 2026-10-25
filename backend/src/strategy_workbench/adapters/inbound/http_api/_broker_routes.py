"""키움 모의 연결 설정 API(S06 ②-2).

앱 키·시크릿·계좌번호는 요청으로만 들어오고 어떤 응답에도 실리지 않는다. OpenAPI에서도
`writeOnly`로 표시해 생성 SDK가 응답 타입에 그 칸을 만들지 않게 한다. 응답에는 저장 여부와 계좌번호
끝 4자리만 싣는다. 본문 검증 실패도 입력값을 되돌려 보내지 않도록 코드화된 422로 바꾼다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import Field

from strategy_workbench.application.broker_connection.facade.connection import (
    BrokerConnectionResult,
    BrokerConnectionService,
    BrokerConnectionStatus,
    BrokerCredentialsUnwritableError,
    ConnectionOutcome,
    InvalidBrokerCredentialsError,
)

from ._backtest_contract import CodedBodyValidationRoute

KIWOOM_MOCK_CONNECTION_PATH = "/api/v1/broker-connections/kiwoom-mock"

BrokerErrorCode = Literal[
    "broker.request_invalid", "broker.credentials_invalid", "broker.storage_unavailable"
]
# 저장 파일 자체를 못 읽거나 못 쓸 때 사용자에게 보일 문장. 다시
# 저장해도 고쳐지지 않는다(3차 리뷰 3).
STORAGE_UNAVAILABLE_MESSAGE = (
    "키를 저장하는 파일을 읽거나 쓸 수 없습니다. 설정 화면의 저장 위치에 보이는 파일을 고치거나 "
    "지운 뒤 다시 시도하세요."
)


@dataclass(frozen=True)
class SaveKiwoomMockConnectionBody:
    app_key: Annotated[
        str,
        Field(
            json_schema_extra={"writeOnly": True},
            description="키움 모의투자 App Key. 요청 전용이며 어떤 응답에도 실리지 않는다.",
        ),
    ]
    secret_key: Annotated[
        str,
        Field(
            json_schema_extra={"writeOnly": True},
            description="키움 모의투자 App Secret. 요청 전용이며 어떤 응답에도 실리지 않는다.",
        ),
    ]
    account_number: Annotated[
        str,
        Field(
            json_schema_extra={"writeOnly": True},
            description="모의 계좌번호 10자리('-' 허용). 요청 전용이며 응답에는 끝 4자리만 실린다.",
        ),
    ]


@dataclass(frozen=True)
class KiwoomMockConnectionBody:
    saved: bool
    account_tail: str | None
    unreadable: Annotated[
        bool,
        Field(description="저장된 항목이 깨져 읽지 못했다. 다시 저장하거나 지우면 고쳐진다."),
    ] = False


@dataclass(frozen=True)
class KiwoomMockConnectionCheckBody:
    outcome: ConnectionOutcome
    message: str
    account_tail: str | None


@dataclass(frozen=True)
class BrokerErrorDetail:
    code: BrokerErrorCode
    message: str
    field: str | None = None


@dataclass(frozen=True)
class BrokerErrorResponse:
    detail: BrokerErrorDetail


class _BrokerBodyRoute(CodedBodyValidationRoute):
    @staticmethod
    def coded_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        # reason: FastAPI 검증 오류 hook의 공개 JSON 계약을 그대로 받는다. 입력값(`input`)은
        # 키일 수 있으므로 칸 이름만 옮긴다.
        location = next(iter(issues), {}).get("loc", ())
        field = "/".join(str(part) for part in location if part != "body")
        return {
            "code": "broker.request_invalid",
            "message": "요청 본문을 읽지 못했습니다. 칸을 확인해 주세요.",
            "field": field or None,
        }


def _status(status: BrokerConnectionStatus) -> KiwoomMockConnectionBody:
    return KiwoomMockConnectionBody(
        saved=status.saved, account_tail=status.account_tail, unreadable=status.unreadable
    )


def _check(result: BrokerConnectionResult) -> KiwoomMockConnectionCheckBody:
    return KiwoomMockConnectionCheckBody(
        outcome=result.outcome, message=result.message, account_tail=result.account_tail
    )


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        422,
        detail={"code": "broker.storage_unavailable", "message": STORAGE_UNAVAILABLE_MESSAGE},
    )


def register_broker_connection_routes(app: FastAPI, service: BrokerConnectionService) -> None:
    responses: dict[int | str, dict[str, object]] = {422: {"model": BrokerErrorResponse}}

    @app.get(KIWOOM_MOCK_CONNECTION_PATH, operation_id="getKiwoomMockConnection")
    def get_connection() -> KiwoomMockConnectionBody:
        return _status(service.status())

    def save_connection(body: SaveKiwoomMockConnectionBody) -> KiwoomMockConnectionBody:
        try:
            return _status(service.save(body.app_key, body.secret_key, body.account_number))
        except InvalidBrokerCredentialsError as error:
            raise HTTPException(
                422,
                detail={
                    "code": "broker.credentials_invalid",
                    "message": error.message,
                    "field": error.field,
                },
            ) from error
        except BrokerCredentialsUnwritableError as error:
            raise _storage_unavailable() from error

    app.router.add_api_route(
        KIWOOM_MOCK_CONNECTION_PATH,
        save_connection,
        methods=["PUT"],
        operation_id="saveKiwoomMockConnection",
        responses=responses,
        route_class_override=_BrokerBodyRoute,
    )

    @app.delete(
        KIWOOM_MOCK_CONNECTION_PATH,
        operation_id="deleteKiwoomMockConnection",
        status_code=204,
        responses=responses,
    )
    def delete_connection() -> None:
        try:
            service.delete()
        except BrokerCredentialsUnwritableError as error:
            raise _storage_unavailable() from error

    @app.post(f"{KIWOOM_MOCK_CONNECTION_PATH}/verify", operation_id="verifyKiwoomMockConnection")
    def verify_connection() -> KiwoomMockConnectionCheckBody:
        """토큰 발급과 계좌 대조만 한다. 사용자가 누를 때만 키를 쓴다. 주문하지 않는다."""
        return _check(service.verify())
