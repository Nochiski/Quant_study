"""동결 schema 1.0 revision의 HTTP 계약 (spec D2, P1-03).

- 문서 API는 동결 revision을 `requires_upgrade: true`로 돌려준다.
- saved-reference backtest·trace는 저장된 hash를 정확히 대도 422 `*.strategy.requires_upgrade`다.
  업그레이드된 spec의 hash가 저장된 hash와 다르므로 재현 가능한 provenance를 만들 수 없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._backtest_contract import Backtest422Response
from strategy_workbench.adapters.inbound.http_api._trace_contract import Trace422Response
from strategy_workbench.bootstrap.facade.http import build_http_app
from tests.frozen_revision_rows import FROZEN_SPEC_HASH, seed_frozen_rows


def _client(tmp_path: Path) -> TestClient:
    path = tmp_path / "frozen-http.sqlite3"
    seed_frozen_rows(path)
    return TestClient(build_http_app(strategy_repository_path=path))


def _reference(strategy_id: str) -> dict[str, Any]:
    return {
        "kind": "saved_revision",
        "strategy_id": strategy_id,
        "revision": 1,
        "expected_spec_hash": FROZEN_SPEC_HASH,
    }


def test_document_api_marks_frozen_revisions(tmp_path: Path) -> None:
    client = _client(tmp_path)

    document = client.get("/api/v1/strategies/frozen-doc/revisions/1/document")
    legacy = client.get("/api/v1/strategies/frozen-legacy/revisions/1/document")

    assert document.status_code == 200, document.text
    body = document.json()
    assert body["requires_upgrade"] is True
    assert body["schema_version"] == "1.0"
    assert body["spec_hash"] == FROZEN_SPEC_HASH
    assert body["source"].startswith("# P0-01 golden authoring fixture")
    assert body["spec"]["factors"][0]["factor_id"] == "momentum"  # 응답 spec은 1.1 모양
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["requires_upgrade"] is True and legacy.json()["generated"] is True
    assert '"schema_version": "1.1"' in legacy.json()["source"]

    listed = client.get("/api/v1/strategies")
    assert listed.status_code == 200
    assert {item["strategy_id"] for item in listed.json()["items"]} >= {
        "frozen-doc",
        "frozen-legacy",
    }


def test_saved_reference_backtest_of_a_frozen_revision_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/v1/backtests",
        json={"strategy_source": _reference("frozen-doc"), "core": "python"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "backtest.strategy.requires_upgrade"
    assert "frozen-doc" in detail["message"] and "schema_version=1.0" in detail["message"]
    TypeAdapter(Backtest422Response).validate_python(response.json())  # 선언된 422 계약 안에 있다


def test_saved_reference_trace_of_a_frozen_revision_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            "strategy_source": _reference("frozen-legacy"),
            "as_of": "2026-08-31",
            "security_ids": ["sec-1"],
            "factor_id": "momentum",
            "node_ids": ["mom_252"],
            "include_raw": False,
            "limit": 10,
        },
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "trace.strategy.requires_upgrade"
    assert "frozen-legacy" in detail["message"]
    TypeAdapter(Trace422Response).validate_python(response.json())


def test_json_spec_api_marks_frozen_revisions_and_lists(tmp_path: Path) -> None:
    client = _client(tmp_path)

    saved = client.get("/api/v1/strategies/frozen-doc")
    listed = client.get("/api/v1/strategies")
    history = client.get("/api/v1/strategies/frozen-doc/revisions")

    assert saved.status_code == 200, saved.text
    assert saved.json()["requires_upgrade"] is True
    assert saved.json()["spec_hash"] == FROZEN_SPEC_HASH
    frozen_items = [
        item for item in listed.json()["items"] if item["strategy_id"].startswith("frozen-")
    ]
    assert frozen_items and all(item["requires_upgrade"] for item in frozen_items)
    assert history.status_code == 200, history.text
    assert [item["requires_upgrade"] for item in history.json()["items"]] == [True]


def test_json_spec_api_rejects_a_retired_schema_version_with_422(tmp_path: Path) -> None:
    """동결 응답의 spec을 그대로 되보내는 왕복이 저장소 500이 아니라 검증 422로 끝난다."""
    client = _client(tmp_path)
    spec = client.get("/api/v1/strategies/frozen-doc").json()["spec"]

    response = client.post("/api/v1/strategies", json=spec)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "strategy.invalid"
    codes = {issue["code"] for issue in detail["validation"]["issues"]}
    assert "strategy.schema_version.unsupported" in codes
