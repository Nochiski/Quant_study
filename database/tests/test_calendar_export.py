"""daily.calendar_export — 판정 달력을 v3·uni 휴장 파일(`.kis_holidays.json`) 형식으로 내보낸다(T-24, QL-Q).

형식 정본은 v3 `backend/clients/kis/holiday.py` `fetch_year_holidays` 의 쓰기(:99-105) —
{"year": int, "fetched_at", "last_reviewed_at", "holidays": [YYYYMMDD 정렬], "review_history": []}, 단일 연도.
읽는 쪽: v3 `check_holiday`(:170-178)·`daily_pipeline._holiday_from_cache`(:60-69)는 `year`(int) = 조회 연도일 때만
`holidays` 를 쓰고, uni `unitelegram/sources/holiday.py` `_kael_is_trading_day`(:179-188)도 같다.
`check_today_business.py`(:29-36)·`news/events/holidays.py`(:20-26)·kael-wiki `lint.sh`(:25)는 `holidays` 만 읽는다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path

import pytest
from daily import calendar_export as ce
from daily import calendar_refresh as cr

# 2026 평일 휴장 17건 — v3 `.kis_holidays.json` 2026 판(121건 = 주말 104 + 평일 17)의 실제 평일 휴장
_WD_2026 = ("20260101", "20260216", "20260217", "20260218", "20260302", "20260501", "20260505",
            "20260525", "20260603", "20260717", "20260817", "20260924", "20260925", "20261005",
            "20261009", "20261225", "20261231")
_WD_2027 = ("20270101", "20270208", "20270209", "20270210", "20271231")
_V3_KEYS = {"year", "fetched_at", "last_reviewed_at", "holidays", "review_history"}
_NOW = dt.datetime(2026, 10, 10, 6, 1, 2)


def _closed(year: int, weekdays: tuple[str, ...]) -> list[str]:
    """그해 휴장 목록 = 토·일 전부 + 주어진 평일 휴장(판정 연도 파일의 `holidays`)."""
    d = dt.date(year, 1, 1)
    out: list[str] = []
    while d.year == year:
        s = d.strftime("%Y%m%d")
        if d.weekday() >= 5 or s in weekdays:
            out.append(s)
        d += dt.timedelta(days=1)
    return out


def _cal_dir(tmp_path: Path, years: dict[int, tuple[str, ...]]) -> Path:
    """판정 디렉터리 — `calendar_refresh.write_year_file` 과 같은 모양의 연도 파일들."""
    d = tmp_path / "ql" / "data" / "calendar"
    d.mkdir(parents=True)
    for year, weekdays in years.items():
        (d / f"kis_holidays_{year}.json").write_text(
            json.dumps({"year": year, "holidays": _closed(year, weekdays),
                        "updated_at": "2026-10-09T21:00:03", "source": "kis_direct"}), encoding="utf-8")
    return d


def _v3_target(tmp_path: Path) -> Path:
    """지금 v3 가 쓰는 대상 파일(내보내기 전 상태) — v3 2026 판과 같은 모양."""
    d = tmp_path / "v3" / "data"
    d.mkdir(parents=True)
    t = d / ".kis_holidays.json"
    t.write_text(json.dumps({"year": 2026, "fetched_at": "2026-06-06T19:30:31.341459",
                             "last_reviewed_at": "2026-06-06T19:30:31.341459",
                             "holidays": _closed(2026, _WD_2026), "review_history": []}), encoding="utf-8")
    return t


def _main(cal_dir: Path, target: Path, now: dt.datetime, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(ce, "_now_kst", lambda: now)
    return ce.main(["--home", str(cal_dir.parent.parent), "--target", str(target)])


def test_two_year_files_export_this_year_in_v3_format(tmp_path, monkeypatch, capsys):
    """올해·이듬해 연도 파일 → 올해 한 해만, v3 키 5개·year int·정렬된 YYYYMMDD(이듬해 섞임 없음)."""
    cal_dir = _cal_dir(tmp_path, {2026: _WD_2026, 2027: _WD_2027})
    target = _v3_target(tmp_path)

    assert _main(cal_dir, target, _NOW, monkeypatch) == 0

    out = json.loads(target.read_text(encoding="utf-8"))
    assert set(out) == _V3_KEYS
    assert out["year"] == 2026 and isinstance(out["year"], int)
    assert out["holidays"] == _closed(2026, _WD_2026)
    assert out["review_history"] == []
    assert out["fetched_at"] == out["last_reviewed_at"] == "2026-10-10T06:01:02"
    # v3 validate_year_holidays(:24-37) 규칙도 그대로 통과한다 — 100건 이상·전건 해당 연도·주말 90건 이상
    hol = out["holidays"]
    weekend = sum(1 for h in hol if dt.date(int(h[:4]), int(h[4:6]), int(h[6:])).weekday() >= 5)
    assert len(hol) == 121 and all(h.startswith("2026") for h in hol) and weekend >= 90
    # 읽는 쪽 판정: year 가 맞으므로 v3 check_holiday·uni 가 holidays 로 직접 판정한다
    assert "20261009" in hol and "20261012" not in hol
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("휴장 달력 내보내기 20261010: rc=0 year=2026 holidays=121(평일 17)")


@pytest.mark.parametrize(("now", "year"), [(dt.datetime(2026, 12, 31, 6, 0), 2026),
                                           (dt.datetime(2027, 1, 1, 6, 0), 2027)])
def test_year_is_the_kst_year_of_the_run(tmp_path, monkeypatch, now, year):
    """연도 = 실행 시각(KST)의 해 — 12-31(연말휴장)까지 올해, 1-1 부터 이듬해(v3 의 12-30 교체를 따르지 않는다)."""
    cal_dir = _cal_dir(tmp_path, {2026: _WD_2026, 2027: _WD_2027})
    target = _v3_target(tmp_path)

    assert _main(cal_dir, target, now, monkeypatch) == 0

    out = json.loads(target.read_text(encoding="utf-8"))
    assert out["year"] == year
    assert out["holidays"] == _closed(year, _WD_2026 if year == 2026 else _WD_2027)


@pytest.mark.parametrize("case", ["corrupt_next_year", "no_year_files", "this_year_missing"])
def test_unreadable_calendar_leaves_target_untouched_rc2(tmp_path, monkeypatch, capsys, case):
    """판정 달력을 못 읽거나 올해를 덮지 않으면 대상 파일을 건드리지 않고 rc 2(P1)."""
    years = {"corrupt_next_year": {2026: _WD_2026, 2027: _WD_2027}, "no_year_files": {},
             "this_year_missing": {2027: _WD_2027}}[case]
    cal_dir = _cal_dir(tmp_path, years)
    if case == "corrupt_next_year":
        (cal_dir / "kis_holidays_2027.json").write_text('{"year": 2027, "holidays": ["2027', encoding="utf-8")
    target = _v3_target(tmp_path)
    before = target.read_bytes()

    assert _main(cal_dir, target, _NOW, monkeypatch) == 2

    assert target.read_bytes() == before
    assert sorted(p.name for p in target.parent.iterdir()) == [".kis_holidays.json"]
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("휴장 달력 내보내기 20261010: rc=2") and "대상 파일 무변경" in last
    if case == "this_year_missing":
        # 운영자가 원인을 바로 알게 빠진 해와 가진 해를 적는다(KeyError 한 줄로 끝내지 않는다)
        assert "rc=2 year_not_covered: 판정 달력이 2026 년을 덮지 않는다" in last and "have=['2027']" in last
    else:
        assert "rc=2 calendar_unavailable: CalendarUnavailable" in last
        if case == "corrupt_next_year":
            assert "kis_holidays_2027.json" in last


def test_failure_mid_write_keeps_the_target_whole(tmp_path, monkeypatch):
    """쓰는 도중 실패(디스크 등) — 대상은 옛 내용 그대로, 반쪽 JSON·임시 파일이 남지 않는다."""
    cal_dir = _cal_dir(tmp_path, {2026: _WD_2026})
    target = _v3_target(tmp_path)
    before = target.read_bytes()

    def half_dump(obj, f, **kw):
        f.write(json.dumps(obj)[:40])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cr.json, "dump", half_dump)

    assert _main(cal_dir, target, _NOW, monkeypatch) == 2

    assert target.read_bytes() == before
    assert sorted(p.name for p in target.parent.iterdir()) == [".kis_holidays.json"]


def test_missing_target_directory_is_not_created(tmp_path, monkeypatch, capsys):
    """`--target` 의 디렉터리가 없으면(경로 오타) 만들지 않고 rc 2 — v3 가 읽지 않는 곳에 조용히 쓰지 않는다."""
    cal_dir = _cal_dir(tmp_path, {2026: _WD_2026})
    target = tmp_path / "v3-typo" / "data" / ".kis_holidays.json"

    assert _main(cal_dir, target, _NOW, monkeypatch) == 2

    assert not (tmp_path / "v3-typo").exists()
    assert "rc=2 target_dir_missing" in capsys.readouterr().out.strip().splitlines()[-1]


def test_existing_target_mode_is_kept(tmp_path, monkeypatch):
    """대상 파일 권한은 그대로 둔다 — 내용만 바꾼다(다른 계정 리더가 있으면 0600 이 읽기를 막는다)."""
    cal_dir = _cal_dir(tmp_path, {2026: _WD_2026})
    target = _v3_target(tmp_path)
    os.chmod(target, 0o644)

    assert _main(cal_dir, target, _NOW, monkeypatch) == 0

    assert stat.S_IMODE(target.stat().st_mode) == 0o644
