"""스냅샷 id → KST 날짜 (`stage.snapshot.snapshot_kst_date`) — equity EG13 기준일의 경계값 (K1-3a).

id 는 만든 시각(UTC)이다. KST 는 UTC+9 라 15:00Z 부터 다음 날이 된다 — 아침 체인 스냅샷(06:00 KST)은
id 의 날짜 숫자가 전날이다. 형식 밖 id 는 기준일을 추정하지 않고 거부한다.
"""
from datetime import date

import pytest
from stage import snapshot


@pytest.mark.parametrize(("snapshot_id", "expect"), [
    ("snap_20261009T145959Z", date(2026, 10, 9)),      # 23:59:59 KST — 같은 날
    ("snap_20261009T150000Z", date(2026, 10, 10)),     # 00:00:00 KST — 다음 날
    ("snap_20261009T210000Z", date(2026, 10, 10)),     # 아침 체인 06:00 KST
    ("snap_20261231T150000Z", date(2027, 1, 1)),       # 해 넘김
    ("snap_20280228T150000Z", date(2028, 2, 29)),      # 윤년 — 2월 29일로
    ("snap_20280229T150000Z", date(2028, 3, 1)),       # 윤일 자신이 id
    ("snap_20270228T150000Z", date(2027, 3, 1)),       # 평년 — 3월 1일로
])
def test_KST_날짜_경계(snapshot_id: str, expect: date) -> None:
    assert snapshot.snapshot_kst_date(snapshot_id) == expect


@pytest.mark.parametrize("snapshot_id", [
    "snap_2026109T150000Z",          # 월 자릿수 부족 — strptime 은 받아 주므로 왕복 대조가 막는다
    "snap_20261009T1500Z",           # 시각 자릿수 부족
    "snap_20261009t150000z",         # 소문자 — strptime 은 대소문자를 가리지 않는다
    "SNAP_20261009T150000Z",         # 접두사 대문자
    "snapshot_20261009T150000Z",     # 다른 접두사
    "20261009T150000Z",              # 접두사 없음
    "snap_20261009T150000",          # UTC 표지 'Z' 없음
    "snap_20270229T000000Z",         # 평년의 2월 29일
    "snap_test",                     # 손으로 붙인 이름
    "",
])
def test_형식_밖_id는_거부한다(snapshot_id: str) -> None:
    with pytest.raises(ValueError, match="snapshot id outside format"):
        snapshot.snapshot_kst_date(snapshot_id)
