"""홈 시장 블록 API: 연습용 원천은 숫자 없이 안내만, 실데이터는 카드 값을 원 단위로 옮긴다."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def test_practice_data_returns_a_message_and_no_numbers() -> None:
    client = TestClient(build_http_app())
    overview = client.get("/api/v1/market/overview").json()
    assert overview["status"] == "practice_data"
    assert overview["message"].startswith("시장 정보는 실제 데이터를 연결해야 보여요")
    assert (overview["indices"], overview["flows"], overview["sectors"]) == ([], [], None)


def test_real_data_overview_and_sector_detail(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity`")
    from tests.test_adapters_equity_duckdb_market import LAST, build_market_root

    root = build_market_root(tmp_path / "root")
    client = TestClient(build_http_app(equity_adapter="duckdb", equity_root=root))
    overview = client.get("/api/v1/market/overview").json()
    assert overview["status"] == "available"
    flows = {card["market"]: card for card in overview["flows"]}
    # 코스피는 측정 시가총액 비중 600/900 이라 합을 숨기고 사유를 싣는다
    assert flows["KOSPI"]["issue"]["code"] == "coverage_low"
    assert (flows["KOSPI"]["totals"], flows["KOSPI"]["days"]) == ([], [])
    day = next(total for total in flows["KOSDAQ"]["totals"] if total["period"] == "day")
    amounts = {item["investor"]: item["amount_krw"] for item in day["amounts"]}
    assert (amounts["individual"], amounts["foreign"]) == (100_000_000, -100_000_000)
    assert day["summary"].startswith("코스닥 ")
    sector_map = overview["sectors"]["sector_map"]
    assert sector_map["session"] == LAST.isoformat()
    child = sector_map["parents"][0]["children"][0]
    assert child["name"] == "반도체와반도체장비"

    detail = client.get(f"/api/v1/market/sectors/{child['code']}").json()
    assert [move["ticker"] for move in detail["securities"]] == ["005930", "000660"]
    missing = client.get("/api/v1/market/sectors/G9999")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "market.sector.not_found"
