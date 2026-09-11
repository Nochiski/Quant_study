"""sweep_disclosure — 창 대조·재스윕 판정(순수 함수). 검수 D H3(2026-09-10) 후속."""
import os
import tempfile

# sweep_disclosure 는 import 시점에 `api` 를 통해 kael .env 를 읽는다(api.py:35). CI 에는 .env 가 없으므로
# 임시 .env 를 만들어 QL_ENV 로 가리킨다 — test_api_dart_keys 와 같은 방식. 키 값은 더미다.
if not os.environ.get("QL_ENV"):
    _env = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
    _env.write("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\nDART_API_KEY_2=k2\n")
    _env.close()
    os.environ["QL_ENV"] = _env.name

import sweep_disclosure as sd  # noqa: E402  # reason: 위 QL_ENV 설정이 import 보다 먼저여야 한다


def test_reconcile_accepts_ledger_excess_as_source_side_deletion():
    # 09-10 실측: total_count 44,156 = 수신 44,156 < 적재 고유 44,162. 초과 6건은 DART 가 지운 공시라
    # 원장에만 남은 것 — "받은 것을 다 적었나" 는 참이므로 완료로 적고 초과분만 기록한다.
    r = sd.reconcile(total_count=44156, n_recv=44156, n_db=44162)
    assert r.ok and r.excess == 6


def test_reconcile_still_flags_paging_loss_and_store_loss():
    assert not sd.reconcile(total_count=100, n_recv=99, n_db=99).ok       # 페이징 누락
    assert not sd.reconcile(total_count=100, n_recv=100, n_db=98).ok      # 적재 유실
    assert sd.reconcile(total_count=100, n_recv=100, n_db=100).excess == 0


def test_window_is_reswept_while_inside_lookback():
    assert sd.is_resweep("20260930", today="20261005", lookback_days=30)      # 분기 끝난 지 5일
    assert not sd.is_resweep("20260930", today="20261105", lookback_days=30)  # 36일 — 종결
    assert sd.is_resweep("20261231", today="20261005", lookback_days=0)       # 진행 중 창은 항상
    assert not sd.is_resweep("20260630", today="20261005", lookback_days=30)
