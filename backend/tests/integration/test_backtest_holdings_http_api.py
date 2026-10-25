"""실제 엔진이 낸 결과로 보유·매매 조회를 끝까지 잇는다.

손으로 만든 원장은 `tests/domain/test_holdings.py` 가 본다. 여기서는 엔진 원장(세션 끝 보유·체결)과
종목 디렉터리 배선이 투영과 맞물리는지를 성질로 본다: 현금 + 보유 평가액 = 자산, 개요의 종목 체결
수 = 종목 조회의 체결 수, 이름이 디렉터리에서 붙는다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app
from tests.backtest_run_wait import wait_for_terminal_state


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(build_http_app(research_db_path=tmp_path / "research.sqlite3"))


def _completed_run(client: TestClient) -> str:
    spec = client.get("/api/v1/strategies/template").json()
    body: dict[str, Any] = {
        "strategy_source": {"kind": "inline_draft", "spec": spec, "source_hash": "d" * 64},
        "core": "python",
        "environment": {
            "start": "2026-01-02",
            "end": "2026-03-31",
            "universe_id": "krx.common-stock",
        },
    }
    run_id = client.post("/api/v1/backtests", json=body).json()["run"]["run_id"]
    completed = wait_for_terminal_state(client, run_id)
    assert completed["status"] == "completed", completed
    return str(run_id)


def test_holdings_views_agree_with_the_engine_ledger_and_carry_names(client: TestClient) -> None:
    run_id = _completed_run(client)

    overview = client.get(f"/api/v1/backtests/{run_id}/holdings")
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["trade_sessions"], "템플릿 전략이 한 번도 매매하지 않으면 이 검사가 약하다"
    assert data["fill_count"] == sum(item["fill_count"] for item in data["trade_sessions"])
    assert all(item["security"]["name"] for item in data["securities"])

    for trade_session in data["trade_sessions"]:
        report = client.get(
            f"/api/v1/backtests/{run_id}/holdings/sessions/{trade_session['session']}"
        ).json()
        assert report["status"] == "available", report
        holdings = report["holdings"]
        held_value = sum(row["market_value"] for row in holdings["holdings"])
        assert holdings["cash"] + held_value == pytest.approx(holdings["equity"])
        assert len(holdings["holdings"]) == trade_session["holding_count"]
        assert len(holdings["closed"]) == trade_session["closed_count"]
        totals = holdings["totals"]
        assert totals["buy_count"] + totals["sell_count"] == trade_session["fill_count"]

    first = data["securities"][0]
    trades = client.get(
        f"/api/v1/backtests/{run_id}/holdings/securities/{first['security']['security_id']}"
    ).json()
    assert trades["status"] == "available", trades
    assert sum(len(item["fills"]) for item in trades["sessions"]) == first["fill_count"]
    assert trades["security"] == first["security"]


def test_holdings_answer_unrecorded_days_and_unknown_runs_with_codes(client: TestClient) -> None:
    run_id = _completed_run(client)
    weekend = client.get(f"/api/v1/backtests/{run_id}/holdings/sessions/{date(2026, 1, 3)}")
    assert weekend.status_code == 200, weekend.text
    assert weekend.json()["status"] == "session_not_recorded"
    assert weekend.json()["holdings"] is None

    missing = client.get("/api/v1/backtests/no-such-run/holdings")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "backtest.run.not_found"
