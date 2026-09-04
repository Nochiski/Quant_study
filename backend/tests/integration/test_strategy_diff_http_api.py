"""P1-08 semantic diff API over stored revisions."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


def test_diff_between_revisions_reports_semantic_changes_only() -> None:
    client = TestClient(build_http_app())
    text = (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8")
    created = client.post(
        "/api/v1/strategy-documents", json={"source": text, "format": "yaml"}
    ).json()
    strategy_id = created["strategy_id"]
    commented = text.replace("max_name_weight: 0.05", "max_name_weight: 0.04  # tighter cap")
    client.post(
        f"/api/v1/strategy-documents/{strategy_id}/revisions",
        json={"source": commented, "format": "yaml", "expected_revision": 1},
    )
    client.post(
        f"/api/v1/strategy-documents/{strategy_id}/revisions",
        json={
            "source": commented
            + "# note only\
",
            "format": "yaml",
            "expected_revision": 2,
        },
    )

    diff = client.get(
        f"/api/v1/strategies/{strategy_id}/diff", params={"base": 1, "target": 2}
    ).json()
    assert (diff["base_revision"], diff["target_revision"]) == (1, 2)
    assert diff["base_spec_hash"] == created["spec_hash"] != diff["target_spec_hash"]
    assert diff["changes"] == [
        {"pointer": "/risk/max_name_weight", "kind": "changed", "before": 0.05, "after": 0.04}
    ]

    comment_only = client.get(
        f"/api/v1/strategies/{strategy_id}/diff", params={"base": 2, "target": 3}
    ).json()
    assert comment_only["changes"] == []
    assert comment_only["base_spec_hash"] == comment_only["target_spec_hash"]

    reverse = client.get(
        f"/api/v1/strategies/{strategy_id}/diff", params={"base": 2, "target": 1}
    ).json()
    assert reverse["changes"][0]["before"] == 0.04 and reverse["changes"][0]["after"] == 0.05

    missing = client.get(f"/api/v1/strategies/{strategy_id}/diff", params={"base": 1, "target": 9})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "strategy.not_found"
    assert (
        client.get("/api/v1/strategies/nope/diff", params={"base": 1, "target": 1}).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/strategies/{strategy_id}/diff", params={"base": 0, "target": 1}
        ).status_code
        == 422
    )
