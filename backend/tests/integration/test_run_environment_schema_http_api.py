"""P2-01 실행 설정 스키마 API: ETag·304·모델에서 유도한 기본값과 enum."""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

PATH = "/api/v1/run-environments/schema"


def test_endpoint_serves_the_run_environment_schema_with_its_hash_as_etag() -> None:
    client = TestClient(build_http_app())

    response = client.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    canonical = json.dumps(
        body["schema"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert body["schema_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert response.headers["ETag"] == f'"{body["schema_hash"]}"'
    schema = body["schema"]
    assert schema["title"] == "RunEnvironment"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["start", "end", "universe_id"]
    properties = schema["properties"]
    assert properties["fee_bps"]["default"] == 15.0
    assert properties["participation_rate"]["default"] == 0.1
    assert properties["slippage_bps"]["default"] == 10.0
    assert properties["timing"] == {
        "type": "string",
        "enum": ["next_open"],
        "default": "next_open",
        "x-description-key": "strategy.field.run_environment.timing",
    }
    # 화면 어휘는 frontend i18n 이 렌더하고 backend 는 키 줄기만 싣는다(P1-03 정책, 모든 필드 공통).
    # 비용 세 필드는 `/execution/*` 제약 카탈로그 행을 재사용하므로 그 행의 키를 쓴다.
    assert all("x-description-key" in prop for prop in properties.values())
    assert properties["fee_bps"]["x-description-key"] == "strategy.contract.execution.fee_bps"
    assert properties["missing"]["enum"] == ["drop", "keep", "zero", "cross_sectional_median"]
    assert properties["universe_id"]["x-catalog"] == "universe"


def test_endpoint_is_a_different_document_from_the_authoring_schema() -> None:
    client = TestClient(build_http_app())

    environment = client.get(PATH).json()
    authoring = client.get("/api/v1/strategy-documents/schema").json()

    assert environment["schema_hash"] != authoring["schema_hash"]
    assert "schema_version" not in environment["schema"]["properties"]


def test_endpoint_returns_304_for_a_matching_etag() -> None:
    client = TestClient(build_http_app())
    etag = client.get(PATH).headers["ETag"]

    cached = client.get(PATH, headers={"If-None-Match": etag})
    weak = client.get(PATH, headers={"If-None-Match": f"W/{etag}"})
    stale = client.get(PATH, headers={"If-None-Match": '"nope"'})

    assert cached.status_code == 304 and cached.headers["ETag"] == etag
    assert weak.status_code == 304
    assert stale.status_code == 200
