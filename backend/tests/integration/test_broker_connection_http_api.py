"""키움 모의 연결 설정 API(S06 ②-2): 저장·조회·확인·삭제와 키 비노출. 키움 대신 대본 probe."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_workbench.bootstrap.facade.http import build_http_app

PATH = "/api/v1/broker-connections/kiwoom-mock"
APP_KEY = "fake-app-key-0123456789"
SECRET = "fake-secret-0123456789"
CONNECTED_ACCOUNT = "5012345678"  # 대본 probe가 연결됨으로 답하는 계좌


def _client() -> TestClient:
    return TestClient(build_http_app(broker_fake_probe=True))


def _no_secret(text: str, account: str = CONNECTED_ACCOUNT) -> None:
    for secret in (APP_KEY, SECRET, account):
        assert secret not in text


def test_save_verify_and_delete_never_return_the_keys() -> None:
    client = _client()
    assert client.get(PATH).json() == {"saved": False, "account_tail": None, "unreadable": False}
    assert client.post(f"{PATH}/verify").json()["outcome"] == "not_saved"

    saved = client.put(
        PATH, json={"app_key": APP_KEY, "secret_key": SECRET, "account_number": "5012-3456-78"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"saved": True, "account_tail": "5678", "unreadable": False}
    _no_secret(saved.text)
    _no_secret(client.get(PATH).text)

    verified = client.post(f"{PATH}/verify")
    assert verified.json()["outcome"] == "connected"
    assert verified.json()["message"].startswith("연결됐습니다")
    _no_secret(verified.text)

    client.put(
        PATH, json={"app_key": APP_KEY, "secret_key": SECRET, "account_number": "5099999999"}
    )
    mismatch = client.post(f"{PATH}/verify").json()
    assert (mismatch["outcome"], mismatch["account_tail"]) == ("account_mismatch", "9999")

    assert client.delete(PATH).status_code == 204
    assert client.get(PATH).json()["saved"] is False


def test_a_broken_saved_entry_is_reported_and_can_be_replaced_or_deleted(
    isolate_runtime_state_paths: Path,
) -> None:
    # 리뷰 #594 P3: 저장 항목이 깨지면 500 이 나고 화면이 삭제 버튼을 숨겨 지울 수 없었다
    secrets = isolate_runtime_state_paths / "secrets.json"
    secrets.write_text(json.dumps({"broker.kiwoom_mock": '{"app_key": "fake-app-key-0123"}'}))
    client = _client()
    broken = client.get(PATH)
    assert broken.status_code == 200
    assert broken.json() == {"saved": False, "account_tail": None, "unreadable": True}
    check = client.post(f"{PATH}/verify").json()
    assert check["outcome"] == "unreadable" and "다시 저장" in check["message"]
    assert "fake-app-key-0123" not in broken.text + json.dumps(check)
    assert client.delete(PATH).status_code == 204
    assert client.get(PATH).json()["unreadable"] is False


def test_invalid_bodies_are_coded_and_do_not_echo_input() -> None:
    client = _client()
    short = client.put(
        PATH, json={"app_key": APP_KEY, "secret_key": SECRET, "account_number": "12"}
    )
    assert short.status_code == 422
    assert short.json()["detail"]["code"] == "broker.credentials_invalid"
    assert short.json()["detail"]["field"] == "account_number"
    _no_secret(short.text)

    wrong_type = client.put(PATH, json={"app_key": 123, "secret_key": SECRET})
    assert wrong_type.status_code == 422
    assert wrong_type.json()["detail"]["code"] == "broker.request_invalid"
    _no_secret(wrong_type.text)
    assert client.get(PATH).json()["saved"] is False


def test_openapi_marks_the_key_fields_write_only() -> None:
    schema = _client().get("/openapi.json").json()
    properties = schema["components"]["schemas"]["SaveKiwoomMockConnectionBody"]["properties"]
    assert all(
        properties[name]["writeOnly"] for name in ("app_key", "secret_key", "account_number")
    )
    status = schema["components"]["schemas"]["KiwoomMockConnectionBody"]["properties"]
    assert set(status) == {"saved", "account_tail", "unreadable"}  # 키 칸은 응답에 없다
