"""DART 일일 증분 러너 — 플랜 Task 1.5 (DEFECT-B01~B04).

픽스처는 소형 sqlite `dart.db` 하나. 하위 도구(subprocess)는 monkeypatch 로 갈아끼워
**콜을 하지 않고 인자만 기록**한다. 표본 `report_nm` 20건은 findings B §7-2·§2-1 의 LIKE
조건과 `rules_s11.PERIODIC_PREFIXES`/`EXCLUDED_TOKENS`/`CORRECTION_PREFIXES` 어휘를 근거로 골랐다.
"""
from __future__ import annotations

import datetime as dt
import importlib
import sqlite3
import sys

import pytest

from daily import dart_daily as dd
from daily import runlog
from daily.dart_daily import Kind

C1, C2, C3 = "00126380", "00164779", "00919966"      # 8자리 corp_code (백필 형식 검증 통과)

# ── 표본 report_nm 20건 ─────────────────────────────────────────────────────────
# (report_nm, kind, is_correction, bsns_year, reprt_code)
SAMPLES: tuple[tuple[str, Kind, bool, str | None, str | None], ...] = (
    ("사업보고서 (2025.12)", Kind.PERIODIC, False, "2025", "11011"),
    ("반기보고서 (2026.06)", Kind.PERIODIC, False, "2026", "11012"),
    ("분기보고서 (2026.03)", Kind.PERIODIC, False, "2026", "11013"),
    ("분기보고서 (2026.09)", Kind.PERIODIC, False, "2026", "11014"),
    ("[기재정정]사업보고서 (2024.12)", Kind.PERIODIC, True, "2024", "11011"),
    ("[첨부정정]반기보고서 (2026.06)", Kind.PERIODIC, True, "2026", "11012"),
    # `[첨부추가]` 는 원본 라벨이라 정정이 아니다(rules_s11 CORRECTION_PREFIXES 규약).
    ("[첨부추가]분기보고서 (2026.03)", Kind.PERIODIC, False, "2026", "11013"),
    ("[기재정정][첨부추가]분기보고서 (2025.09)", Kind.PERIODIC, True, "2025", "11014"),
    # 우측 공백 패딩 실측(DART_CENSUS §12) — rstrip 없이는 라벨 뒤가 안 잘린다
    ("사업보고서 (2023.12)          ", Kind.PERIODIC, False, "2023", "11011"),
    ("사업보고서제출기한연장신고서 (2022.12)", Kind.OTHER, False, None, None),
    ("주요사항보고서(자기주식취득결정)", Kind.MAJOR, False, None, None),
    ("주요사항보고서(유상증자결정)", Kind.MAJOR, False, None, None),
    ("주요사항보고서(주식분할결정)", Kind.MAJOR, False, None, None),
    ("[기재정정]주요사항보고서(회사분할결정)", Kind.MAJOR, True, None, None),
    ("주식등의대량보유상황보고서(약식)", Kind.HOLDER, False, None, None),
    ("주식등의대량보유상황보고서(일반)", Kind.HOLDER, False, None, None),
    ("임원ㆍ주요주주특정증권등소유상황보고서", Kind.HOLDER, False, None, None),
    ("[기재정정]임원ㆍ주요주주특정증권등소유상황보고서", Kind.HOLDER, True, None, None),
    ("기타시장안내(정리매매 개시)", Kind.OTHER, False, None, None),
    ("[기재정정]주권매매거래정지(투자자보호)", Kind.CORRECTION, True, None, None),
)


@pytest.mark.parametrize(("report_nm", "kind", "is_corr", "year", "reprt"), SAMPLES)
def test_classify_samples(report_nm: str, kind: Kind, is_corr: bool,
                          year: str | None, reprt: str | None) -> None:
    c = dd.classify(report_nm)
    assert (c.kind, c.is_correction, c.bsns_year, c.reprt_code) == (kind, is_corr, year, reprt)


def test_classify_uses_report_name_not_label_month_for_non_december_fiscal_year() -> None:
    """3월 결산 법인의 `사업보고서 (2026.03)`. 라벨 월만 보면 11013(1분기)로 둔갑한다."""
    c = dd.classify("사업보고서 (2026.03)")
    assert (c.kind, c.reprt_code) == (Kind.PERIODIC, "11011")
    c = dd.classify("반기보고서 (2025.09)")
    assert (c.kind, c.reprt_code) == (Kind.PERIODIC, "11012")


def test_classify_defers_undecidable_quarter_instead_of_guessing() -> None:
    """12월 결산이 아닌 법인의 분기보고서는 Q1/Q3 를 acc_mt 없이 못 가른다 — 보류하고 남긴다."""
    c = dd.classify("분기보고서 (2025.06)")
    assert c.kind is Kind.PERIODIC and c.reprt_code is None and not c.resolved
    assert "acc_mt" in c.detail and "2025.06" in c.detail


def test_classify_periodic_without_label_is_unresolved() -> None:
    c = dd.classify("분기보고서")
    assert c.kind is Kind.PERIODIC and not c.resolved and "period label" in c.detail


def test_endpoint_names_match_backfill_dart_stages(tmp_path, monkeypatch) -> None:
    """SoT 드리프트 대조 — 엔드포인트 이름·축은 `backfill_dart.py` STAGES·SPEC 가 정본이다."""
    (tmp_path / ".env").write_text(
        "KRX_API_KEY=k\nKRX_ID=i\nKRX_PW=p\nDART_API_KEY_2=x\nDART_API_KEY_3=y\n",
        encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(tmp_path / ".env"))
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    for m in ("api", "backfill_dart"):
        sys.modules.pop(m, None)
    bf = importlib.import_module("backfill_dart")

    corp_year_stage3 = tuple(n for n in bf.STAGES[3] if bf.SPEC[n]["axis"] == "corp_year")
    corp_stage3 = tuple(n for n in bf.STAGES[3] if bf.SPEC[n]["axis"] == "corp")
    assert dd.PERIODIC_ENDPOINTS == tuple(bf.STAGES[2]) + corp_year_stage3
    assert dd.HOLDER_ENDPOINTS == corp_stage3
    assert dd.DS005_ENDPOINTS == tuple(bf.STAGES[4]) + tuple(bf.STAGES[5])
    assert len(dd.DS005_ENDPOINTS) == 15
    assert {bf.SPEC[n]["axis"] for n in dd.DS005_ENDPOINTS} == {"corp_range"}
    # 재무 재확인은 이 표 이름으로 저장된 재무 행을 읽는다(`fin_rcept_max`). 이름이 바뀌면
    # 받은 재무가 매 런 '행 없음' 으로 보여 전부 다시 불린다
    assert bf.SPEC["fin"]["tbl"] == "dart_fin_raw"


# ── 픽스처 ──────────────────────────────────────────────────────────────────────
_DDL = (
    ("CREATE TABLE dart_disclosure (row_hash TEXT PRIMARY KEY, rcept_no TEXT, rcept_dt TEXT, "
     "corp_code TEXT, stock_code TEXT, report_nm TEXT, collected_at TEXT NOT NULL)"),
    ("CREATE TABLE ingest_log (name TEXT NOT NULL, corp_code TEXT NOT NULL, "
     "bsns_year TEXT NOT NULL DEFAULT '', reprt_code TEXT NOT NULL DEFAULT '', "
     "fs_div TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, n_rows INTEGER NOT NULL, "
     "note TEXT, ts TEXT NOT NULL, PRIMARY KEY (name, corp_code, bsns_year, reprt_code, fs_div))"),
    ("CREATE TABLE dart_call_log (endpoint TEXT NOT NULL, corp_code TEXT, bsns_year TEXT, "
     "reprt_code TEXT, fs_div TEXT, status TEXT NOT NULL, n_rows INTEGER NOT NULL, "
     "ts TEXT NOT NULL, key_id TEXT NOT NULL DEFAULT 'kael')"),
    ("CREATE TABLE doc_store (rcept_no TEXT PRIMARY KEY, bytes INTEGER NOT NULL, "
     "sha256 TEXT NOT NULL, n_files INTEGER, zip_ok INTEGER NOT NULL, http_status TEXT, "
     "fetched_at TEXT NOT NULL)"),
)
D = "20260908"
# rows 에 `collected_at` 을 안 주면 이 값이 붙는다 — D 당일 수집분이라 어느 런의 스윕 시작
# 시각보다도 과거다(실제 시계는 D 이후). 그래서 "늦게 등장" 판정에 걸리지 않는다.
_SEEN_BEFORE = "2026-09-08T12:00:00"


