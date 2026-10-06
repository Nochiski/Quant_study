"""backfill_wise.probe_coverage — 커버 판정은 대상 3개년을 다 본 뒤에만 none 이다.

검수 D H1(2026-09-10): 당해 연도(ymms[0]) 의 cF5001 만 보고 none 을 확정해 09-10 커버 상실 4건 중
3건(036010·190510·342870)이 오탐이었다 — FY2027 추정치가 실재했다. 연말로 갈수록 당해 연도가
먼저 비므로 오탐이 늘어난다. false-none 은 그날 스냅샷의 영구 손실이다.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import backfill_wise as bw
import pytest
from daily.universe import kiwoom_common

YMMS = ["202612", "202712", "202812"]


def _fetch(covered_years, fail_years=()):
    calls = []

    def fetch(cmp_cd, ep, pk, url):
        calls.append((ep, pk))
        if pk in fail_years:
            return cmp_cd, "http/500", b"", 0, 1
        c1 = {"select_item": [1 if pk in covered_years else None], "target_price": [None]}
        body = json.dumps({"chart1": json.dumps(c1), "chart2": json.dumps({"select_item": [None]})}).encode()
        return cmp_cd, "ok", body, len(body), 1
    return fetch, calls


def test_later_year_coverage_counts_as_covered_and_fetches_full_set():
    fetch, calls = _fetch({"202712"})
    covered, out = bw.probe_coverage("036010", YMMS, fetch)
    assert covered
    # 판정 순서: cF5001 을 연도순으로 보다가 커버가 나오면 나머지 연도·cF5002 전부 받는다 (총 6콜 = 종전과 같다)
    assert calls == [("cF5001", "202612"), ("cF5001", "202712"), ("cF5001", "202812"),
                     ("cF5002", "202612"), ("cF5002", "202712"), ("cF5002", "202812")]
    assert len(out) == 6


def test_none_is_declared_only_after_all_three_years():
    fetch, calls = _fetch(set())
    covered, out = bw.probe_coverage("000000", YMMS, fetch)
    assert not covered
    assert calls == [("cF5001", y) for y in YMMS] and len(out) == 3


def test_probe_fetch_failure_is_treated_as_covered():
    # 오판 비용이 비대칭(false-none = 영구 손실)이라 판정 불능은 covered 로 둔다 (is_covered 와 같은 원칙)
    fetch, calls = _fetch(set(), fail_years={"202612"})
    covered, _ = bw.probe_coverage("000000", YMMS, fetch)
    assert covered
    assert calls[0] == ("cF5001", "202612") and len(calls) == 6


def test_request_budget_per_stock():
    # ledger_health.wise.req_identity 의 상수와 맞물린다: 커버 18(종전 15 + 재무 추가 3) · 무커버 4(목록 1 + cF5001 3)
    assert bw.REQ_COVERED == 15 + len(bw.FIN_REQUESTS) - 2 == 18 and bw.REQ_NONE == 4
    assert bw.req_covered_on(bw.FIN_EXT_SINCE) == 18 and bw.req_covered_on("2026-09-30") == 15


def test_fin_requests_add_quarterly_income_and_annual_bs_cf():
    """플랜 2026-09-30 T-Q2 — 분기 손익·연간 재무상태·현금흐름을 pkey 로 가른다. 기존 두 요청(pkey 'Y')은 그대로."""
    got = {(ep, pk): extra for ep, pk, extra in bw.FIN_REQUESTS}
    assert got[("cF3002", "Y")] == {"frq": "0", "rpt": "0", "frqTyp": "0"}
    assert got[("cF4002", "Y")] == {"frq": "0", "rpt": "5", "frqTyp": "0"}
    assert got[("cF3002", "Q:IS")] == {"frq": "1", "rpt": "0", "frqTyp": "1"}
    assert got[("cF3002", "Y:BS")] == {"frq": "0", "rpt": "1", "frqTyp": "0"}
    assert got[("cF3002", "Y:CF")] == {"frq": "0", "rpt": "2", "frqTyp": "0"}
    assert len(got) == len(bw.FIN_REQUESTS) == 5          # (ep, pkey) 유일 = ws_raw 키가 안 겹친다


def test_fin_screens_request_main_basis_not_consolidated_only():
    """DQ-5(2026-09-26): cF3002/cF4002 를 연결(IFRSL) 고정으로 부르면 별도만 내는 회사(예 샘씨엔에스
    252990)의 값이 전부 NULL 로 온다. 주재무제표(MAIN)로 불러야 연결 있는 회사는 연결, 없는 회사는
    별도가 온다(09-26 실측: 252990 MAIN=별도 순이익 150.6억, 005930 MAIN=연결=IFRSL 과 동일)."""
    import inspect
    assert bw.FIN_GUBUN == "MAIN"
    src = inspect.getsource(bw)
    assert '"finGubun": "IFRSL"' not in src, "cF3002/cF4002 요청이 다시 연결 고정으로 돌아갔다"
    assert src.count('"finGubun": FIN_GUBUN') == 2


def test_daily_mode_is_gone():
    """DQ-9: `--mode daily` 는 무커버 판정 종목을 영구 스킵(재프로브 없음)하는데 건전성 검사
    `wise.run` 은 mode='full' 을 요구한다 — 운영에서 쓸 수 없는 길이라 제거했다(크론은 full).
    네트워크·DB 를 타지 않고 파서만 보려고 `main()` 에서 `_parse_args` 를 뽑아 썼다."""
    with pytest.raises(SystemExit):
        bw._parse_args(["--mode", "daily"])
    assert bw._parse_args(["--mode", "full"]).mode == "full"
    assert bw._parse_args([]).mode == "full"


# ── universe() — WISE 수집 유니버스는 키움 수집과 같은 규칙이다(A-07, 10-06) ──────

def _kiwoom_master(base: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """`<base>/data/raw/kiwoom.db` 에 키움 마스터를 만든다.

    rows: (snap_date, code, upSizeName, marketName). 열은 `kiwoom_common`·`universe()` 가
    읽는 넷만 둔다(실물은 응답 필드 전부 TEXT)."""
    raw = base / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(raw / "kiwoom.db")) as con:
        con.execute("CREATE TABLE ka10099_stock_master "
                    "(snap_date TEXT, code TEXT, upSizeName TEXT, marketName TEXT)")
        con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?,?)", rows)
        con.commit()
    return raw / "kiwoom.db"


def test_universe_follows_kiwoom_rule_and_keeps_new_listing_without_size_class(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """A-07: 규모구분(upSizeName)은 상장 몇 주 뒤에야 붙는다. 옛 규칙(`upSizeName<>''` 만)은
    0039P0 매드업(07-01 상장, 코스닥, 시총 1,497억 — 원본 v3 에는 2026E 컨센서스가 있다)을
    한 번도 받지 않아 '추정치 보유' 유니버스에서 조용히 뺐다. 종목 선정은 키움 수집과 같은
    `kiwoom_common` 하나여야 한다."""
    kw = _kiwoom_master(tmp_path, [
        ("20261002", "000660", "대형주", "거래소"),   # 직전 스냅샷에만 있다 → 최신만 본다
        ("20261005", "005930", "대형주", "거래소"),   # 규모구분 있는 보통주
        ("20261005", "0039P0", "", "코스닥"),         # 신규 상장 보통주, 규모구분 없음 → 포함
        ("20261005", "005935", "", "거래소"),         # 우선주(6번째 자리 '5') → 제외
        ("20261005", "0238P0", "", "ETF"),            # ETF → 제외
    ])
    monkeypatch.setattr(bw, "BASE_DIR", str(tmp_path))
    with closing(sqlite3.connect(":memory:")) as con_w:     # WISE 원장 자리 — 선정에 안 쓴다
        got = bw.universe(con_w)
    with closing(sqlite3.connect(kw)) as con_kw:
        want = kiwoom_common(con_kw).tickers
    assert "0039P0" in got and "005935" not in got
    assert got == sorted(want) == ["0039P0", "005930"]
    assert "· 유니버스 = 키움 마스터 20261005 (2종목)" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["no_snapshot", "no_file"])
def test_universe_falls_back_to_krx_without_kiwoom_snapshot(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], case: str) -> None:
    """키움 마스터에 스냅샷이 없거나(`kiwoom_common` 의 ValueError) 파일을 못 열면
    (sqlite3.Error) 종전대로 KRX 종목기본 최신일의 보통주로 폴백한다. 실패 안내 줄은
    열기·조회 실패일 때만 찍힌다(종전과 같다)."""
    if case == "no_snapshot":
        _kiwoom_master(tmp_path, [])
    krx = tmp_path / "krx.db"
    with closing(sqlite3.connect(krx)) as con:
        for t, rows in (("krx_stk_isu_base_info", [("20260820", "005930", "보통주"),
                                                   ("20260820", "005935", "구형우선주")]),
                        ("krx_ksq_isu_base_info", [("20260820", "247540", "보통주")])):
            con.execute(f"CREATE TABLE {t} "
                        "(bas_dd_req TEXT, ISU_SRT_CD TEXT, KIND_STKCERT_TP_NM TEXT)")
            con.executemany(f"INSERT INTO {t} VALUES (?,?,?)", rows)
        con.commit()
    monkeypatch.setattr(bw, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bw, "KRX", str(krx))
    with closing(sqlite3.connect(":memory:")) as con_w:
        assert bw.universe(con_w) == ["005930", "247540"]
    out = capsys.readouterr().out
    assert "유니버스 = 키움 마스터" not in out
    assert ("키움 마스터 조회 실패" in out) == (case == "no_file")
