"""sweep_disclosure — 창 대조·재스윕 판정(순수 함수). 검수 D H3(2026-09-10) 후속."""
import sweep_disclosure as sd


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