def _corp_for(i: int) -> str:
    return (C1, C2, C3)[i % 3]


def _make_home(tmp_path, *, rows=None):
    """표본 20건이 든 dart.db 를 tmp home 에 만든다. rows 를 주면 그것으로 대체한다.

    rows 한 건은 `(rcept_no, rcept_dt, corp_code, stock_code, report_nm[, collected_at])` 다.
    """
    db = tmp_path / "data" / "raw" / "dart.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    for ddl in _DDL:
        con.execute(ddl)
    if rows is None:
        rows = [(f"2026090800{i:04d}", D, _corp_for(i), "005930", nm)
                for i, (nm, *_rest) in enumerate(SAMPLES)]
    con.executemany(
        "INSERT INTO dart_disclosure VALUES (?,?,?,?,?,?,?)",
        [(f"h{i}", *r, *((_SEEN_BEFORE,) if len(r) == 5 else ()))
         for i, r in enumerate(rows)])
    con.commit()
    con.close()
    return str(tmp_path)


def _con(home) -> sqlite3.Connection:
    return sqlite3.connect(f"{home}/data/raw/dart.db")


def _calls(con, endpoint, corp, ts, *, key_id="k2", year="", reprt="", status="000"):
    con.execute("INSERT INTO dart_call_log VALUES (?,?,?,?,'', ?, 1, ?, ?)",
                (endpoint, corp, year, reprt, status, ts, key_id))


# ── plan ────────────────────────────────────────────────────────────────────────
def test_plan_builds_units_per_kind(tmp_path) -> None:
    home = _make_home(tmp_path)
    con = _con(home)
    p = dd.plan(con, D)

    # 표본의 corp 는 3개가 돌아가며 붙는다 — 유닛은 (endpoint, corp, year, reprt) 로 중복 제거된다
    periodic_eps = {u.endpoint for u in p.units if u.bsns_year}
    assert periodic_eps == set(dd.PERIODIC_ENDPOINTS) and len(periodic_eps) == 7
    # 정기보고서 유닛은 전부 그 공시의 (bsns_year, reprt_code) 를 쓴다 — 11011 고정이 아니다(B02)
    assert {(u.bsns_year, u.reprt_code) for u in p.units if u.bsns_year} == {
        ("2025", "11011"), ("2026", "11012"), ("2026", "11013"), ("2026", "11014"),
        ("2024", "11011"), ("2025", "11014"), ("2023", "11011")}
    # 주요사항 corp 은 DS005 15종 전부(B04 — stage 5 포함)
    for corp in p.major_corps:
        got = {u.endpoint for u in p.units if u.corp_code == corp and not u.bsns_year}
        assert set(dd.DS005_ENDPOINTS) <= got
    # 지분공시 corp 은 elestock·majorstock 2종
    assert set(dd.HOLDER_ENDPOINTS) <= {u.endpoint for u in p.units
                                        for c in p.holder_corps if u.corp_code == c}
    assert p.kind_counts["periodic"] == 9 and p.kind_counts["major"] == 4
    assert p.kind_counts["holder"] == 4 and p.kind_counts["correction"] == 1
    assert p.kind_counts["other"] == 2
    con.close()


def test_plan_deduplicates_repeated_disclosures(tmp_path) -> None:
    rows = [("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
            ("20260908000002", D, C1, "005930", "[기재정정]반기보고서 (2026.06)"),
            ("20260908000003", D, C1, "005930", "주요사항보고서(유상증자결정)"),
            ("20260908000004", D, C1, "005930", "주요사항보고서(자기주식취득결정)"),
            ("20260908000005", D, C1, "005930", "주식등의대량보유상황보고서(약식)"),
            ("20260908000006", D, C1, "005930", "임원ㆍ주요주주특정증권등소유상황보고서")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    p = dd.plan(con, D)
    # 정정본은 원 유형과 같은 유닛 → 7 + DS005 15 + 지분 2 = 24
    assert len(p.units) == 7 + 15 + 2
    assert p.periodic_units == ((C1, "2026", "11012"),)
    assert p.major_corps == (C1,) and p.holder_corps == (C1,)
    assert dd.calls_est(p.units) == 24 + 1               # fin 은 CFS→OFS 폴백으로 최대 2콜
    con.close()


def test_plan_skips_unlisted_and_records_unresolved(tmp_path) -> None:
    rows = [("20260908000001", D, C1, "", "반기보고서 (2026.06)"),          # 비상장 → 제외
            ("20260908000002", D, C2, "005930", "분기보고서 (2025.06)"),    # 라벨 월 미해석
            ("20260908000003", D, "123", "005930", "주요사항보고서(합병결정)")]  # corp_code 형식 오류
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    p = dd.plan(con, D)
    assert p.units == () and p.n_filings == 2
    assert [r[0] for r in p.unresolved] == ["20260908000002"]
    assert p.bad_corp_codes == (("20260908000003", "123"),)
    con.close()


def test_plan_resolves_non_december_quarterly_label_with_company_fiscal_month(tmp_path) -> None:
    """09-23 실측: 테라뷰(결산월 04)의 '분기보고서 (2026.07)' — 라벨 월 07 − 결산월 04 = 3 → 1분기(11013),
    bsns_year 는 라벨 연도. 결산월을 모르는 법인(dart_company 에 없음)은 종전대로 unresolved 로 남긴다."""
    rows = [("20260923000617", D, C1, "950250", "분기보고서 (2026.07)"),     # acc_mt 04 → Q1
            ("20260923000618", D, C2, "005930", "분기보고서 (2026.01)"),     # acc_mt 04 → 01−04 = 9 → Q3
            ("20260923000619", D, C3, "000660", "분기보고서 (2026.05)")]     # dart_company 에 없음 → 보류
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    con.execute("CREATE TABLE dart_company (corp_code TEXT PRIMARY KEY, corp_name TEXT, acc_mt TEXT)")
    con.executemany("INSERT INTO dart_company VALUES (?,?,?)", [(C1, "테라뷰홀딩스", "04"), (C2, "x", "04")])
    con.commit()
    p = dd.plan(con, D)
    assert set(p.periodic_units) == {(C1, "2026", "11013"), (C2, "2026", "11014")}
    assert [r[0] for r in p.unresolved] == ["20260923000619"]
    # 6월 결산: 1분기 라벨이 09, 3분기 라벨이 03 — 12월 기준 월 규칙(09→3분기)을 결산월로 덮어쓴다(09-24 검수)
    rows_jun = [("20261114000001", "20261114", C1, "005930", "분기보고서 (2026.09)"),
                ("20260514000002", "20260514", C1, "005930", "분기보고서 (2026.03)"),
                ("20260514000003", "20260514", C3, "000660", "분기보고서 (2026.03)")]      # C3 는 12월 결산
    con.execute("DELETE FROM dart_disclosure"); con.execute("DELETE FROM dart_company")
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?,?,?,?)",
                    [(f"j{i}", *r, "2026-11-14T12:00:00") for i, r in enumerate(rows_jun)])
    con.executemany("INSERT INTO dart_company VALUES (?,?,?)", [(C1, "june", "06"), (C3, "dec", "12")])
    con.commit()
    p1 = dd.plan(con, "20261114")
    assert p1.periodic_units == ((C1, "2026", "11013"),)                     # 09 → 1분기(6월 결산)
    p3 = dd.plan(con, "20260514")
    assert set(p3.periodic_units) == {(C1, "2026", "11014"), (C3, "2026", "11013")}   # 03 → 3분기 / 12월 결산은 1분기
    c = dd.resolve_with_acc_mt(dd.classify("분기보고서 (2026.07)"), "04")
    assert c is not None and c.reprt_code == "11013" and c.bsns_year == "2026" and "acc_mt=04" in c.detail
    assert dd.resolve_with_acc_mt(dd.classify("분기보고서 (2026.07)"), "12") is None      # 07−12 = 7 → 판정 불가
    assert dd.resolve_with_acc_mt(dd.classify("분기보고서 (2026.07)"), None) is None
    con.close()


