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
    assert bf.SPEC["fin"]["ep"] == dd.FIN_CALL_EP


# ── 픽스처 ──────────────────────────────────────────────────────────────────────
_DDL = (
    ("CREATE TABLE dart_disclosure (row_hash TEXT PRIMARY KEY, rcept_no TEXT, rcept_dt TEXT, "
     "corp_code TEXT, stock_code TEXT, report_nm TEXT)"),
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


def _corp_for(i: int) -> str:
    return (C1, C2, C3)[i % 3]


def _make_home(tmp_path, *, rows=None):
    """표본 20건이 든 dart.db 를 tmp home 에 만든다. rows 를 주면 그것으로 대체한다."""
    db = tmp_path / "data" / "raw" / "dart.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    for ddl in _DDL:
        con.execute(ddl)
    if rows is None:
        rows = [(f"2026090800{i:04d}", D, _corp_for(i), "005930", nm)
                for i, (nm, *_rest) in enumerate(SAMPLES)]
    con.executemany(
        "INSERT INTO dart_disclosure VALUES (?,?,?,?,?,?)",
        [(f"h{i}", *r) for i, r in enumerate(rows)])
    con.commit()
    con.close()
    return str(tmp_path)


def _con(home) -> sqlite3.Connection:
    return sqlite3.connect(f"{home}/data/raw/dart.db")


def _calls(con, endpoint, corp, ts, *, key_id="k2", year="", reprt=""):
    con.execute("INSERT INTO dart_call_log VALUES (?,?,?,?,'', '000', 1, ?, ?)",
                (endpoint, corp, year, reprt, ts, key_id))


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
    assert p.n_calls_est == 24 + 1                       # fin 은 CFS→OFS 폴백으로 최대 2콜
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


# ── 완료 판정 ────────────────────────────────────────────────────────────────────
def _busy_day_rows(n_extra: int) -> list[tuple[str, str, str, str, str]]:
    rows = [(f"2026090810{i:04d}", D, C1, "005930", "기타시장안내") for i in range(n_extra)]
    rows.append(("20260908000001", D, C2, "005930", "반기보고서 (2026.06)"))
    rows.append(("20260908000002", D, C3, "005930", "주요사항보고서(유상증자결정)"))
    return rows


def test_gates_pass_when_ledger_and_calls_are_complete(tmp_path) -> None:
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    p = dd.plan(con, D)
    since = "2026-09-08T00:00:00"
    _calls(con, dd.FIN_CALL_EP, C2, "2026-09-08T10:00:00", year="2026", reprt="11012")
    _calls(con, "fricDecsn.json", C3, "2026-09-08T10:00:01")
    con.execute("INSERT INTO doc_store VALUES ('20260908000001',1,'x',1,1,'000','t')")
    con.commit()
    gates = {g.name: g for g in dd.check(con, p, since_ts=since)}
    assert all(g.ok for g in gates.values()), {k: v.detail for k, v in gates.items()}
    assert gates["filings"].metrics == {"n_filings": 502, "n_listed_rows": 502}
    assert gates["periodic_followed"].metrics["n_fin_recalled"] == 1
    assert gates["major_followed"].metrics["n_corp_recalled"] == 1
    assert gates["documents"].metrics == {"n_target": 1, "n_ok": 1, "n_never_tried": 0,
                                          "n_failed_other": 0}
    con.close()


def test_gates_fail_on_thin_ledger_and_missing_recalls(tmp_path) -> None:
    home = _make_home(tmp_path, rows=_busy_day_rows(10))          # 12건 < 하한 400
    con = _con(home)
    p = dd.plan(con, D)
    gates = {g.name: g for g in dd.check(con, p, since_ts="2026-09-08T00:00:00")}
    assert not gates["filings"].ok and gates["filings"].metrics["n_filings"] == 12
    assert not gates["periodic_followed"].ok      # fin 재호출 0
    assert not gates["major_followed"].ok         # DS005 재호출 0
    assert not gates["documents"].ok              # doc_store 에 행 없음 → n_never_tried=1
    assert gates["documents"].metrics["n_never_tried"] == 1
    con.close()


def test_filings_gate_allows_zero_on_non_trading_day(tmp_path) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", "20260907", C1, "005930", "기타")])
    con = _con(home)
    p = dd.plan(con, D)
    gates = {g.name: g for g in dd.check(con, p, since_ts="2026-09-08T00:00:00",
                                         trading_day=False)}
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
    g = {x.name: x for x in dd.check(con, p, since_ts="2026-09-08T00:00:00")}["documents"]
    assert not g.ok and g.metrics == {"n_target": 2, "n_ok": 0, "n_never_tried": 0,
                                      "n_failed_other": 1}
    con.execute("UPDATE doc_store SET http_status='014' WHERE rcept_no='20260908000002'")
    con.commit()
    g = {x.name: x for x in dd.check(con, p, since_ts="2026-09-08T00:00:00")}["documents"]
    assert g.ok
    con.close()


def test_periodic_gate_ignores_ingest_log_status(tmp_path) -> None:
    """`ok` 는 영구 종결이라 어제 ok 였던 유닛이 오늘 재호출 없이 통과하면 안 된다(DEFECT-B01)."""
    home = _make_home(tmp_path, rows=_busy_day_rows(500))
    con = _con(home)
    con.execute("INSERT INTO ingest_log VALUES ('fin',?,'2026','11012','CFS','ok',9,NULL,?)",
                (C2, "2026-09-01T00:00:00"))
    con.commit()
    p = dd.plan(con, D)
    g = {x.name: x for x in dd.check(con, p, since_ts="2026-09-08T00:00:00")}
    assert not g["periodic_followed"].ok and g["periodic_followed"].metrics["n_fin_recalled"] == 0
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
    assert sweep and sweep[0][2:] == ["--from", "2026Q3", "--quota-window", "midnight"]
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


def test_run_skip_sweep_and_default_window(tmp_path, monkeypatch) -> None:
    home = _make_home(tmp_path, rows=[("20260908000001", D, C1, "005930", "기타")])
    seen = _fake_exec(monkeypatch)
    dd.run(D, home=home, skip_sweep=True)
    assert not [c for c in seen if c[1].endswith("sweep_disclosure.py")]
    assert dd.sweep_from_for("20260908") == "2026Q3"
    assert dd.sweep_from_for("20260101") == "2026Q1"


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
