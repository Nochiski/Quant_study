"""api.dart_keys() — v3 프로덕션 키(DART_API_KEY)는 어떤 경우에도 폴백 순서에 들어가지 않는다.

플랜 P0 Task 0.4. 근거: 08-28 백필이 우리 키 2개(80,000콜)를 소진한 뒤 자동 폴백으로
v3 키를 35,588~39,002콜 태웠다(reviews/2026-09-09-daily-findings-E §3-2, B §3-2).
"""
import importlib
import sys

_IMPORT_TIME_KEYS = "KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\n"   # api.py:35 가 import 시점에 요구


def _load_api(tmp_path, monkeypatch, env_text: str):
    env = tmp_path / ".env"
    env.write_text(_IMPORT_TIME_KEYS + env_text, encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    sys.modules.pop("api", None)
    return importlib.import_module("api")


def test_dart_keys_exclude_v3_production_key(tmp_path, monkeypatch):
    api = _load_api(tmp_path, monkeypatch,
                    "DART_API_KEY=prod-key\nDART_API_KEY_2=ours-2\nDART_API_KEY_3=ours-3\n")
    assert api.dart_keys() == [("k2", "ours-2"), ("k3", "ours-3")]


def test_dart_keys_empty_when_only_v3_key_present(tmp_path, monkeypatch):
    # 우리 키가 하나도 없으면 빈 목록 — 조용히 v3 키로 넘어가는 대신 호출 쪽이 실패해야 한다.
    api = _load_api(tmp_path, monkeypatch, "DART_API_KEY=prod-key\n")
    assert api.dart_keys() == []


def test_dart_keys_keep_numeric_order(tmp_path, monkeypatch):
    api = _load_api(tmp_path, monkeypatch,
                    "DART_API_KEY_3=c\nDART_API_KEY_2=b\nDART_API_KEY_5=e\n")
    assert api.dart_keys() == [("k2", "b"), ("k3", "c"), ("k5", "e")]