def test_plan_includes_late_arriving_filings_first_seen_in_this_sweep(tmp_path) -> None:
    """접수일이 D 가 아니어도 **이번 스윕에서 처음 본** 공시는 상세 축을 따라가야 한다.

    09-10 스윕에서 08-25 접수 8건이 처음 등장했다(검수 D H3). 종전 `rcept_dt = D` 필터로는
    그런 공시가 영영 상세를 못 받는다.
    """
    since = "2026-09-11T09:00:00"
    rows = [("20260908000001", D, C1, "005930", "반기보고서 (2026.06)", "2026-09-08T23:00:00"),
            # D−15일 접수인데 이번 스윕(since 이후)에 처음 나타났다 → 대상
            ("20260824000001", "20260824", C2, "005930", "주식등의대량보유상황보고서(약식)",
             "2026-09-11T09:10:00"),
            # 같은 D−15일 접수지만 어제 이미 봤다 → 대상 아님
            ("20260824000002", "20260824", C3, "005930", "주식등의대량보유상황보고서(일반)",
             "2026-09-10T09:10:00")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)

    p = dd.plan(con, D, since_ts=since)
    assert (p.n_late, p.late_rcept_nos) == (1, ("20260824000001",))
    assert p.holder_corps == (C2,)                       # C3 는 늦게 등장한 게 아니다
    assert p.periodic_units == ((C1, "2026", "11012"),)  # (a) 는 종전 그대로
    assert {u.corp_code for u in p.units} == {C1, C2}
    assert p.n_filings == 1 and p.n_rows == 1            # (a) 기준 — 게이트가 쓰는 값

    p0 = dd.plan(con, D)                                 # since_ts 없으면 종전 동작
    assert (p0.n_late, p0.holder_corps, p0.late_rcept_nos) == (0, (), ())
    con.close()


