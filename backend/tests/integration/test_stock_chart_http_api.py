"""개별종목차트 API: 연습용 원천도 합성 일봉으로 흐름이 돌고, 실데이터는 화면 가격 =
원주가로 옮긴다.

오류는 404·422 `{detail: {code, message}}`이고 응답 어디에도 NaN·inf가 없다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

BASE = "/api/v1/stock-chart"
SAMSUNG = {"kind": "stock", "code": "005930"}


class _Body(Protocol):
    """TestClient 응답(설치된 전송 계층에 따라 httpx·httpx2)에서 읽는 칸."""

    @property
    def text(self) -> str: ...


def _strict(response: _Body) -> Any:  # noqa: ANN401  # reason: JSON 본문 그대로
    """NaN·Infinity 토큰이 있으면 실패하는 JSON 읽기."""

    def refuse(token: str) -> None:
        raise AssertionError(f"non-finite JSON number {token}")

    return json.loads(response.text, parse_constant=refuse)


def test_practice_source_runs_the_whole_chart_flow(tmp_path: Path) -> None:
    client = TestClient(build_http_app(research_db_path=tmp_path / "research.sqlite3"))

    found = _strict(client.get(f"{BASE}/instruments", params={"query": "삼성"}))
    assert found["practice_data"] and found["notice"].startswith("연습용 데이터예요")
    assert [item["code"] for item in found["instruments"]] == ["005930"]

    week = _strict(client.get(f"{BASE}/candles", params={**SAMSUNG, "timeframe": "week"}))
    assert week["context"]["practice_data"]
    assert week["candles"][-1]["in_progress"] and week["last_completed"] < week["context"]["as_of"]

    for path, extra in (
        ("regime", {}),
        ("channels", {"direction": "up"}),
        ("retracement", {"swing": "range_extremes"}),
        ("zones", {}),
    ):
        response = client.get(f"{BASE}/{path}", params={**SAMSUNG, "timeframe": "day", **extra})
        assert response.status_code == 200, (path, response.text)
        _strict(response)
    channels = _strict(
        client.get(f"{BASE}/channels", params={**SAMSUNG, "timeframe": "day", "direction": "up"})
    )
    channel = channels["candidates"][0]

    # 자동 후보를 그 기록과 함께 그대로 저장하고, 수평선을 하나 더 긋는다
    created = client.post(
        f"{BASE}/lines",
        json={
            "instrument_kind": "stock",
            "instrument_code": "005930",
            "timeframe": "day",
            "kind": "fib_channel",
            "source": "auto",
            "anchors": channel["drawing"]["anchors"],
            "auto": channels["record"],
        },
    )
    assert created.status_code == 201, created.text
    saved = _strict(created)
    # 화면 가격 ÷ k로 저장했다가 × k로 다시 읽어도 후보와 같은 선이다
    assert [(p["level"], p["start"]["bar_date"]) for p in saved["paths"]] == [
        (p["level"], p["start"]["bar_date"]) for p in channel["drawing"]["paths"]
    ]
    assert [p["end"]["price"] for p in saved["paths"]] == pytest.approx(
        [p["end"]["price"] for p in channel["drawing"]["paths"]]
    )
    assert saved["auto"]["chosen_by"] == channels["record"]["chosen_by"]
    hline = _strict(
        client.post(
            f"{BASE}/lines",
            json={
                "instrument_kind": "stock",
                "instrument_code": "005930",
                "timeframe": "day",
                "kind": "hline",
                "source": "manual",
                "anchors": [channel["drawing"]["anchors"][0]],
            },
        )
    )
    edited = client.put(
        f"{BASE}/lines/{hline['line_id']}",
        json={
            "anchors": hline["anchors"],
            "extend_right": False,
            "memo": "첫 저점",
            "locked": True,
            "visible": True,
        },
    )
    assert _strict(edited)["memo"] == "첫 저점"
    listed = _strict(client.get(f"{BASE}/lines", params=SAMSUNG))
    assert [line["line_id"] for line in listed["lines"]] == [saved["line_id"], hline["line_id"]]

    confluence = client.post(
        f"{BASE}/confluence",
        json={
            "instrument_kind": "stock",
            "instrument_code": "005930",
            "lines": [
                {
                    "ref": "auto-1",
                    "timeframe": "day",
                    "kind": "fib_channel",
                    "anchors": channels["candidates"][-1]["drawing"]["anchors"],
                }
            ],
        },
    )
    assert confluence.status_code == 200, confluence.text
    assert _strict(confluence)["caution"]

    assert client.delete(f"{BASE}/lines/{hline['line_id']}").status_code == 204
    missing = client.delete(f"{BASE}/lines/{hline['line_id']}")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "chart.line.not_found")


@pytest.mark.parametrize(
    ("path", "params", "status", "code"),
    [
        (
            "candles",
            {"kind": "stock", "code": "999999", "timeframe": "day"},
            404,
            "chart.instrument.not_found",
        ),
        ("candles", {**SAMSUNG, "timeframe": "hour"}, 422, "chart.request_invalid"),
        (
            "regime",
            {
                **SAMSUNG,
                "timeframe": "day",
                "window_start": "2001-01-02",
                "window_end": "2025-10-01",
            },
            422,
            "chart.window.outside_series",
        ),
    ],
)
def test_rejections_are_coded(path: str, params: dict[str, str], status: int, code: str) -> None:
    response = TestClient(build_http_app()).get(f"{BASE}/{path}", params=params)
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message"]


def test_a_line_that_cannot_be_drawn_is_rejected_with_its_code() -> None:
    response = TestClient(build_http_app()).post(
        f"{BASE}/lines",
        json={
            "instrument_kind": "stock",
            "instrument_code": "005930",
            "timeframe": "day",
            "kind": "fib_retracement",
            "source": "manual",
            "anchors": [{"bar_date": "2025-09-01", "price": 100.0}],
        },
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (
        422,
        "chart.line.anchor_count",
    )


def test_real_data_candles_show_actual_prices(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity`")
    from tests.test_adapters_equity_duckdb_chart import _raw, build_chart_root

    root = build_chart_root(tmp_path / "root")
    client = TestClient(build_http_app(equity_adapter="duckdb", equity_root=root))
    body = _strict(client.get(f"{BASE}/candles", params={**SAMSUNG, "timeframe": "day"}))
    assert body["context"]["practice_data"] is False and body["context"]["notice"] is None
    # 조정가 = 원주가 × 2, k = 원주가 ÷ 조정가 = 0.5 → 화면 가격은 원주가다
    last = body["candles"][-1]
    assert (last["start"], last["close"]) == (
        "2024-01-11",
        _raw(date(2024, 1, 11)),
    )
    index = _strict(client.get(f"{BASE}/instruments", params={"query": "건설"}))["instruments"]
    assert sorted(item["code"] for item in index) == ["KOSDAQ:건설", "KOSPI:건설"]
