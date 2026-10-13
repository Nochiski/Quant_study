"""선택 이유의 Unicode 공백 계약을 실제 HTTP와 도메인이 함께 적용한다."""

import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from strategy_workbench.adapters.inbound.http_api._experiment_routes import (
    ExperimentSelectionRequest,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.experiment.facade.trial import validate_selection_reason


@pytest.fixture(scope="module")
def client():
    with TestClient(build_http_app(), raise_server_exceptions=False) as result:
        yield result


@pytest.mark.parametrize(
    "reason", [""] + [chr(code) for code in range(sys.maxunicode + 1) if chr(code).isspace()]
)
def test_unicode_blank_reason_is_rejected_before_the_selection_use_case(client, reason):
    with pytest.raises(ValueError):
        validate_selection_reason(reason)
    response = client.post(
        "/api/v1/experiments/missing-experiment/selections",
        json={"trial_index": 0, "reason": reason},
    )
    assert response.status_code == 422, response.text
    with pytest.raises(ValidationError):
        TypeAdapter(ExperimentSelectionRequest).validate_python(
            {"trial_index": 0, "reason": reason}
        )


@pytest.mark.parametrize("reason", ["학습 근거", "\x1c 근거 \x1f", "\u200b", "\ufeff", " x "])
def test_nonblank_reason_keeps_its_original_text_and_reaches_the_use_case(client, reason):
    validate_selection_reason(reason)
    request = TypeAdapter(ExperimentSelectionRequest).validate_python(
        {"trial_index": 0, "reason": reason}
    )
    assert request.reason == reason
    response = client.post(
        "/api/v1/experiments/missing-experiment/selections",
        json={"trial_index": 0, "reason": reason},
    )
    assert response.status_code == 404, response.text
