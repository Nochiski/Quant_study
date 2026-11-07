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
SAMSUNG = {"kind": "stock", "code": "practice:005930"}
REAL_SAMSUNG = {"kind": "stock", "code": "005930"}


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
    assert [item["code"] for item in found["instruments"]] == ["practice:005930"]

    week = _strict(client.get(f"{BASE}/candles", params={**SAMSUNG, "timeframe": "week"}))
    assert week["context"]["practice_data"]
    assert week["candles"][-1]["in_progress"] and week["last_completed"] < week["context"]["as_of"]
    # 등락률은 직전 봉 종가 대비이고 첫 봉은 비교할 봉이 없다(화면은 계산하지 않고 읽는다)
    first, second = week["candles"][:2]
    assert first["change_pct"] is None
    assert second["change_pct"] == pytest.approx((second["close"] / first["close"] - 1) * 100)
    # 표시 구간을 잘라도 첫 봉의 등락률은 계열의 바로 앞 봉 대비다
    later = _strict(
        client.get(
            f"{BASE}/candles",
            params={**SAMSUNG, "timeframe": "week", "start": week["candles"][2]["start"]},
        )
    )
    assert later["candles"][0]["change_pct"] == pytest.approx(
        (week["candles"][2]["close"] / second["close"] - 1) * 100
    )
    # 한 호가 표는 대상 머리에 실린다(종목 = 주식 호가표)
    ticks = week["context"]["instrument"]["tick_steps"]
    assert ticks[0] == {"below": 2000.0, "tick": 1.0}
    assert ticks[-1] == {"below": None, "tick": 1000.0}

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
            "instrument_code": "practice:005930",
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
                "instrument_code": "practice:005930",
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
    assert listed["level_choices"] == [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.618, 2.618]

    confluence = client.post(
        f"{BASE}/confluence",
        json={
            "instrument_kind": "stock",
            "instrument_code": "practice:005930",
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
    assert _strict(confluence)["rules"]
    # 내 선 빼기는 요청 칸이다 — 손으로 그은 수평선은 빠지고 저장한 자동 채널은 남는다
    autos_only = _strict(
        client.post(
            f"{BASE}/confluence",
            json={
                "instrument_kind": "stock",
                "instrument_code": "practice:005930",
                "lines": [],
                "include_mine": False,
            },
        )
    )
    members = {m["ref"] for g in autos_only["groups"] for m in g["members"]}
    assert hline["line_id"] not in members
    # 판정 규칙·피벗 규칙·첫 점 고르기 목록·수평 구간 규칙은 응답 문장이다
    regime = _strict(client.get(f"{BASE}/regime", params={**SAMSUNG, "timeframe": "day"}))
    assert regime["rules"] and regime["pivot_rule"]
    assert {p["kind"] for p in channels["anchor_choices"]} <= {"low"}
    zones = _strict(client.get(f"{BASE}/zones", params={**SAMSUNG, "timeframe": "day"}))
    assert zones["rules"]

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
        (
            "regime",
            {**SAMSUNG, "timeframe": "day", "window_start": "2025-09-01"},
            422,
            "chart.window.incomplete",
        ),
        (
            "candles",
            {**SAMSUNG, "timeframe": "day", "start": "2025-10-01", "end": "2025-09-01"},
            422,
            "chart.window.reversed",
        ),
        # 분석 파라미터 범위는 domain 하나가 검사한다(HTTP 스키마에 같은 범위를 두지 않는다)
        (
            "regime",
            {**SAMSUNG, "timeframe": "day", "candidate_sensitivity": "0"},
            422,
            "chart.params.invalid",
        ),
        ("zones", {**SAMSUNG, "timeframe": "day", "min_touches": "0"}, 422, "chart.params.invalid"),
        ("instruments", {"query": "삼성", "limit": "0"}, 422, "chart.params.invalid"),
        # 연습용 원천은 실데이터 코드를 모른다
        ("candles", {**REAL_SAMSUNG, "timeframe": "day"}, 404, "chart.instrument.not_found"),
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
            "instrument_code": "practice:005930",
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


@pytest.mark.parametrize(
    "auto",
    [
        # 표준 JSON 밖 토큰(NaN·Infinity)도 저장소 오류(500)가 아니라 자동 기록 거절(422)이다
        {"algorithm_version": "chart_analysis@1", "params": {"width_atr": float("nan")}},
        {
            "algorithm_version": "chart_analysis@1",
            "params": {},
            "regime": {"verdict": "up", "strength": float("inf"), "structure": "up"},
        },
    ],
)
def test_non_finite_auto_record_numbers_are_rejected(auto: dict[str, object]) -> None:
    body = {
        "instrument_kind": "stock",
        "instrument_code": "practice:005930",
        "timeframe": "day",
        "kind": "hline",
        "source": "auto",
        "anchors": [{"bar_date": "2025-09-01", "price": 2_000.0}],
        "auto": auto,
    }
    response = TestClient(build_http_app()).post(
        f"{BASE}/lines",
        content=json.dumps(body, allow_nan=True),
        headers={"content-type": "application/json"},
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (
        422,
        "chart.line.auto_record",
    )


def test_preview_returns_the_drawing_without_saving(tmp_path: Path) -> None:
    client = TestClient(build_http_app(research_db_path=tmp_path / "research.sqlite3"))
    draft = {
        "instrument_kind": "stock",
        "instrument_code": "practice:005930",
        "timeframe": "week",
        "kind": "hline",
        # 수요일 날짜는 그 주의 시작일(월)로 붙는다
        "anchors": [{"bar_date": "2025-09-03", "price": 2_000.0}],
    }
    preview = _strict(client.post(f"{BASE}/lines/preview", json=draft))
    assert preview["anchors"][0]["bar_date"] == "2025-09-01"
    assert preview["paths"][0]["end"]["price"] == pytest.approx(2_000.0)
    assert _strict(client.get(f"{BASE}/lines", params=SAMSUNG))["lines"] == []
    in_progress = {**draft, "anchors": [{"bar_date": "2025-10-28", "price": 2_000.0}]}
    response = client.post(f"{BASE}/lines/preview", json=in_progress)
    assert (response.status_code, response.json()["detail"]["code"]) == (
        422,
        "chart.line.anchor_in_progress",
    )


def test_real_data_candles_show_actual_prices(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity`")
    from tests.test_adapters_equity_duckdb_chart import _raw, build_chart_root

    root = build_chart_root(tmp_path / "root")
    client = TestClient(build_http_app(equity_adapter="duckdb", equity_root=root))
    body = _strict(client.get(f"{BASE}/candles", params={**REAL_SAMSUNG, "timeframe": "day"}))
    assert body["context"]["practice_data"] is False and body["context"]["notice"] is None
    # 조정가 = 원주가 × 2, k = 원주가 ÷ 조정가 = 0.5 → 화면 가격은 원주가다
    last = body["candles"][-1]
    assert (last["start"], last["close"]) == (
        "2024-01-11",
        _raw(date(2024, 1, 11)),
    )
    index = _strict(client.get(f"{BASE}/instruments", params={"query": "건설"}))["instruments"]
    assert sorted(item["code"] for item in index) == ["KOSDAQ:건설", "KOSPI:건설"]
    # 금요일(01-12)은 잠정 행뿐이라 기준일은 목요일이고 그 주는 진행 중이다
    week = _strict(client.get(f"{BASE}/candles", params={**REAL_SAMSUNG, "timeframe": "week"}))
    assert week["context"]["as_of"] == "2024-01-11"
    assert (week["candles"][-1]["start"], week["candles"][-1]["in_progress"]) == (
        "2024-01-08",
        True,
    )


def test_practice_lines_never_reach_the_real_chart(tmp_path: Path) -> None:
    """두 원천이 같은 연구 DB 파일을 써도 연습 선은 실데이터 차트·중첩에 나오지 않는다."""
    pytest.importorskip("duckdb", reason="backend optional extra `equity`")
    from tests.test_adapters_equity_duckdb_chart import build_chart_root

    db = tmp_path / "research.sqlite3"
    practice = TestClient(build_http_app(research_db_path=db))
    saved = practice.post(
        f"{BASE}/lines",
        json={
            "instrument_kind": "stock",
            "instrument_code": "practice:005930",
            "timeframe": "day",
            "kind": "hline",
            "source": "manual",
            "anchors": [{"bar_date": "2025-09-01", "price": 2_000.0}],
        },
    )
    assert saved.status_code == 201, saved.text

    real = TestClient(
        build_http_app(
            equity_adapter="duckdb",
            equity_root=build_chart_root(tmp_path / "root"),
            research_db_path=db,
        )
    )
    assert _strict(real.get(f"{BASE}/lines", params=REAL_SAMSUNG))["lines"] == []
    confluence = _strict(
        real.post(
            f"{BASE}/confluence",
            json={"instrument_kind": "stock", "instrument_code": "005930", "lines": []},
        )
    )
    assert (confluence["groups"], confluence["skipped_line_ids"]) == ([], [])
    # 연습 코드로 실데이터 앱에 물어도 모르는 대상이고, 그 선을 지우지도 못한다
    assert real.get(f"{BASE}/lines", params=SAMSUNG).status_code == 404
    line_id = saved.json()["line_id"]
    refused = real.delete(f"{BASE}/lines/{line_id}")
    assert (refused.status_code, refused.json()["detail"]["code"]) == (404, "chart.line.not_found")
    assert [
        line["line_id"] for line in _strict(practice.get(f"{BASE}/lines", params=SAMSUNG))["lines"]
    ] == [line_id]
