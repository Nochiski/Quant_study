"""daily.calendar — 휴장 캐시 기반 거래일 판정. 플랜 P1 Task 1.1."""
import datetime as dt
import json

from daily import calendar as cal


def _write(tmp_path, holidays, year=2026):
    p = tmp_path / "kis_holidays.json"
    p.write_text(json.dumps({"year": year, "holidays": holidays}), encoding="utf-8")
    return p


def test_trading_day_with_cache(tmp_path):
    c = cal.load(_write(tmp_path, ["20260924", "20260925", "20260926", "20260927"]))
    assert c.source == "kis_cache"
    assert c.is_trading_day(dt.date(2026, 9, 24)) is False      # 추석
    assert c.is_trading_day(dt.date(2026, 9, 23)) is True
    assert c.is_trading_day(dt.date(2026, 9, 26)) is False      # 토요일


def test_prev_trading_day_skips_holiday_and_weekend(tmp_path):
    c = cal.load(_write(tmp_path, ["20260924", "20260925"]))
    assert c.prev_trading_day(dt.date(2026, 9, 28)) == dt.date(2026, 9, 23)
    assert c.prev_trading_day(dt.date(2026, 9, 14)) == dt.date(2026, 9, 11)
    assert c.prev_trading_day(dt.date(2026, 9, 14), n=2) == dt.date(2026, 9, 10)


def test_count_trading_days_is_exclusive_start_inclusive_end(tmp_path):
    # 유예 카운터가 쓰는 정의(A04) — 금요일 이탈 뒤 화요일이면 9/21·9/22 두 세션이다.
    c = cal.load(_write(tmp_path, ["20260924", "20260925"]))
    assert c.count_trading_days(dt.date(2026, 9, 18), dt.date(2026, 9, 22)) == 2
    assert c.count_trading_days(dt.date(2026, 9, 18), dt.date(2026, 9, 20)) == 0   # 주말만 지났다
    assert c.count_trading_days(dt.date(2026, 9, 23), dt.date(2026, 9, 28)) == 1   # 추석 9/24·9/25 휴장
    assert c.count_trading_days(dt.date(2026, 9, 22), dt.date(2026, 9, 22)) == 0   # 같은 날은 0
    assert c.count_trading_days(dt.date(2026, 9, 23), dt.date(2026, 9, 22)) == 0   # 역순도 0


def test_missing_cache_assumes_business_days(tmp_path):
    c = cal.load(tmp_path / "nope.json")
    assert c.source == "weekend_only"
    assert c.is_trading_day(dt.date(2026, 9, 24)) is True       # 캐시 없으면 평일 = 영업일 가정
    assert c.is_trading_day(dt.date(2026, 9, 27)) is False


def test_invalid_cache_falls_back(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = cal.load(p)
    assert c.source == "weekend_only"
    assert c.detail  # 왜 폴백했는지 남긴다