def test_plan_ignores_old_filings_restored_by_a_page_shift(tmp_path) -> None:
    """재스윕은 이미 아는 공시를 다른 `req_page_no` 로 한 번 더 적재한다.

    요청 파라미터가 행 해시에 들어가서(`sweep_disclosure.py:158-165` 주석) 그 행의
    `collected_at` 은 오늘이지만 **공시 자체는 처음 본 게 아니다**. 행이 아니라 공시
    단위 최초 관측 시각(`MIN(collected_at)`)으로 판정해야 한다.
    """
    since = "2026-09-11T09:00:00"
    nm = "주식등의대량보유상황보고서(약식)"
    rows = [("20260824000001", "20260824", C2, "005930", nm, "2026-09-01T09:00:00"),
            ("20260824000001", "20260824", C2, "005930", nm, "2026-09-11T09:10:00")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    p = dd.plan(con, D, since_ts=since)
    assert (p.n_late, p.units) == (0, ())
    con.close()


def test_plan_late_sample_is_capped(tmp_path) -> None:
    """로그용 표본은 최대 `LATE_SAMPLE_MAX` 건. 수는 `n_late` 가 온전히 센다."""
    since = "2026-09-11T09:00:00"
    rows = [(f"202608240{i:05d}", "20260824", C1, "005930",
             "주식등의대량보유상황보고서(약식)", "2026-09-11T09:10:00")
            for i in range(dd.LATE_SAMPLE_MAX + 5)]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    p = dd.plan(con, D, since_ts=since)
    assert p.n_late == dd.LATE_SAMPLE_MAX + 5
    assert len(p.late_rcept_nos) == dd.LATE_SAMPLE_MAX
    con.close()


def test_plan_trigger_takes_max_first_seen_and_max_rcept_no(tmp_path) -> None:
    """같은 유닛을 만든 공시가 여럿이면 최초 관측·접수번호 둘 다 최댓값이다.

    저녁 스윕이 처음 본 늦은 공시(b, 09:10)와 D일 정정본(a, 12:00)이 같은 반기 재무 유닛을 만든다.
    plan 은 (a) 다음 (b) 순서로 돌므로 '마지막 값' 이나 최솟값으로 모으면 기준이 어긋난다.
    """
    rows = [("20260908000002", D, C1, "005930", "[기재정정]반기보고서 (2026.06)",
             "2026-09-08T12:00:00"),
            ("20260905000001", "20260905", C1, "005930", "반기보고서 (2026.06)",
             "2026-09-08T09:10:00")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    p = dd.plan(con, D, since_ts="2026-09-08T09:05:00")
    assert p.n_late == 1
    t = p.triggers[dd.Unit("fin", C1, "2026", "11012")]
    assert (t.first_seen, t.rcept_no) == ("2026-09-08T12:00:00", "20260908000002")
    con.close()


def test_group_units_folds_endpoints_sharing_the_same_corp_set() -> None:
    units = [dd.Unit(ep, C1, "2026", "11012") for ep in dd.PERIODIC_ENDPOINTS]
    units += [dd.Unit(ep, C2, "", "") for ep in dd.DS005_ENDPOINTS]
    units += [dd.Unit(ep, C3, "", "") for ep in dd.HOLDER_ENDPOINTS]
    groups = dd.group_units(units)
    # (2026,11012) 1그룹 + corp 축은 corp 집합이 달라 DS005/지분 2그룹
    assert len(groups) == 3
    # corp_year 축이 먼저 — 마감일에 한도가 걸려도 재무가 밀리지 않게
    assert groups[0] == ("2026", "11012", dd.PERIODIC_ENDPOINTS, (C1,))
    # `--only` 순서는 선언순(fin 먼저) — 알파벳순이면 audit 이 앞선다
    assert [eps for y, _r, eps, _c in groups if not y] == [dd.DS005_ENDPOINTS,
                                                           dd.HOLDER_ENDPOINTS]


# ── unlock ──────────────────────────────────────────────────────────────────────
def test_unlock_deletes_by_axis_key(tmp_path) -> None:
    home = _make_home(tmp_path)
    con = _con(home)
    ts = "2026-09-01T00:00:00"
    con.executemany("INSERT INTO ingest_log VALUES (?,?,?,?,?,?,?,NULL,?)", [
        # corp_year 축 — fin 은 fs_div 가 CFS/OFS 두 행으로 갈린다. 둘 다 지워야 되살아난다
        ("fin", C1, "2026", "11012", "CFS", "ok", 100, ts),
        ("fin", C1, "2026", "11012", "OFS", "no_data", 0, ts),
        ("fin", C1, "2025", "11011", "CFS", "ok", 100, ts),        # 다른 기수 — 남아야 한다
        ("dividend", C1, "2026", "11012", "", "ok", 3, ts),
        ("dividend", C1, "2026", "11011", "", "ok", 3, ts),        # 다른 reprt — 남아야 한다
        # corp / corp_range 축 — bsns_year·reprt_code 가 빈 문자열이다
        ("elestock", C2, "", "", "", "ok", 12, ts),
        ("fricDecsn", C2, "", "", "", "ok", 1, ts),
        ("fricDecsn", C3, "", "", "", "ok", 1, ts),                # 다른 corp — 남아야 한다
    ])
    con.commit()

    n = dd.unlock(con, [dd.Unit("fin", C1, "2026", "11012"),
                        dd.Unit("dividend", C1, "2026", "11012"),
                        dd.Unit("elestock", C2, "", ""),
                        dd.Unit("fricDecsn", C2, "", "")])
    assert n == 5                                                   # fin 2행 + 나머지 3행
    left = sorted(con.execute("SELECT name, corp_code, bsns_year, reprt_code FROM ingest_log"))
    assert left == [("dividend", C1, "2026", "11011"), ("fin", C1, "2025", "11011"),
                    ("fricDecsn", C3, "", "")]
    con.close()


# ── 완료 판정 — '받았다' (배포 묶음 3 T12 · A-05 · N-24 3.1~3.5) ─────────────────────
# 백필이 남기는 유닛 결과의 시각. 표본 공시의 최초 관측(_SEEN_BEFORE) 뒤 — 공시를 보고 받은 결과.
_LOGGED = "2026-09-08T13:00:00"
# 재무 상세 콜의 `dart_call_log.endpoint`(SPEC 의 ep). 판정 근거가 아니다 — 콜 기록이 남아 있어도
# 판정이 갈린다는 것을 보이는 데만 쓴다(옛 판정은 이 기록으로 통과했다).
_FIN_EP = "fnlttSinglAcntAll.json"


def _busy_day_rows(n_extra: int) -> list[tuple[str, str, str, str, str]]:
    rows = [(f"2026090810{i:04d}", D, C1, "005930", "기타시장안내") for i in range(n_extra)]
    rows.append(("20260908000001", D, C2, "005930", "반기보고서 (2026.06)"))
    rows.append(("20260908000002", D, C3, "005930", "주요사항보고서(유상증자결정)"))
    return rows


def _log(con, units, *, status="ok", ts=_LOGGED):
    """유닛 최종 결과 1행(`backfill_dart.py:742-745` 와 같은 열 순서). fin 은 CFS 행으로 남는다."""
    con.executemany("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,1,NULL,?)",
                    [(u.endpoint, u.corp_code, u.bsns_year, u.reprt_code,
                      "CFS" if u.endpoint == "fin" else "", status, ts) for u in units])


def _periodic(corp, year="2026", reprt="11012"):
    return [dd.Unit(ep, corp, year, reprt) for ep in dd.PERIODIC_ENDPOINTS]


def _per_corp(endpoints, corp):
    return [dd.Unit(ep, corp, "", "") for ep in endpoints]


def _gates(con, p, **kw):
    return {g.name: g for g in dd.check(con, p, **kw)}


def _failed(gates) -> set[str]:
    return {name for name, g in gates.items() if not g.ok}


def _doc_ok(con, rcept_no):
    con.execute("INSERT INTO doc_store VALUES (?,1,'x',1,1,'000','t')", (rcept_no,))


def test_gates_pass_on_units_received_after_first_seen_without_calls_this_run(tmp_path) -> None:
    """G1 ⑥ — 최초 관측 뒤에 받은 ok 가 있으면 이번 런에 콜이 없어도 통과한다.

    T13 의 전제다: 같은 D 의 두 번째 런은 받은 유닛을 다시 부르지 않는다. 옛 판정은 '이번 런
    시작 뒤 콜' 을 요구해서 그런 런은 매일 실패했을 것이다.
    """
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    _log(con, [*_periodic(C2), *_per_corp(dd.DS005_ENDPOINTS, C3)])
    _doc_ok(con, "20260908000001")
    con.commit()
    gates = _gates(con, dd.plan(con, D))
    assert _failed(gates) == set(), {k: v.detail for k, v in gates.items()}
    assert gates["filings"].metrics == {"n_filings": 502, "n_listed": 502}
    assert gates["periodic_followed"].metrics == {"n_units": 7, "n_received": 7, "n_unresolved": 0}
    assert gates["major_followed"].metrics == {"n_units": 15, "n_received": 15}
    assert gates["holder_followed"].metrics == {"n_units": 0, "n_received": 0}
    assert gates["documents"].metrics == {"n_target": 1, "n_ok": 1, "n_never_tried": 0,
                                          "n_failed_other": 0}
    con.close()


def test_gates_fail_on_thin_ledger_and_missing_recalls(tmp_path) -> None:
    home = _make_home(tmp_path, rows=_busy_day_rows(10))          # 12건 < 하한 400
    con = _con(home)
    p = dd.plan(con, D)
    gates = _gates(con, p)
    assert not gates["filings"].ok and gates["filings"].metrics["n_filings"] == 12
    assert not gates["periodic_followed"].ok      # 정기 7종 미수신
    assert not gates["major_followed"].ok         # DS005 15종 미수신
    assert not gates["documents"].ok              # doc_store 에 행 없음 → n_never_tried=1
    assert gates["documents"].metrics["n_never_tried"] == 1
    con.close()


def test_filings_gate_allows_zero_on_non_trading_day(tmp_path) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", "20260907", C1, "005930", "기타")])
    con = _con(home)
    p = dd.plan(con, D)
    gates = _gates(con, p, trading_day=False)
    assert gates["filings"].ok and gates["filings"].metrics["n_filings"] == 0
    con.close()


def test_docs_gate_allows_014_but_not_other_failures(tmp_path) -> None:
    rows = [("20260908000001", D, C1, "005930", "[기재정정]사업보고서 (2025.12)"),
            ("20260908000002", D, C2, "005930", "반기보고서 (2026.06)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    con.execute("INSERT INTO doc_store VALUES ('20260908000001',0,'x',0,0,'014','t')")
    con.execute("INSERT INTO doc_store VALUES ('20260908000002',0,'x',0,0,'900','t')")
    con.commit()
    p = dd.plan(con, D)
    g = _gates(con, p)["documents"]
    assert not g.ok and g.metrics == {"n_target": 2, "n_ok": 0, "n_never_tried": 0,
                                      "n_failed_other": 1}
    con.execute("UPDATE doc_store SET http_status='014' WHERE rcept_no='20260908000002'")
    con.commit()
    g = _gates(con, p)["documents"]
    assert g.ok
    con.close()


def test_periodic_gate_rejects_ok_logged_before_first_seen(tmp_path) -> None:
    """어제 ok 였던 유닛이 오늘 공시를 반영하지 않은 채 통과하면 안 된다(DEFECT-B01 의 우려).

    옛 규칙은 그래서 `ingest_log.status` 를 아예 읽지 않고 이번 런의 콜 기록(`dart_call_log`)만
    봤다. 그런데 콜 기록은 실패·한도(020) 콜도 남고(G1 ①·⑤), 같은 D 의 두 번째 런은 부르지 않는다
    (T13). 새 규칙(N-24 3.1)은 status 를 읽되 `ts ≥ 계기 공시 최초 관측` 인 결과만 받는다 —
    09-01 의 ok 는 09-08 에 처음 본 공시를 반영했을 수 없다.
    """
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    _log(con, _periodic(C2), ts="2026-09-01T00:00:00")          # 최초 관측(09-08 12:00) 전의 ok
    con.commit()
    g = _gates(con, dd.plan(con, D))["periodic_followed"]
    assert not g.ok and g.metrics["n_received"] == 0
    assert "최초 관측 2026-09-08T12:00:00" in g.detail
    con.close()


def test_periodic_gate_fails_when_the_fin_call_failed(tmp_path) -> None:
    """G1 ① — 재무 콜이 실패해 `ingest_log` 가 error 인데 옛 판정은 콜 기록만 봐서 통과했다."""
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    _log(con, _periodic(C2)[1:])                                  # 부속 6종은 받았다
    _log(con, _periodic(C2)[:1], status="error")                  # fin 은 실패
    _log(con, _per_corp(dd.DS005_ENDPOINTS, C3))
    _calls(con, _FIN_EP, C2, _LOGGED, year="2026", reprt="11012", status="800")
    _calls(con, "fricDecsn.json", C3, _LOGGED)
    _doc_ok(con, "20260908000001")
    con.commit()
    g = _gates(con, dd.plan(con, D))
    assert _failed(g) == {"periodic_followed"}
    assert "status=error" in g["periodic_followed"].detail
    con.close()


def test_major_gate_needs_all_fifteen_ds005_endpoints(tmp_path) -> None:
    """G1 ② — DS005 15종 중 1종만 받아도 '아무 1건' 규칙으로 통과했다(N-24 3.4: 15종 전부)."""
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    _log(con, _periodic(C2))
    _log(con, _per_corp(("fricDecsn",), C3))
    _calls(con, _FIN_EP, C2, _LOGGED, year="2026", reprt="11012")
    _calls(con, "fricDecsn.json", C3, _LOGGED)
    _doc_ok(con, "20260908000001")
    con.commit()
    g = _gates(con, dd.plan(con, D))
    assert _failed(g) == {"major_followed"}
    assert g["major_followed"].metrics == {"n_units": 15, "n_received": 1}
    con.close()


def test_holder_gate_fails_without_elestock_and_majorstock(tmp_path) -> None:
    """G1 ③ — 지분공시(대량보유·임원소유)가 만든 elestock·majorstock 유닛은 판정이 아예 없었다."""
    rows = [*_busy_day_rows(500)[:-2],                            # 정기·주요 2건을 빼고
            ("20260908000003", D, C1, "005930", "주식등의대량보유상황보고서(약식)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    g = _gates(con, dd.plan(con, D))
    assert _failed(g) == {"holder_followed"}
    assert g["holder_followed"].metrics == {"n_units": 2, "n_received": 0}
    con.close()


def test_periodic_gate_checks_all_seven_endpoints_not_only_fin(tmp_path) -> None:
    """G1 ④ — 재무는 받고 감사의견(audit)은 실패. 옛 판정은 재무 콜만 봤다."""
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    assert dd.PERIODIC_ENDPOINTS[-1] == "audit"
    _log(con, _periodic(C2)[:-1])
    _log(con, _periodic(C2)[-1:], status="error")
    _log(con, _per_corp(dd.DS005_ENDPOINTS, C3))
    _calls(con, _FIN_EP, C2, _LOGGED, year="2026", reprt="11012")
    _calls(con, "fricDecsn.json", C3, _LOGGED)
    _doc_ok(con, "20260908000001")
    con.commit()
    g = _gates(con, dd.plan(con, D))
    assert _failed(g) == {"periodic_followed"}
    assert "audit" in g["periodic_followed"].detail
    con.close()


def test_filings_gate_counts_listed_filings_once_not_resweep_rows(tmp_path) -> None:
    """N-24 3.5 — 재스윕은 같은 공시를 다른 `req_page_no` 로 한 번 더 적재한다.

    09-30 실측 행 1,262 · 고유 725. 상장사 공시 고유 200건이 두 벌씩 적재된 날: 행으로 세면
    400(하한 290 통과), 공시로 세면 200(실패).
    """
    listed = [(f"20260908{i:06d}", D, C1, "005930", "기타시장안내") for i in range(200)]
    unlisted = [(f"20260908{i:06d}", D, C1, "", "기타시장안내") for i in range(200, 450)]
    home = _make_home(tmp_path, rows=[*listed, *listed, *unlisted])
    con = _con(home)
    g = _gates(con, dd.plan(con, D))["filings"]
    assert not g.ok
    assert g.metrics == {"n_filings": 450, "n_listed": 200}
    con.close()


def test_received_rules_axis_status_fs_div_and_latest_trigger(tmp_path) -> None:
    """N-24 3.1~3.3 경계(3.2 는 10-07 수정) — no_data 는 모든 축에서 받음(다시 부르는 013 은
    재무뿐) · ok_empty · fs_div 무시 · 최초 관측(계기 여럿은 최댓값)."""
    rows = [("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
            ("20260908000002", D, C2, "005930", "주식등의대량보유상황보고서(약식)"),
            # 같은 정기 유닛을 두 공시가 만든다 — 정정본을 나중(15:00)에 처음 봤다
            ("20260908000003", D, C3, "005930", "반기보고서 (2026.06)", "2026-09-08T12:00:00"),
            ("20260908000004", D, C3, "005930", "[기재정정]반기보고서 (2026.06)",
             "2026-09-08T15:00:00")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    fin1, div1, sh1, cap1 = _periodic(C1)[:4]
    con.execute("INSERT INTO ingest_log VALUES ('fin',?,'2026','11012','OFS','ok',9,NULL,?)",
                (C1, _LOGGED))                                   # fs_div 는 키가 아니다
    _log(con, [div1], status="no_data")                           # 부속 연도 축 013 — 받음
    _log(con, [sh1], status="ok_empty")                           # 정상이라며 0행
    _log(con, [cap1], ts="2026-09-01T00:00:00")                   # 최초 관측 전의 ok
    _log(con, _per_corp(("elestock",), C2), status="no_data")     # 회사 축 무자료 — 받음
    _log(con, _per_corp(("majorstock",), C2))
    _log(con, _periodic(C3)[:1], ts="2026-09-08T13:00:00")        # 원본 뒤·정정본 전
    con.commit()

    rec = dd.received(con, dd.plan(con, D))
    assert {fin1, div1, *_per_corp(dd.HOLDER_ENDPOINTS, C2)} <= rec.ok
    assert rec.nodata == frozenset()                              # 부속·회사 축 013 은 안 부른다
    assert rec.missing[sh1] == "status=ok_empty"
    assert rec.missing[cap1].startswith("ts 2026-09-01T00:00:00 < 최초 관측")
    assert rec.missing[_periodic(C3)[0]] == "ts 2026-09-08T13:00:00 < 최초 관측 2026-09-08T15:00:00"
    assert rec.missing[_periodic(C1)[4]] == "ingest_log 없음"
    con.close()


@pytest.mark.parametrize(("endpoint", "rows", "want"), [
    # 최종 행 = ts 가 가장 늦은 행, fs_div 는 키가 아니다 — 옛 CFS error 뒤 새 OFS ok 는 받음
    ("fin", [("CFS", "error", "2026-09-08T12:30:00"), ("OFS", "ok", _LOGGED)], "ok"),
    ("fin", [("CFS", "ok", "2026-09-08T12:30:00"), ("OFS", "error", _LOGGED)], "status=error"),
    # 같은 시각이면 받지 않은 쪽(P1)
    ("fin", [("CFS", "ok", _LOGGED), ("OFS", "error", _LOGGED)], "status=error"),
    # ts == 최초 관측(12:00)은 받음, 1초 전은 받지 않음
    ("dividend", [("", "ok", "2026-09-08T12:00:00")], "ok"),
    ("dividend", [("", "ok", "2026-09-08T11:59:59")],
     "ts 2026-09-08T11:59:59 < 최초 관측 2026-09-08T12:00:00"),
    # 013 은 모든 축에서 받음 — 다시 부르는 것은 재무뿐(N-23 ② 와 같은 범위)
    ("fin", [("OFS", "no_data", _LOGGED)], "ok·다시 부름"),
    ("dividend", [("", "no_data", _LOGGED)], "ok"),
    ("elestock", [("", "no_data", _LOGGED)], "ok"),
    ("dividend", [("", "ok_empty", _LOGGED)], "status=ok_empty"),
    ("dividend", [], "ingest_log 없음"),
])
def test_received_final_row_rules(tmp_path, endpoint, rows, want) -> None:
    """`received()` 표 — 최종 행 규칙 · 같은 시각 · 최초 관측 경계 · 축별 013 · ok_empty ·
    행 없음."""
    home = _make_home(tmp_path, rows=[
        ("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
        ("20260908000002", D, C1, "005930", "주식등의대량보유상황보고서(약식)")])
    con = _con(home)
    u = (dd.Unit(endpoint, C1, "2026", "11012") if endpoint in dd.PERIODIC_ENDPOINTS
         else dd.Unit(endpoint, C1, "", ""))
    con.executemany("INSERT INTO ingest_log VALUES (?,?,?,?,?,?,1,NULL,?)",
                    [(u.endpoint, u.corp_code, u.bsns_year, u.reprt_code, fs, st, ts)
                     for fs, st, ts in rows])
    con.commit()
    rec = dd.received(con, dd.plan(con, D))
    got = (("ok·다시 부름" if u in rec.nodata else "ok") if u in rec.ok else rec.missing[u])
    assert got == want
    con.close()


def test_periodic_gate_fails_on_unresolved_label_even_when_units_are_received(tmp_path) -> None:
    """라벨을 못 읽은 정기보고서는 유닛을 만들지 못해 '받았다' 를 물을 수 없다 — 받은 유닛이
    다 있어도 FAIL."""
    rows = [*_busy_day_rows(500)[:-1],                            # 정기 C2(반기)만, 주요 제외
            ("20260908000009", D, C3, "005930", "분기보고서 (2025.06)")]   # 결산월 모름 → 미해석
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    _log(con, _periodic(C2))
    _doc_ok(con, "20260908000001")
    _doc_ok(con, "20260908000009")
    con.commit()
    g = _gates(con, dd.plan(con, D))
    assert _failed(g) == {"periodic_followed"}
    assert g["periodic_followed"].metrics == {"n_units": 7, "n_received": 7, "n_unresolved": 1}
    con.close()


# ── 예산 가드 ────────────────────────────────────────────────────────────────────
def test_budget_counts_our_keys_and_kael_separately(tmp_path) -> None:
    home = _make_home(tmp_path)
    con = _con(home)
    ts = "2026-09-08T10:00:00"
    for kid, n in (("k2", 3), ("k3", 2), ("kael", 1)):
        for _ in range(n):
            _calls(con, "list.json", "", ts, key_id=kid)
    con.commit()
    b = dd.budget(con, since_ts="2026-09-08T00:00:00")
    assert (b.ours, b.kael) == (5, 1) and b.kael_used and not b.ok and not b.over_limit
    b2 = dd.budget(con, since_ts="2026-09-08T00:00:00", limit=4)
    assert b2.over_limit and "ours=5/4" in b2.describe()
    con.close()


def _fake_exec(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(dd, "_exec", lambda cmd, *, cwd: (seen.append(list(cmd)), 0)[1])
    return seen


def test_run_stops_before_any_call_when_our_budget_is_blown(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path)
    con = _con(home)
    today = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).strftime("%Y-%m-%dT12:00:00")
    for _ in range(5):
        _calls(con, "list.json", "", today, key_id="k2")
    con.commit()
    con.close()
    seen = _fake_exec(monkeypatch)
    monkeypatch.setattr(dd, "OUR_QUOTA_LIMIT", 4)
    r = dd.run(D, home=home)
    assert r.status is dd.RunStatus.BUDGET_EXCEEDED and r.exit_code == 2 and seen == []
    assert "late 창" not in dd.report(r)                     # 창을 정하기 전에 끝난 런


def test_run_stops_when_kael_production_key_was_used(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path)
    con = _con(home)
    today = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).strftime("%Y-%m-%dT12:00:00")
    _calls(con, "list.json", "", today, key_id="kael")
    con.commit()
    con.close()
    seen = _fake_exec(monkeypatch)
    r = dd.run(D, home=home)
    assert r.status is dd.RunStatus.KAEL_KEY_USED and r.exit_code == 2 and seen == []
    assert "kael" in r.detail


# ── 러너 ────────────────────────────────────────────────────────────────────────
def test_run_sweeps_unlocks_and_calls_backfill_per_group(tmp_path, monkeypatch) -> None:
    rows = [("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
            ("20260908000002", D, C2, "005930", "주요사항보고서(유상증자결정)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    con.execute("INSERT INTO ingest_log VALUES ('fin',?,'2026','11012','CFS','ok',9,NULL,?)",
                (C1, "2026-09-01T00:00:00"))
    con.execute("INSERT INTO ingest_log VALUES ('fricDecsn',?,'','','','ok',1,NULL,?)",
                (C2, "2026-09-01T00:00:00"))
    con.commit()
    con.close()
    seen = _fake_exec(monkeypatch)

    r = dd.run(D, home=home, sweep_from="2026Q3")
    assert r.n_unlocked == 2
    sweep = [c for c in seen if c[1].endswith("sweep_disclosure.py")]
    assert sweep and sweep[0][2:] == ["--from", "2026Q3", "--lookback-days", str(dd.SWEEP_LOOKBACK_DAYS),
                                      "--quota-window", "midnight"]
    backfills = [c for c in seen if c[1].endswith("backfill_dart.py")]
    assert len(backfills) == 2                                   # 정기보고서 1 + DS005 1
    periodic = next(c for c in backfills if "--years" in c)
    assert periodic[periodic.index("--only") + 1] == ",".join(dd.PERIODIC_ENDPOINTS)
    assert periodic[periodic.index("--years") + 1] == "2026"
    assert periodic[periodic.index("--reprt") + 1] == "11012"     # 11011 고정이 아니다(B02)
    ds005 = next(c for c in backfills if "--years" not in c)      # corp_range 축엔 연도가 없다
    assert set(ds005[ds005.index("--only") + 1].split(",")) == set(dd.DS005_ENDPOINTS)
    docs = [c for c in seen if c[1].endswith("backfill_docs.py")]
    assert docs and docs[0][2:] == ["--max-calls", str(dd.DEFAULT_MAX_DOCS)]

    con = _con(home)
    assert con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0] == 0   # 종결 해제됨
    con.close()
    runs = sqlite3.connect(f"{home}/data/raw/daily_run.db").execute(
        "SELECT source, date, status FROM run").fetchall()
    assert runs == [("dart", D, r.status.value)]


def test_run_fails_the_gate_when_backfill_only_logged_a_quota_call(tmp_path, monkeypatch) -> None:
    """G1 ⑤ — 백필이 rc 0 으로 끝났는데 남은 것은 재무 '020'(한도) 콜 기록 1행뿐이다.

    키 소진·키 오류 경로는 `ingest_log` 행을 남기지 않는다(`backfill_dart.py:723-737`).
    옛 판정은 콜 기록만 봐서 통과했다."""
    home = _make_home(tmp_path, rows=_busy_day_rows(500)[:-1])   # 정기 C2 만(주요 제외)
    con = _con(home)
    _doc_ok(con, "20260908000001")
    con.commit()
    con.close()

    def _exec(cmd, *, cwd):
        if cmd[1].endswith("backfill_dart.py"):
            c = _con(home)
            # 옛 판정(이번 런 시작 '뒤' 콜)이 확실히 세도록 미래 시각으로 남긴다
            _calls(c, _FIN_EP, C2, "2099-01-01T00:00:00", year="2026", reprt="11012",
                   status="020")
            c.commit()
            c.close()
        return 0

    monkeypatch.setattr(dd, "_exec", _exec)
    r = dd.run(D, home=home, skip_sweep=True)
    assert r.status is dd.RunStatus.GATE_FAILED and r.exit_code == 2
    assert "periodic_followed" in r.detail


# ── 같은 D 의 두 번째 런 (배포 묶음 3 T13 · A-04 · N-23 ②) ──────────────────────────
def _fin_rows(con, *rows):
    """저장된 재무 행 (corp, year, reprt, rcept_no). 열은 store() 규약 가운데 판정에 쓰는 것만."""
    con.execute("CREATE TABLE IF NOT EXISTS dart_fin_raw (row_hash TEXT PRIMARY KEY, "
                "rcept_no TEXT, req_corp_code TEXT, req_bsns_year TEXT, req_reprt_code TEXT, "
                "req_fs_div TEXT, collected_at TEXT NOT NULL)")
    con.executemany("INSERT INTO dart_fin_raw VALUES (?,?,?,?,?,'CFS',?)",
                    [(f"{c}:{y}:{r}:{rno}", rno, c, y, r, _LOGGED) for c, y, r, rno in rows])


def test_fin_rcept_max_with_two_periods_and_foreign_corp(tmp_path) -> None:
    """(연도, 보고서) 쌍이 둘 이상이고 plan 밖 회사 행이 섞여도 키별 최댓값만 낸다."""
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타")])
    con = _con(home)
    _fin_rows(con, (C1, "2026", "11012", "20260814000001"),
              (C1, "2026", "11012", "20260908000001"),
              (C2, "2025", "11011", "20260310000001"),
              ("99999999", "2026", "11012", "20260909000009"))           # plan 밖 회사
    got = dd.fin_rcept_max(con, {(C1, "2026", "11012"), (C2, "2025", "11011")})
    assert got == {(C1, "2026", "11012"): "20260908000001", (C2, "2025", "11011"): "20260310000001"}
    con.close()


def _fake_backfill(monkeypatch, home, *, fin_rcept_no=None, status="ok"):
    """`backfill_dart.py` 를 흉내 내는 가짜(콜 0) — 부른 유닛마다 `ingest_log` 결과(기본 ok)를
    남긴다.

    `fin_rcept_no` 를 주면 재무 행을 그 접수번호로 적재한다(DART 가 새 판을 준 경우). corp 목록은
    호출 시점에 읽어 둔다 — 목록 파일은 `run()` 이 끝나면 지워진다.
    """
    seen: list[tuple[list[str], list[str]]] = []

    def _exec(cmd, *, cwd):
        if not cmd[1].endswith("backfill_dart.py"):
            return 0
        with open(cmd[cmd.index("--corps") + 1], encoding="utf-8") as f:
            corps = [ln.strip() for ln in f if ln.strip()]
        seen.append((list(cmd), corps))
        eps = cmd[cmd.index("--only") + 1].split(",")
        year = cmd[cmd.index("--years") + 1] if "--years" in cmd else ""
        reprt = cmd[cmd.index("--reprt") + 1] if "--reprt" in cmd else ""
        c = _con(home)
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
        _log(c, [dd.Unit(ep, corp, year, reprt) for corp in corps for ep in eps], status=status,
             ts=now)
        if fin_rcept_no and "fin" in eps:
            _fin_rows(c, *((corp, year, reprt, fin_rcept_no) for corp in corps))
        c.commit()
        c.close()
        return 0

    monkeypatch.setattr(dd, "_exec", _exec)
    return seen


def test_second_run_of_a_received_day_unlocks_and_calls_nothing(tmp_path, monkeypatch) -> None:
    """T13 G1 ① — 저녁에 다 받은 D 를 아침에 다시 돌면 해제 0 · 상세 호출 0.

    옛 코드는 plan 전 유닛을 지우고(해제 22) 두 그룹(정기·DS005)을 다시 불렀다.
    """
    rows = [*_busy_day_rows(500)[:-2],                            # 공시 하한(400·290)을 넘기는 평일
            ("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
            ("20260908000002", D, C2, "005930", "주요사항보고서(유상증자결정)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    _log(con, [*_periodic(C1), *_per_corp(dd.DS005_ENDPOINTS, C2)])
    _fin_rows(con, (C1, "2026", "11012", "20260908000001"))     # 저녁 재무 응답 = 계기 공시 판
    _doc_ok(con, "20260908000001")
    con.commit()
    con.close()
    seen = _fake_backfill(monkeypatch, home)

    r = dd.run(D, home=home, skip_sweep=True)
    assert (r.n_unlocked, seen) == (0, [])
    assert r.status is dd.RunStatus.OK, r.detail
    assert "콜 추정 0 " in dd.report(r)                      # plan 전 유닛(23콜) 기준이 아니다


def test_second_run_recalls_only_the_unit_that_failed(tmp_path, monkeypatch) -> None:
    """T13 G1 ② — 저녁에 배당 1종만 실패(error) → 아침엔 그 유닛만 다시 부른다(재시도 경로 보존)."""
    rows = [*_busy_day_rows(500)[:-2],
            ("20260908000001", D, C1, "005930", "반기보고서 (2026.06)"),
            ("20260908000002", D, C2, "005930", "반기보고서 (2026.06)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    _log(con, [*_periodic(C1), *_periodic(C2)])
    _log(con, [dd.Unit("dividend", C1, "2026", "11012")], status="error")
    _fin_rows(con, (C1, "2026", "11012", "20260908000001"), (C2, "2026", "11012", "20260908000002"))
    _doc_ok(con, "20260908000001")
    _doc_ok(con, "20260908000002")
    con.commit()
    con.close()
    seen = _fake_backfill(monkeypatch, home)

    r = dd.run(D, home=home, skip_sweep=True)
    assert [(c[c.index("--only") + 1], corps) for c, corps in seen] == [("dividend", [C1])]
    cmd = seen[0][0]
    assert (cmd[cmd.index("--years") + 1], cmd[cmd.index("--reprt") + 1]) == ("2026", "11012")
    assert r.n_unlocked == 1
    assert r.status is dd.RunStatus.OK, r.detail


@pytest.mark.parametrize(("new_rcept_no", "n_lag"), [(None, 1), ("20260908000001", 0)])
def test_second_run_rechecks_fin_when_the_stored_filing_is_older(tmp_path, monkeypatch,
                                                                 new_rcept_no, n_lag) -> None:
    """T13 G1 ③ — 저녁 재무 응답의 접수번호가 계기 공시보다 옛것(반영 지연)이면 그 재무 유닛만
    1회 다시 부른다(N-23 ②). 다시 불러도 옛 번호면 완료 판정은 통과시키고 '재무 반영 지연 N건' 을
    출력·런 로그에 남긴다(N-24 3.10).
    """
    rows = [*_busy_day_rows(500)[:-2],
            ("20260908000001", D, C1, "005930", "[기재정정]반기보고서 (2026.06)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    _log(con, _periodic(C1))
    _fin_rows(con, (C1, "2026", "11012", "20260814000001"))     # 정정 전 원본 판
    _doc_ok(con, "20260908000001")
    con.commit()
    con.close()
    seen = _fake_backfill(monkeypatch, home, fin_rcept_no=new_rcept_no)

    r = dd.run(D, home=home, skip_sweep=True)
    assert [(c[c.index("--only") + 1], corps) for c, corps in seen] == [("fin", [C1])]
    assert r.status is dd.RunStatus.OK, r.detail
    assert len(r.fin_lagged) == n_lag
    detail = sqlite3.connect(f"{home}/data/raw/daily_run.db").execute(
        "SELECT detail FROM run").fetchone()[0]
    assert ("재무 반영 지연 1건" in dd.report(r)) is (n_lag == 1)
    assert ("재무 반영 지연 1건" in detail) is (n_lag == 1)


def test_fin_no_data_passes_the_gate_and_is_recalled_once(tmp_path, monkeypatch) -> None:
    """09-22형 — 재무가 연도 축 013(no_data). 013 은 DART 의 확정 답이라 완료 판정은 통과하고
    (N-24 3.2 10-07 수정), 늦은 반영에 대비해 두 번째 런이 그 재무 유닛만 1회 다시 부른다 —
    재무 재확인(3.10)과 같은 성격이라 재호출만 정하고 판정엔 넣지 않는다. 부속 6종의 013(여기선
    배당)은 받음으로만 두고 다시 부르지 않는다(N-23 ② '재무만' 과 같은 범위).
    """
    rows = [*_busy_day_rows(500)[:-2],
            ("20260908000001", D, C1, "005930", "반기보고서 (2026.06)")]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    _log(con, _periodic(C1)[2:])
    _log(con, _periodic(C1)[:2], status="no_data")               # 저녁 재무·배당 응답이 013
    _doc_ok(con, "20260908000001")
    con.commit()
    assert _failed(_gates(con, dd.plan(con, D))) == set()       # 완료 판정 통과
    con.close()
    seen = _fake_backfill(monkeypatch, home, status="no_data")   # 아침에도 여전히 013

    r = dd.run(D, home=home, skip_sweep=True)
    assert [(c[c.index("--only") + 1], corps) for c, corps in seen] == [("fin", [C1])]
    assert (r.n_missing, r.n_fin_recheck, r.n_nodata_recheck) == (0, 0, 1)
    assert r.status is dd.RunStatus.OK, r.detail
    assert "재무 자료없음 재확인 1" in dd.report(r)


@pytest.mark.parametrize("evening", ["fin_lag", "nodata"])
def test_recheck_call_error_flips_received_to_gate_failed(tmp_path, monkeypatch, evening) -> None:
    """저녁에 '받음'(옛 판 ok 또는 재무 013)이던 재무 유닛을 아침 재확인으로 다시 부르다 실패하면
    GATE_FAILED — unlock 이 저녁 행을 먼저 지우므로 받음이 받지 않음으로 바뀐다. P1 쪽으로 둔다."""
    nm = "[기재정정]반기보고서 (2026.06)" if evening == "fin_lag" else "반기보고서 (2026.06)"
    home = _make_home(tmp_path, rows=[*_busy_day_rows(500)[:-2],
                                      ("20260908000001", D, C1, "005930", nm)])
    con = _con(home)
    _log(con, _periodic(C1)[1:])
    if evening == "fin_lag":
        _log(con, _periodic(C1)[:1])
        _fin_rows(con, (C1, "2026", "11012", "20260814000001"))  # 정정 전 판
    else:
        _log(con, _periodic(C1)[:1], status="no_data")
    _doc_ok(con, "20260908000001")
    con.commit()
    assert _failed(_gates(con, dd.plan(con, D))) == set()       # 저녁 상태 = 판정 통과
    con.close()
    seen = _fake_backfill(monkeypatch, home, status="error")     # 아침 재확인 호출이 실패

    r = dd.run(D, home=home, skip_sweep=True)
    assert [(c[c.index("--only") + 1], corps) for c, corps in seen] == [("fin", [C1])]
    assert r.status is dd.RunStatus.GATE_FAILED and "status=error" in r.detail


_EVENING = "2026-09-08T09:05:00"            # 그 D 의 첫 dart 런(18:05 KST 저녁) 시작, UTC


def _prior_dart_run(home, started):
    """그 D 의 앞선 dart 런 1행 — runlog 실물로 남기고 시작 시각 원문만 고정한다('…Z' UTC)."""
    db = f"{home}/data/raw/daily_run.db"
    rid = runlog.start(db, date=D, source="dart")
    con = sqlite3.connect(db)
    con.execute("UPDATE run SET started=? WHERE run_id=?", (started, rid))
    con.commit()
    con.close()


def test_run_stops_on_a_runlog_start_time_in_an_unknown_format(tmp_path, monkeypatch) -> None:
    """앞선 런의 시작 시각을 읽지 못하면 (b) 창을 조용히 잃지 않고 멈춘다(ValueError)."""
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타")])
    _prior_dart_run(home, "2026-09-08 18:05:00+09:00")
    seen = _fake_exec(monkeypatch)
    with pytest.raises(ValueError):
        dd.run(D, home=home, skip_sweep=True)
    assert seen == []


@pytest.mark.parametrize(("evening_status", "skip_sweep"),
                         [("error", False), ("ok", False), ("error", True)])
def test_second_run_follows_late_filings_first_seen_by_the_first_run(tmp_path, monkeypatch,
                                                                     evening_status,
                                                                     skip_sweep) -> None:
    """저녁 (b) 실패가 아침 OK 에 덮이던 경로(검토 지적) — 두 번째 런도 그 D 의 첫 런이 처음 본 늦은
    공시 L 을 plan 에 넣는다.

    L 유닛이 저녁에 실패했으면 다시 부르고 판정에 넣는다(계속 실패면 GATE_FAILED). 저녁에 받았으면
    `received()` 가 건너뛰어 호출 0. 스윕 없는 재실행(`--skip-sweep`, 절차서 T17)도 같다.
    옛 코드는 (b) 기준을 이번 스윕 시작으로 잡아 L 이 plan 에서 빠졌다(n_late 0 · 호출 0 · OK).
    """
    late = ("20260824000001", "20260824", C2, "005930", "주식등의대량보유상황보고서(약식)",
            "2026-09-08T09:10:00")                              # 저녁 스윕(18:10 KST)이 처음 봤다
    home = _make_home(tmp_path, rows=[*_busy_day_rows(500)[:-2], late])
    _prior_dart_run(home, f"{_EVENING}Z")
    con = _con(home)
    _log(con, _per_corp(dd.HOLDER_ENDPOINTS, C2), status=evening_status, ts="2026-09-08T09:30:00")
    con.commit()
    con.close()
    seen = _fake_backfill(monkeypatch, home, status="error")    # 다시 불러도 계속 실패

    r = dd.run(D, home=home, sweep_from="2026Q3", skip_sweep=skip_sweep)
    assert (r.plan.n_late, r.plan.holder_corps) == (1, (C2,))
    # (b) 창 시작과 출처를 출력·런 로그에 남긴다(검토 M-5)
    assert (r.late_since, r.late_source) == (_EVENING, "첫 런 시작")
    detail = sqlite3.connect(f"{home}/data/raw/daily_run.db").execute(
        "SELECT detail FROM run ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    assert f"late 창 {_EVENING} (첫 런 시작)" in dd.report(r)
    assert f"late 창 {_EVENING} (첫 런 시작)" in detail
    if evening_status == "ok":
        assert seen == [] and r.status is dd.RunStatus.OK, r.detail
    else:
        assert [(c[c.index("--only") + 1], corps) for c, corps in seen] == [
            ("elestock,majorstock", [C2])]
        assert r.status is dd.RunStatus.GATE_FAILED and "holder_followed" in r.detail


def test_run_skip_sweep_and_default_window(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타")])
    seen = _fake_exec(monkeypatch)
    dd.run(D, home=home, skip_sweep=True)
    assert not [c for c in seen if c[1].endswith("sweep_disclosure.py")]
    assert dd.sweep_from_for("20260908") == "2026Q3"
    # 분기 첫 30일은 직전 분기부터 — 접수일이 지난 공시가 뒤늦게 목록에 나타난다
    # (검수 D H3: 09-10 스윕에서 08-25 접수 8건이 처음 나타남). 30일 뒤에는 당 분기만.
    assert dd.SWEEP_LOOKBACK_DAYS == 30
    assert dd.sweep_from_for("20261005") == "2026Q3"
    assert dd.sweep_from_for("20261105") == "2026Q4"
    assert dd.sweep_from_for("20260101") == "2025Q4"


# 스윕이 방금 적재한 것처럼 보이게 하는 미래 시각. `run()` 이 스윕 직전에 잡는 시각보다
# 항상 뒤라서 "이번 스윕에서 처음 봤다" 판정에 걸린다.
_SEEN_NOW = "2099-01-01T00:00:00"


def test_run_follows_filings_that_first_appeared_in_this_sweep(tmp_path, monkeypatch) -> None:
    rows = [("20260908000001", D, C1, "005930", "기타시장안내"),
            ("20260824000001", "20260824", C2, "005930",
             "주식등의대량보유상황보고서(약식)", _SEEN_NOW)]
    home = _make_home(tmp_path, rows=rows)
    seen = _fake_exec(monkeypatch)

    r = dd.run(D, home=home, sweep_from="2026Q3")
    assert r.plan.n_late == 1 and r.plan.holder_corps == (C2,)
    assert r.late_source == "이번 런 시작"                       # 앞선 런이 없는 첫 런
    backfills = [c for c in seen if c[1].endswith("backfill_dart.py")]
    assert len(backfills) == 1                                   # 지분공시 1그룹
    only = backfills[0][backfills[0].index("--only") + 1]
    assert set(only.split(",")) == set(dd.HOLDER_ENDPOINTS)
    assert "late=1" in dd.report(r) and "20260824000001" in dd.report(r)


def test_run_takes_since_ts_before_launching_the_sweep(tmp_path, monkeypatch) -> None:
    """스윕이 **그 런 도중에** 적재한 행이 잡히려면 since_ts 를 서브프로세스 기동 전에 잡아야 한다."""
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타시장안내")])

    def _spy(cmd, *, cwd):
        if cmd[1].endswith("sweep_disclosure.py"):
            con = _con(home)
            con.execute(
                "INSERT INTO dart_disclosure VALUES (?,?,?,?,?,?,?)",
                ("hlate", "20260824000001", "20260824", C2, "005930",
                 "주식등의대량보유상황보고서(약식)",
                 dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")))
            con.commit()
            con.close()
        return 0

    monkeypatch.setattr(dd, "_exec", _spy)
    r = dd.run(D, home=home, sweep_from="2026Q3")
    assert r.plan.n_late == 1 and r.plan.holder_corps == (C2,)


def test_run_skip_sweep_does_not_follow_late_filings(tmp_path, monkeypatch) -> None:
    """`--skip-sweep` 이면 since_ts 가 없다 → (b) 는 빈 집합(종전 동작)."""
    rows = [("20260824000001", "20260824", C2, "005930",
             "주식등의대량보유상황보고서(약식)", _SEEN_NOW)]
    home = _make_home(tmp_path, rows=rows)
    seen = _fake_exec(monkeypatch)

    r = dd.run(D, home=home, skip_sweep=True)
    assert r.plan.n_late == 0 and r.plan.units == ()
    assert (r.late_since, r.late_source) == (None, "없음")
    assert not [c for c in seen if c[1].endswith("backfill_dart.py")]


def test_dry_run_does_not_touch_ingest_log_or_runlog(tmp_path, monkeypatch) -> None:
    rows = [(f"2026090800{i:04d}", D, c, "005930", "주요사항보고서(유상증자결정)")
            for i, c in enumerate((C1, C2, C3))]
    home = _make_home(tmp_path, rows=rows)
    con = _con(home)
    con.executemany("INSERT INTO ingest_log VALUES ('fricDecsn',?,'','','','ok',1,NULL,?)",
                    [(c, "2026-09-01T00:00:00") for c in (C1, C2, C3)])
    con.commit()
    con.close()
    seen = _fake_exec(monkeypatch)

    r = dd.run(D, home=home, dry_run=True, limit=2, skip_sweep=True)
    assert r.status is dd.RunStatus.DRY_RUN and r.exit_code == 0 and r.n_unlocked == 0
    con = _con(home)
    assert con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0] == 3   # 그대로
    con.close()
    assert not (tmp_path / "data" / "raw" / "daily_run.db").exists()
    # --limit 만큼만 corp 을 자른다
    backfill = next(c for c in seen if c[1].endswith("backfill_dart.py"))
    corps_path = backfill[backfill.index("--corps") + 1]
    # 임시 디렉터리는 run() 종료 시 지워지므로 인자 형태만 본다
    assert corps_path.endswith(".txt")
    docs = next(c for c in seen if c[1].endswith("backfill_docs.py"))
    assert docs[docs.index("--max-calls") + 1] == "2"


def test_dry_run_truncates_corps_to_limit(tmp_path, monkeypatch) -> None:
    rows = [(f"2026090800{i:04d}", D, c, "005930", "주요사항보고서(유상증자결정)")
            for i, c in enumerate((C1, C2, C3))]
    home = _make_home(tmp_path, rows=rows)
    written: list[list[str]] = []

    def _spy(cmd, *, cwd):
        if cmd[1].endswith("backfill_dart.py"):
            with open(cmd[cmd.index("--corps") + 1], encoding="utf-8") as f:
                written.append([ln.strip() for ln in f if ln.strip()])
        return 0

    monkeypatch.setattr(dd, "_exec", _spy)
    dd.run(D, home=home, dry_run=True, limit=2, skip_sweep=True)
    assert written == [[C1, C2]]                       # 3 corp 중 2개만


def test_run_reports_tool_failure(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타")])
    monkeypatch.setattr(dd, "_exec", lambda cmd, *, cwd: 1)
    r = dd.run(D, home=home, skip_sweep=True)
    assert r.status is dd.RunStatus.TOOL_FAILED and r.exit_code == 2 and r.rc_docs == 1


def test_run_rejects_a_db_without_the_required_tables(tmp_path) -> None:
    (tmp_path / "data" / "raw").mkdir(parents=True)
    sqlite3.connect(tmp_path / "data" / "raw" / "dart.db").close()
    with pytest.raises(RuntimeError, match="missing tables"):
        dd.run(D, home=str(tmp_path))


def test_report_renders_gates_and_warnings(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "분기보고서 (2025.06)")])
    _fake_exec(monkeypatch)
    text = dd.report(dd.run(D, home=home, skip_sweep=True))
    assert "기간 라벨 미해석 1건" in text and "periodic_followed" in text


def test_parse_date_accepts_both_forms_and_rejects_junk() -> None:
    assert dd._parse_date("2026-09-08") == "20260908" == dd._parse_date("20260908")
    with pytest.raises(ValueError, match="--date must be"):
        dd._parse_date("2026-9-8")
