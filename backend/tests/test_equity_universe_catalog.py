"""실행 설정 유니버스 선택지(`GET /api/v1/equity/universes`).

선택지의 owner 는 연결된 시장 데이터 어댑터다. 화면은 이 목록을 드롭다운으로 보이기만
하고 id·표시명을 다시 만들지 않는다. 문장 없는 id 도 목록에서 빠지지 않고 id 를 이름으로 쓴다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.equity_mock._fixture import build_demo_fixture
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.equity.facade.research_data import describe_universe


def test_mock_adapter_lists_the_universe_its_fixture_serves() -> None:
    universes = MockEquityDataAdapter(build_demo_fixture()).list_universes()

    assert [(item.market, item.universe_id) for item in universes] == [
        ("KRX", "krx.common-stock")
    ]
    assert universes[0].label == "KRX 보통주"


def test_universe_without_registered_text_keeps_its_id_as_the_label() -> None:
    item = describe_universe("krx.custom", "KRX")

    assert (item.universe_id, item.label) == ("krx.custom", "krx.custom")
    assert item.description == "설명이 등록되지 않은 유니버스입니다."


def test_http_lists_universes_from_the_connected_adapter() -> None:
    response = TestClient(build_http_app()).get("/api/v1/equity/universes")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "universe_id": "krx.common-stock",
            "market": "KRX",
            "label": "KRX 보통주",
            "description": (
                "KRX 상장 보통주입니다. 기준일마다 그날 상장돼 있던 종목으로 후보를 다시 고릅니다."
            ),
        }
    ]
