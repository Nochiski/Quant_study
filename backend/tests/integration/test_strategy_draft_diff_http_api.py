"""저장 전 diff는 revision diff와 같은 정책을 사용하고 저장하지 않는다."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "strategy_documents"


def test_draft_diff_matches_revision_diff_without_saving() -> None:
    client = TestClient(build_http_app())
    source = (FIXTURES / "quality_momentum.json").read_text(encoding="utf-8")
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": source, "format": "json"}
    ).json()
    strategy_id = saved["strategy_id"]
    tree = json.loads(source)
    tree["risk"]["max_name_weight"] = 0.07
    changed = json.dumps(tree)
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/draft-diff",
        json={"base_revision": 1, "source": changed, "format": "json"},
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["source_hash"] == hashlib.sha256(changed.encode()).hexdigest()
    assert draft["base_spec_hash"] == saved["spec_hash"]
    assert draft["changes"] == [
        {"pointer": "/risk/max_name_weight", "kind": "changed", "before": 0.05, "after": 0.07}
    ]
    assert client.get(f"/api/v1/strategies/{strategy_id}/revisions").json()["total"] == 1
    revised = client.post(
        f"/api/v1/strategy-documents/{strategy_id}/revisions",
        json={"expected_revision": 1, "source": changed, "format": "json"},
    )
    assert revised.status_code == 201, revised.text
    revision = client.get(f"/api/v1/strategies/{strategy_id}/diff?base=1&target=2").json()
    assert draft["changes"] == revision["changes"]
    assert draft["target_spec_hash"] == revision["target_spec_hash"]


def test_draft_diff_ignores_comments() -> None:
    client = TestClient(build_http_app())
    source = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": source, "format": "yaml"}
    ).json()
    response = client.post(
        f"/api/v1/strategies/{saved['strategy_id']}/draft-diff",
        json={"base_revision": 1, "source": source + "\n# 의미 변화 없음\n", "format": "yaml"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["changes"] == []
    assert response.json()["target_spec_hash"] == saved["spec_hash"]


@pytest.mark.parametrize("base_revision", [0, -1])
def test_draft_diff_invalid_revision_is_422(base_revision: int) -> None:
    client = TestClient(build_http_app())
    response = client.post(
        "/api/v1/strategies/missing/draft-diff",
        json={"base_revision": base_revision, "source": "", "format": "yaml"},
    )
    assert response.status_code == 422


def test_draft_diff_missing_revision_and_invalid_source_use_existing_errors() -> None:
    client = TestClient(build_http_app())
    source = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": source, "format": "yaml"}
    ).json()
    url = f"/api/v1/strategies/{saved['strategy_id']}/draft-diff"
    missing = client.post(url, json={"base_revision": 2, "source": source, "format": "yaml"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "strategy.not_found"
    invalid = client.post(url, json={"base_revision": 1, "source": "[", "format": "yaml"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "strategy_document.invalid"
    assert invalid.json()["detail"]["diagnostics"]


def test_draft_diff_hides_boolean_promotion_nodes() -> None:
    client = TestClient(build_http_app())
    tree = {
        "schema_version": "1.2",
        "title": "참거짓 출력",
        "factors": [
            {
                "factor_id": "flag",
                "direction": "high",
                "graph": {
                    "nodes": [
                        {"kind": "constant", "node_id": "left", "value": 1},
                        {"kind": "constant", "node_id": "right", "value": 2},
                        {
                            "kind": "comparison",
                            "node_id": "flag",
                            "operator": "gt",
                            "left_node_id": "left",
                            "right_node_id": "right",
                        },
                    ],
                    "output_node_id": "flag",
                },
            }
        ],
    }
    saved = client.post(
        "/api/v1/strategy-documents", json={"source": json.dumps(tree), "format": "json"}
    )
    assert saved.status_code == 201, saved.text
    tree["factors"][0]["graph"]["nodes"][2]["operator"] = "lt"
    response = client.post(
        f"/api/v1/strategies/{saved.json()['strategy_id']}/draft-diff",
        json={"base_revision": 1, "source": json.dumps(tree), "format": "json"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["changes"] == [
        {
            "pointer": "/factors/0/graph/nodes/2/operator",
            "kind": "changed",
            "before": "gt",
            "after": "lt",
        }
    ]
