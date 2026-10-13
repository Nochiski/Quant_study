"""연구 구간 잠금의 equity 미리보기 경로(검증 랩 spec D1, V1-02).

판정 자체(경계일)는 `domain/backtest/_research_window.py` 하나가 소유하고 실행·미리보기·추적과 같은
함수를 부른다. 여기서는 두 HTTP 경로가 그 판정을 부르는지와 각 경로 기존 422 모양에 코드·문장을
싣는지만 본다. 구성 종목 이력(`/equity/universe/preview`)은 측정이 아니라 잠금을 받지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

_END = "2024-01-04"
_QUERY = {"end": _END, "security_ids": ["sec-005930-1"], "field_ids": ["price.close"]}


def _panel(start: str) -> tuple[str, dict[str, Any]]:
    return "/api/v1/equity/panel/preview", {"query": {**_QUERY, "start": start}}


def _equity(start: str) -> tuple[str, dict[str, Any]]:
    return "/api/v1/equity/preview", {**_QUERY, "start": start}


def _validation_item(detail: Any) -> tuple[str, str, str]:
    """equity 미리보기는 기존 본문 검증 실패 목록(`HTTPValidationError`)에 항목 하나로 싣는다."""
    (item,) = detail
    return item["type"], ".".join(item["loc"]), item["msg"]


_ROUTES: list[tuple[Callable[[str], tuple[str, dict[str, Any]]], Callable[[Any], Any], str]] = [
    (_panel, _validation_item, "body.query.start"),
    (_equity, _validation_item, "body.start"),
]


@pytest.mark.parametrize(("route", "read", "where"), _ROUTES)
@pytest.mark.parametrize("start", ["2019-12-31", "2020-01-01"])
def test_preview_measuring_before_the_research_floor_is_a_coded_422(
    route: Callable[[str], tuple[str, dict[str, Any]]],
    read: Callable[[Any], tuple[str, str, str]],
    where: str,
    start: str,
) -> None:
    """봉인 구간 마지막 날과 연구 하한 전날(휴장일)은 둘 다 거절한다. 문장의 날짜는 backend 가
    채운다."""
    path, body = route(start)

    response = TestClient(build_http_app()).post(path, json=body)

    assert response.status_code == 422, response.text
    code, location, message = read(response.json()["detail"])
    assert (code, location) == ("run_environment.research_window", where)
    assert f"expected=start>=2020-01-02 got=start={start}" in message
    assert "2016-01-01~2019-12-31은 홀드아웃 봉인 구간" in message


@pytest.mark.parametrize("route", [route for route, _read, _where in _ROUTES])
def test_preview_from_the_research_floor_is_allowed(
    route: Callable[[str], tuple[str, dict[str, Any]]],
) -> None:
    path, body = route("2020-01-02")

    response = TestClient(build_http_app()).post(path, json=body)

    assert response.status_code == 200, response.text


def test_universe_history_is_not_a_measurement_and_stays_open() -> None:
    """구성 종목 이력만 보이는 경로는 봉인 구간을 요청해도 잠그지 않는다(spec D1 적용 경로 밖)."""
    response = TestClient(build_http_app()).post(
        "/api/v1/equity/universe/preview",
        json={"venue": "XKRX", "start": "2019-12-31", "end": _END},
    )

    assert response.status_code == 200, response.text
