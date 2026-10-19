"""사용자 팩터 라이브러리 API(US-CS-08): 저장·새 버전·목록·숨기기·matches 와 재시작 뒤 보존."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app


def _factor(
    window: int = 20, *, factor_id: str = "close_mean", field_id: str = "price.close"
) -> dict[str, object]:
    return {
        "factor_id": factor_id,
        "label": "종가 평균",
        "direction": "high",
        "graph": {
            "nodes": [
                {"node_id": "close", "kind": "field", "field_id": field_id},
                {
                    "node_id": "mean",
                    "kind": "time_series",
                    "operator": "mean",
                    "input_node_id": "close",
                    "window": window,
                },
            ],
            "output_node_id": "mean",
        },
    }


def _document(*factors: dict[str, object]) -> str:
    return json.dumps(
        {"schema_version": "1.2", "title": "문서", "factors": list(factors)}, ensure_ascii=False
    )


def _save(
    client: TestClient, factor: dict[str, object], name: str = "종가 평균"
) -> dict[str, object]:
    response = client.post("/api/v1/factor-library", json={"name": name, "factor": factor})
    assert response.status_code == 201, response.text
    return response.json()


def _matches(client: TestClient, source: str) -> list[dict[str, object]]:
    response = client.post(
        "/api/v1/factor-library/matches", json={"format": "json", "source": source}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compiled"] is True
    return body["matches"]


def test_save_lists_available_factor_and_rejects_duplicate_key() -> None:
    client = TestClient(build_http_app())
    saved = _save(client, _factor())

    assert saved["key"] == "close_mean"
    assert saved["latest_version"] == 1
    assert saved["availability"] == {"available": True, "missing_fields": [], "reasons": []}
    listed = client.get("/api/v1/factor-library").json()["items"]
    assert [item["library_id"] for item in listed] == [saved["library_id"]]

    duplicate = client.post(
        "/api/v1/factor-library", json={"name": "다른 이름", "factor": _factor(60)}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "library.factor.key_exists"
    assert duplicate.json()["detail"]["existing_library_id"] == saved["library_id"]


def test_invalid_factor_is_rejected_with_compile_diagnostics() -> None:
    client = TestClient(build_http_app())
    factor = _factor(field_id="price.not_a_field")

    response = client.post("/api/v1/factor-library", json={"name": "틀린 팩터", "factor": factor})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "library.factor.invalid"
    assert detail["diagnostics"], detail
    assert all(item["message"] for item in detail["diagnostics"])
    assert client.get("/api/v1/factor-library").json()["items"] == []


def test_versions_accumulate_and_unchanged_graph_is_not_a_new_version() -> None:
    client = TestClient(build_http_app())
    saved = _save(client, _factor())
    library_id = saved["library_id"]

    unchanged = client.post(
        f"/api/v1/factor-library/{library_id}/versions",
        json={"factor": _factor(), "expected_latest": 1},
    )
    assert unchanged.status_code == 409
    assert unchanged.json()["detail"]["code"] == "library.version.unchanged"

    # 이름만 바꿔 보낸 factor_id 는 항목의 키로 저장된다.
    second = client.post(
        f"/api/v1/factor-library/{library_id}/versions",
        json={"factor": _factor(60, factor_id="renamed"), "expected_latest": 1},
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert [version["version"] for version in body["versions"]] == [1, 2]
    assert body["versions"][1]["factor"]["factor_id"] == "close_mean"
    assert body["versions"][0]["identity"] != body["versions"][1]["identity"]

    stale = client.post(
        f"/api/v1/factor-library/{library_id}/versions",
        json={"factor": _factor(120), "expected_latest": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "library.version.conflict"
    assert stale.json()["detail"]["current_version"] == 2


def test_matches_tell_current_outdated_and_modified_and_ignore_hidden() -> None:
    client = TestClient(build_http_app())
    library_id = _save(client, _factor())["library_id"]
    client.post(
        f"/api/v1/factor-library/{library_id}/versions",
        json={"factor": _factor(60), "expected_latest": 1},
    )
    unrelated = {**_factor(5, factor_id="other"), "label": "다른 팩터"}

    found = _matches(
        client,
        _document(_factor(20), {**_factor(60, factor_id="copy"), "label": "복사"}, unrelated),
    )
    by_index = {item["factor_index"]: item for item in found}
    assert by_index[0]["status"] == "outdated"
    assert by_index[0]["matched_version"] == 1
    assert by_index[0]["latest_version"] == 2
    # 키가 달라도 그래프가 최신 버전과 같으면 그 항목의 최신이다.
    assert by_index[1]["status"] == "current"
    assert 2 not in by_index

    modified = _matches(client, _document(_factor(30)))
    assert [(item["status"], item["matched_version"]) for item in modified] == [("modified", None)]

    hidden = client.patch(f"/api/v1/factor-library/{library_id}", json={"hidden": True})
    assert hidden.status_code == 200
    assert client.get("/api/v1/factor-library").json()["items"] == []
    assert len(client.get("/api/v1/factor-library?include_hidden=true").json()["items"]) == 1
    assert _matches(client, _document(_factor(60))) == []


def test_matches_report_uncompiled_documents_without_guessing() -> None:
    client = TestClient(build_http_app())
    _save(client, _factor())

    response = client.post(
        "/api/v1/factor-library/matches", json={"format": "yaml", "source": "factors: [broken"}
    )

    assert response.status_code == 200
    assert response.json() == {"compiled": False, "matches": []}


def test_library_survives_container_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "strategy.sqlite3"
    first = TestClient(build_http_app(strategy_repository_path=path))
    saved = _save(first, _factor())

    second = TestClient(build_http_app(strategy_repository_path=path))
    restored = second.get(f"/api/v1/factor-library/{saved['library_id']}")
    assert restored.status_code == 200
    assert restored.json() == saved
    assert second.get("/api/v1/factor-library/missing").json()["detail"]["code"] == (
        "library.factor.not_found"
    )
