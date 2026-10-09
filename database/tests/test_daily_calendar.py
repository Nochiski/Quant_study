"""daily.calendar — 휴장 캐시 기반 거래일 판정. 플랜 P1 Task 1.1."""
import datetime as dt
import json
import re

import pytest

from daily import calendar as cal


def _write(tmp_path, holidays, year=2026):
    p = tmp_path / f"kis_holidays_{year}.json"
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


# ── 달력을 못 읽으면 중단 (K1-9 ⑦ · N-31 ② — R7 '영업일 가정' 폴백 폐지) ─────────────────
def test_missing_calendar_raises_instead_of_assuming_business_days(tmp_path):
    # 옛 동작: weekend_only 폴백 → 10-09(한글날)·추석 같은 평일 휴장을 거래일로 판정하고 체인이 돌았다.
    # 메시지는 옛 경로(nope.json)가 아니라 실제로 읽은 판정 디렉터리를 싣는다(error-messages.md)
    with pytest.raises(cal.CalendarUnavailable, match=re.escape(f"dir={tmp_path}")):
        cal.load(tmp_path / "nope.json")


def test_corrupt_calendar_raises(tmp_path):
    (tmp_path / "kis_holidays_2026.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(cal.CalendarUnavailable, match="JSONDecodeError"):
        cal.load(tmp_path)


def test_corrupt_year_file_is_named_with_its_full_path(tmp_path):
    # 정지 메시지만 보고 어느 연도 파일이 깨졌는지 알아야 한다 — 연도 파일 전체 경로가 실린다
    _write_year(tmp_path, 2026, ["20261225"])
    bad = tmp_path / "kis_holidays_2027.json"
    bad.write_text(json.dumps({"year": 2027}), encoding="utf-8")         # holidays 키 없음
    with pytest.raises(cal.CalendarUnavailable) as e:
        cal.load(tmp_path / "kis_holidays.json")
    assert f"path={bad}" in str(e.value) and "KeyError" in str(e.value)
    assert f"dir={tmp_path}" in str(e.value)


def test_year_in_file_name_must_match_the_content(tmp_path):
    (tmp_path / "kis_holidays_2027.json").write_text(
        json.dumps({"year": 2026, "holidays": ["20261225"]}), encoding="utf-8")
    with pytest.raises(cal.CalendarUnavailable, match="2027"):
        cal.load(tmp_path)


# ── 연 경계 (DEFECT-A07) ────────────────────────────────────────────────────
def _write_year(tmp_path, year, holidays):
    p = tmp_path / f"kis_holidays_{year}.json"
    p.write_text(json.dumps({"year": year, "holidays": holidays}), encoding="utf-8")
    return p


def test_load_merges_every_year_file_in_the_directory(tmp_path):
    # v3 원천은 단일 연도 파일이라 12월에 2027 판으로 교체되면 2026 성탄절·연말이 사라졌다.
    _write_year(tmp_path, 2026, ["20261225", "20261231"])
    _write_year(tmp_path, 2027, ["20270101"])
    c = cal.load(tmp_path / "kis_holidays.json")        # 옛 경로를 줘도 디렉터리를 합쳐 읽는다
    assert c.source == "kis_cache" and c.years == frozenset({"2026", "2027"})
    assert c.is_trading_day(dt.date(2026, 12, 31)) is False
    assert c.is_trading_day(dt.date(2027, 1, 1)) is False
    assert c.prev_trading_day(dt.date(2027, 1, 4)) == dt.date(2026, 12, 30)


def test_single_file_cache_still_works(tmp_path):
    c = cal.load(_write(tmp_path, ["20260924", "20260925"]))
    assert c.source == "kis_cache" and c.years == frozenset({"2026"})
    assert c.is_trading_day(dt.date(2026, 9, 24)) is False


def test_year_without_a_cache_file_raises_instead_of_assuming_weekdays(tmp_path):
    # 폴백으로 넘어가면 신정·설 연휴가 통째로 거래일이 된다 — 조용히 틀리느니 체인을 세운다.
    c = cal.load(_write(tmp_path, ["20261225"]))
    with pytest.raises(KeyError):
        c.is_trading_day(dt.date(2027, 1, 4))
    with pytest.raises(KeyError):
        c.prev_trading_day(dt.date(2027, 1, 4))


def test_legacy_unsuffixed_file_is_not_read(tmp_path):
    # 옛 경로 `kis_holidays.json` 은 v3 사본 호환용으로 남는다. 판정에 섞으면(합집합) 직접 갱신이
    # 지운 휴장일(지정 취소)이 옛 사본으로 되살아난다 — 판정은 연도 파일만 읽는다.
    _write_year(tmp_path, 2026, ["20261225"])
    (tmp_path / "kis_holidays.json").write_text(
        json.dumps({"year": 2026, "holidays": ["20261225", "20261020"]}), encoding="utf-8")
    c = cal.load(tmp_path / "kis_holidays.json")
    assert c.is_trading_day(dt.date(2026, 10, 20)) is True
    assert c.is_trading_day(dt.date(2026, 12, 25)) is False
