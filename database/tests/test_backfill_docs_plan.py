"""backfill_docs.build_plan — 014(문서 없음) 확정 건은 기본 재시도하지 않는다. 플랜 P2 실측 결함."""
import importlib
import sqlite3
import sys


def _load(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KRX_API_KEY=k\nKRX_ID=i\nKRX_PW=p\nDART_API_KEY_2=x\n", encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    for m in ("api", "backfill_docs"):
        sys.modules.pop(m, None)
    return importlib.import_module("backfill_docs")


def _db(tmp_path):
    con = sqlite3.connect(tmp_path / "dart.db")
    con.execute("CREATE TABLE dart_disclosure (rcept_no TEXT, rcept_dt TEXT, stock_code TEXT, report_nm TEXT)")
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)", [
        ("20260901000001", "20260901", "005930", "사업보고서 (2025.12)"),
        ("20260901000002", "20260901", "005930", "[기재정정]사업보고서 (2025.12)"),
        ("20260901000003", "20260901", "005930", "반기보고서 (2026.06)")])
    con.execute("CREATE TABLE doc_store (rcept_no TEXT PRIMARY KEY, bytes INTEGER, sha256 TEXT, n_files INTEGER, "
                "zip_ok INTEGER, http_status TEXT, fetched_at TEXT)")
    con.executemany("INSERT INTO doc_store VALUES (?,?,?,?,?,?,?)", [
        ("20260901000001", 10, "s", 1, 1, "000", "t"),     # 받음
        ("20260901000002", 0, "s", 0, 0, "014", "t")])     # DART: 문서 없음(영구)
    con.commit()
    return con


def test_014_is_excluded_by_default(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    plan = m.build_plan(con, [2, 3])
    assert [r for _, r in plan] == ["20260901000003"]


def test_014_can_be_retried_explicitly(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    plan = m.build_plan(con, [2, 3], retry_014=True)
    assert sorted(r for _, r in plan) == ["20260901000002", "20260901000003"]


def test_p4_takes_preliminary_earnings_from_2026_07_only_and_skips_subsidiary_ones(tmp_path, monkeypatch):
    """N-64 — 잠정실적 원문은 2026-07-01 접수부터 받는다. 하한이 없으면 2010 뒤 약 3.4만 건이 일일 수집
    (런마다 3,000건 상한)으로 밀려 들어와 06:00 · 18:05 런이 길어진다. 자회사 실적 공시는 상장사 본인 숫자가 아니다."""
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)", [
        ("20261029800635", "20261029", "073240", "연결재무제표기준영업(잠정)실적(공정공시)"),
        ("20261021900206", "20261021", "300120", "영업(잠정)실적(공정공시)"),
        ("20261027800047", "20261027", "068270", "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"),
        ("20260701800001", "20260701", "000001", "연결재무제표 기준 영업(잠정)실적(공정공시)"),
        ("20261020800099", "20261020", "000002", "연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)"),
        ("20260630800001", "20260630", "000003", "연결재무제표기준영업(잠정)실적(공정공시)"),
        ("20261020800100", "20261020", "", "영업(잠정)실적(공정공시)")])
    plan = m.build_plan(con, [4])
    assert [r for _, r in plan] == ["20261029800635", "20261027800047", "20261021900206", "20260701800001"]
    assert {p for p, _ in plan} == {4}


def test_daily_gate_and_health_count_the_same_documents_the_collector_takes(tmp_path, monkeypatch):
    """원문 대상 술어의 정본은 `doc_targets` 하나다 — 그날 수집기가 받을 원문과 dart_daily documents 게이트 ·
    ledger_health dart.docs 가 세는 원문이 같아야 한다. 사본이 따로 있을 때 #655 결함(괄호)은 게이트 쪽에만,
    잠정실적 누락은 건전성 쪽에만 났다."""
    from types import SimpleNamespace

    from daily import dart_daily as dd
    from daily import ledger_health as lh

    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    d = "20261029"
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)", [
        ("20261029000001", d, "005930", "주식분할결정"),
        ("20261029000002", d, "005930", "주식병합결정"),
        ("20261029000003", d, "005930", "사업보고서 (2025.12)"),
        ("20261029000004", d, "005930", "사업보고서제출기한연장신고서"),
        ("20261029000005", d, "005930", "[기재정정]반기보고서 (2026.06)"),
        ("20261029000006", d, "005930", "분기보고서 (2026.09)"),
        ("20261029800007", d, "005930", "연결재무제표기준영업(잠정)실적(공정공시)"),
        ("20261029800008", d, "000660", "연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)"),
        ("20261029000009", d, "", "사업보고서 (2025.12)"),
        ("20261029000010", d, "005930", "임원ㆍ주요주주특정증권등소유상황보고서"),
        ("20261028000011", "20261028", "005930", "분기보고서 (2026.09)")])
    on_d = {r for (r,) in con.execute("SELECT rcept_no FROM dart_disclosure WHERE rcept_dt = ?", (d,))}
    taken = {r for _, r in m.build_plan(con, [1, 2, 3, 4]) if r in on_d}
    assert taken == {"20261029000001", "20261029000002", "20261029000003", "20261029000005",
                     "20261029000006", "20261029800007"}
    assert dd._gate_docs(con, SimpleNamespace(date=d)).metrics["n_target"] == len(taken)
    health = {c.name: c for c in lh.check_dart(con, d, True, "2026-10-28T15:00:00")}["dart.docs"]
    assert health.value["target"] == len(taken)


def test_014_within_seven_days_of_filing_is_retried_and_older_is_not(tmp_path, monkeypatch):
    """본문 파일은 접수 뒤 1~3시간 지나 열린다(11-13 실측: 16:15 뒤 접수 299건이 19:30 에 014, 자정에 20건 중
    19건 000). 접수 당일 저녁의 014 를 영구 실패로 굳히면 그 본문은 다시 받지 않아 fin_std 기말일이 추정으로
    남는다(11-13 저녁 · 11-14 아침 확정 빌드 EG3 실패). 접수 7일 안의 014 는 다시 부른다."""
    m = _load(tmp_path, monkeypatch)
    con = _db(tmp_path)
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)", [
        ("20261113000001", "20261113", "005930", "분기보고서 (2026.09)"),    # 접수 당일 014 → 다시 부른다
        ("20261107000001", "20261107", "005930", "분기보고서 (2026.09)"),    # 7일째 → 다시 부른다
        ("20261106000001", "20261106", "005930", "분기보고서 (2026.09)")])   # 8일째 → 영구 실패로 본다
    con.executemany("INSERT INTO doc_store VALUES (?,?,?,?,?,?,?)", [
        ("20261113000001", 0, "s", 0, 0, "014", "t"),
        ("20261107000001", 0, "s", 0, 0, "014", "t"),
        ("20261106000001", 0, "s", 0, 0, "014", "t")])
    plan = m.build_plan(con, [2, 3], today="20261114")
    assert [r for _, r in plan] == ["20261113000001", "20261107000001", "20260901000003"]
