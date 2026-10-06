"""장 마감 직후 프로브(`src/probe_postclose.py`) — 행 고르기·표본·확정 시각·채점(합성 DB 왕복).

키움을 부르지 않는다. 채점은 합성 postclose.db · krx.db · kiwoom.db 로 판독 셋
(가격 일치 시각, 수급 확정 시각,
후보 전량 소요)을 본다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import probe_postclose as P
import pytest

T = "20261006"
FLOWS_A = {"ind_invsr": "10", "frgnr_invsr": "-5", "orgn": "-5"}


def test_price_strips_direction_sign() -> None:
    assert P.price("-259500") == 259500 and P.price("+1,234") == 1234
    assert P.price(None) is None and P.price("") is None


def test_split_code_reads_exchange_suffix() -> None:
    assert P.split_code("005930") == ("005930", "KRX")
    assert P.split_code("005930_NX") == ("005930", "NXT")
    assert P.split_code("005930_AL") == ("005930", "SOR")
    assert P.split_code("ka10095_NX") == ("ka10095", "NXT")


def test_norm_code_and_batches() -> None:
    assert P.norm_code("A005930") == "005930" and P.norm_code("005930_NX") == "005930"
    assert P.norm_code("0010V0") == "0010V0"
    assert P.batches(["a", "b", "c"], 2) == ["a|b", "c"]


def test_pick_rows_keeps_only_target_rows() -> None:
    assert P.pick_rows("ka10060", [{"dt": T}, {"dt": "20261002"}], T) == [{"dt": T}]
    assert P.pick_rows("ka10086", [{"date": "20261002"}], T) == []
    assert P.latest_date("ka10060", [{"dt": "20261002"}, {"dt": "20261001"}]) == "20261002"


def test_settle_time_is_first_time_value_stays_final() -> None:
    s = [("15:25", 1), ("15:31", 2), ("15:40", 3), ("15:45", 3), ("16:00", 3)]
    assert P.settle_time(s) == "15:40"
    assert P.settle_time([("15:25", 3), ("15:31", 2), ("15:45", 3)]) == "15:45"
    assert P.settle_time([]) is None


def _row(prc: str, flows: dict[str, str]) -> dict[str, str]:
    return {"dt": T, "cur_prc": prc, **flows}


def _make(tmp: Path) -> tuple[Path, Path, Path]:
    db = tmp / "postclose.db"
    con = P.connect(db)
    flows_mid = {"ind_invsr": "3", "frgnr_invsr": "-1", "orgn": "-2"}
    for ts, prc_a, fl in (("15:25", "-101", flows_mid), ("15:31", "-100", flows_mid),
                          ("15:45", "-100", FLOWS_A), ("16:01", "-100", FLOWS_A),
                          ("16:10", "+103", FLOWS_A)):                 # 16:10 애프터마켓 체결
        stamp = f"2026-10-06T{ts}:05"
        for tk, prc in (("005930", prc_a), ("000660", "-50")):
            con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                        (stamp, "minute", "ka10060", tk, T, json.dumps([_row(prc, fl)])))
            con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                        (stamp, "minute", "ka10095", tk, T,
                         json.dumps({"stk_cd": tk, "cur_prc": prc, "close_pric": "-100"
                                     if tk == "005930" else "-50", "base_pric": "99"})))
    for ts in ("16:01", "16:10"):                                  # NXT — 애프터마켓 가격
        con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                    (f"2026-10-06T{ts}:09", "minute", "ka10060", "005930_NX", T,
                     json.dumps([_row("+104", FLOWS_A)])))
    for tk, prc in (("005930", "-100"), ("000660", "-50"), ("035720", "-7")):
        con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                    ("2026-10-06T16:00:30", "sweep@1600", "ka10060", tk, T,
                     json.dumps([_row(prc, FLOWS_A)])))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("sweep@1600", "ka10060", T, "2026-10-06T16:00:00", "2026-10-06T16:02:30", 3, 3, 0,
                 1, ""))
    con.commit()
    con.close()
    krx = tmp / "krx.db"
    k = sqlite3.connect(krx)
    for table in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
        k.execute(f"CREATE TABLE {table} "
                  "(BAS_DD TEXT, ISU_CD TEXT, TDD_CLSPRC TEXT, ACC_TRDVOL TEXT)")
    k.executemany("INSERT INTO krx_stk_bydd_trd VALUES (?,?,?,?)",
                  [(T, "005930", "100", "1000"), (T, "000660", "50", "500"),
                   (T, "035720", "7", "70")])
    k.commit()
    k.close()
    kw = tmp / "kiwoom.db"
    w = sqlite3.connect(kw)
    w.execute("CREATE TABLE ka10060_investor_flows (ticker TEXT, dt TEXT, cur_prc TEXT, "
              + ", ".join(f"{c} TEXT" for c in P.FLOW_KEYS) + ")")
    after = {**dict.fromkeys(P.FLOW_KEYS, "None"),
             "ind_invsr": "12", "frgnr_invsr": "-5", "orgn": "-7"}
    w.execute("INSERT INTO ka10060_investor_flows VALUES (?,?,?," + ",".join("?" * len(P.FLOW_KEYS))
              + ")", ("005930", T, "+103", *[after[c] for c in P.FLOW_KEYS]))
    w.commit()
    w.close()
    return db, krx, kw


def test_grade_reads_price_window_flow_settle_and_sweep(tmp_path: Path) -> None:
    db, krx, kw = _make(tmp_path)
    rep = P.grade(T, db=db, krx_db=krx, kw_db=kw)
    cur = rep["minute_price"]["KRX.ka10060.cur_prc"]
    assert cur["first_all_match"] == "15:31"                 # 15:25 장중 → 15:31 공식 종가
    assert cur["first_mismatch_after_1600"] == "16:10"       # 애프터마켓 체결로 달라짐
    assert rep["minute_price"]["KRX.ka10095.close_pric"]["first_mismatch_after_1600"] is None
    nxt = rep["minute_price"]["NXT.ka10060.cur_prc"]                   # 거래소별로 따로 센다
    assert nxt["n_tickers"] == 1 and nxt["first_all_match"] is None
    assert set(rep["minute_flows"]) == {"KRX", "NXT"}
    flows = rep["minute_flows"]["KRX"]
    assert flows["settle_by_ticker"]["005930"] == "15:45" and flows["settle_max"] == "15:45"
    assert flows["n_with_ledger"] == 1 and flows["same_as_ledger"] == 0   # 원장엔 애프터마켓분
    sweep = rep["sweeps"]["sweep@1600/ka10060/KRX"]
    assert (sweep["seconds"], sweep["n_t_rows"], sweep["close_match"], sweep["n_rate"]) == (
        150, 3, 3, 1)
    assert rep["sweeps"]["last_sweep == ledger(21:05)"] == {"n": 1, "flows_equal": 0}
    md = P.report_md(rep)
    assert "15:45" in md and "16:10" in md


def test_main_skips_outside_window_without_calling_kiwoom(monkeypatch, capsys) -> None:
    class Cal:
        def is_trading_day(self, d):
            return False

        def prev_trading_day(self, d):
            return d

    monkeypatch.setattr(P.trading_calendar, "load", lambda: Cal())
    monkeypatch.setattr(P, "_client", lambda: (_ for _ in ()).throw(AssertionError("no call")))
    assert P.main(["minute"]) == 0
    assert "건너뜀" in capsys.readouterr().out


def test_sample_is_fixed_three_plus_evenly_spread_rest() -> None:
    """후보 100종목 = 고정 3 + 나머지에서 같은 간격 97(매번 같은 종목)."""
    cands = [f"{i:06d}" for i in range(1, 627)] + list(P.TICKERS)
    got = P.sample(cands)
    assert len(got) == P.SWEEP_N == 100 and set(P.TICKERS) <= set(got)
    assert got == P.sample(list(reversed(cands)))                  # 입력 순서와 무관
    assert P.spread(["a", "b", "c", "d"], 2) == ["a", "c"] and P.spread(["a"], 5) == ["a"]


# ── 감사 수정 재현 테스트: L-01(채점 분모) · L-05(None 일치) · L-02(쓰기 잠금) ──────────────
def _krx_official(tmp: Path, closes: dict[str, str]) -> Path:
    """합성 krx.db — 종목별 공식 종가만 넣는다."""
    krx = tmp / "krx.db"
    k = sqlite3.connect(krx)
    for table in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
        k.execute(f"CREATE TABLE {table} "
                  "(BAS_DD TEXT, ISU_CD TEXT, TDD_CLSPRC TEXT, ACC_TRDVOL TEXT)")
    k.executemany("INSERT INTO krx_stk_bydd_trd VALUES (?,?,?,?)",
                  [(T, tk, close, "1000") for tk, close in closes.items()])
    k.commit()
    k.close()
    return krx


def _put(con: sqlite3.Connection, hms: str, run: str, api: str, key: str, payload: object) -> None:
    """obs 한 행 — `key` 는 collect 가 저장하는 키(종목코드 + 거래소 접미사)."""
    con.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,?,NULL)",
                (f"2026-10-06T{hms}", run, api, key, T, json.dumps(payload)))


@pytest.mark.parametrize(("ex", "sfx"), P.EXCHANGES)
def test_grade_minute_denominator_skips_ticker_with_no_t_row(
        tmp_path: Path, ex: str, sfx: str) -> None:
    """L-01: 그 거래소에 T 행이 한 번도 없는 종목(NXT 미상장 001820)은 채점 분모에서 뺀다.

    남은 두 종목이 매 분 공식 종가와 같으면 첫 분부터 전 종목 일치고 16:00 뒤 불일치는 없다.
    규칙은 거래소(KRX·NXT·SOR)와 TR 에 관계없이 같다."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    for hm in ("15:25", "15:35", "16:01", "16:10"):
        for tk, close in (("005930", "-100"), ("086520", "-200")):
            _put(con, f"{hm}:05", "minute", "ka10060", tk + sfx, [{"dt": T, "cur_prc": close}])
            _put(con, f"{hm}:06", "minute", "ka10086", tk + sfx,
                 [{"date": T, "close_pric": close}])
            _put(con, f"{hm}:07", "minute", "ka10095", tk + sfx,
                 {"stk_cd": tk, "cur_prc": close, "close_pric": close})
        # 미상장 — ka10060·ka10086 은 T 행 없음(빈 목록), ka10095 는 값이 빈 행
        _put(con, f"{hm}:05", "minute", "ka10060", "001820" + sfx, [])
        _put(con, f"{hm}:06", "minute", "ka10086", "001820" + sfx, [])
        _put(con, f"{hm}:07", "minute", "ka10095", "001820" + sfx,
             {"stk_cd": "001820", "cur_prc": "", "close_pric": ""})
    con.commit()
    con.close()
    krx = _krx_official(tmp_path, {"005930": "100", "086520": "200", "001820": "7000"})
    rep = P.grade(T, db=db, krx_db=krx, kw_db=tmp_path / "kiwoom.db")
    for key in ("ka10060.cur_prc", "ka10086.close_pric", "ka10095.cur_prc",
                "ka10095.close_pric"):
        got = rep["minute_price"][f"{ex}.{key}"]
        assert (got["n_tickers"], got["first_all_match"],
                got["first_mismatch_after_1600"]) == (2, "15:25", None), key


def test_grade_minute_keeps_ticker_whose_t_row_appears_later(tmp_path: Path) -> None:
    """L-01 경계: T 행이 한 번이라도 있는 종목은 분모에 남는다 — 값이 전부 None 인 종목만 뺀다."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    for hm, second in (("15:25", []), ("16:01", [{"dt": T, "cur_prc": "-200"}])):
        _put(con, f"{hm}:05", "minute", "ka10060", "005930", [{"dt": T, "cur_prc": "-100"}])
        _put(con, f"{hm}:05", "minute", "ka10060", "086520", second)   # 16:01 에야 T 행이 생긴다
    con.commit()
    con.close()
    krx = _krx_official(tmp_path, {"005930": "100", "086520": "200"})
    rep = P.grade(T, db=db, krx_db=krx, kw_db=tmp_path / "kiwoom.db")
    assert rep["minute_price"]["KRX.ka10060.cur_prc"]["n_tickers"] == 2


def test_grade_sweep_does_not_count_none_equals_none_as_close_match(tmp_path: Path) -> None:
    """L-05: 공식 종가가 없는 종목·stk_cd 빈 묶음 행은 값이 None 이어도 일치로 세지 않는다."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    run = "sweep@1601"
    # ka10060 sweep(KRX) — 777777 은 공식 종가표에 없고 현재가가 빈 값이다
    for tk, prc in (("005930", "-100"), ("000660", "-51"), ("777777", "")):
        _put(con, "16:01:30", run, "ka10060", tk, [{"dt": T, "cur_prc": prc}])
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run, "ka10060", T, "2026-10-06T16:01:00", "2026-10-06T16:02:00", 3, 3, 0, 0, ""))
    # ka10095 묶음 — 거래소마다 정상 1행 + 공식 종가가 없는 종목의 빈 값 행 + stk_cd 빈 행
    bundle = ({"stk_cd": "005930", "cur_prc": "-100", "close_pric": "-100", "base_pric": "99"},
              {"stk_cd": "888888", "cur_prc": "", "close_pric": "", "base_pric": ""},
              {"stk_cd": "", "cur_prc": "", "close_pric": "", "base_pric": ""})
    for _name, sfx in P.EXCHANGES:
        for r in bundle:
            _put(con, "16:01:40", run, "ka10095", P.norm_code(r["stk_cd"]) + sfx, r)
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run, "ka10095" + sfx, T, "2026-10-06T16:01:35", "2026-10-06T16:01:45",
                     1, 1, 0, 0, ""))
    con.commit()
    con.close()
    krx = _krx_official(tmp_path, {"005930": "100", "000660": "50"})
    rep = P.grade(T, db=db, krx_db=krx, kw_db=tmp_path / "kiwoom.db")
    sweeps = rep["sweeps"]
    got: dict[str, tuple[int, ...]] = {
        "ka10060/KRX": (sweeps[f"{run}/ka10060/KRX"]["n_t_rows"],
                        sweeps[f"{run}/ka10060/KRX"]["close_match"])}
    for name, _sfx in P.EXCHANGES:
        e = sweeps[f"{run}/ka10095/{name}"]
        got[f"ka10095/{name}"] = (e["n_rows"], e["cur_prc_match_close"],
                                  e["close_pric_match_close"], e["base_pric_match_close"])
    # 빈 행 둘도 n_rows 에는 들어 있다 — 일치로만 세지 않는다
    assert got == {"ka10060/KRX": (3, 1), "ka10095/KRX": (3, 1, 1, 0),
                   "ka10095/NXT": (3, 1, 1, 0), "ka10095/SOR": (3, 1, 1, 0)}


@pytest.mark.parametrize(("closes", "matches"), [
    ({}, {"15:20": 0, "15:25": 0, "15:30": 0}),
    ({"005930": "100"}, {"15:20": 0, "15:25": 1, "15:30": 1})],
    ids=["empty-table", "ticker-missing"])
def test_grade_minute_does_not_count_none_equals_none_when_official_close_is_missing(
        tmp_path: Path, closes: dict[str, str], matches: dict[str, int]) -> None:
    """L-05(분 칸): 공식 종가가 없는 종목은 값이 None 이어도 일치로 세지 않는다.

    09:20 채점 때 krx.db 에 T 행이 아직 없으면 공식 종가표가 비어 있다(`empty-table`).
    일부 칸만 None 인 종목은 L-01 필터(값이 전부 None 인 종목만 뺀다)를 지나가므로 그 칸이
    None == None 으로 '일치'가 되고, 첫 전 종목 일치 시각이 거짓으로 찍힌다.
    `ticker-missing` 은 표에 일부 종목만 있는 경우다."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    # (칸, 005930 의 T 행, 086520 의 T 행) — 15:20 은 둘 다 T 행이 없고, 15:25 는 086520 만 없다
    for hm, first, second in (
            ("15:20", [], []),
            ("15:25", [{"dt": T, "cur_prc": "-100"}], []),
            ("15:30", [{"dt": T, "cur_prc": "-100"}], [{"dt": T, "cur_prc": "-200"}])):
        _put(con, f"{hm}:05", "minute", "ka10060", "005930", first)
        _put(con, f"{hm}:05", "minute", "ka10060", "086520", second)
    con.commit()
    con.close()
    krx = _krx_official(tmp_path, closes)
    rep = P.grade(T, db=db, krx_db=krx, kw_db=tmp_path / "kiwoom.db")
    got = rep["minute_price"]["KRX.ka10060.cur_prc"]
    assert rep["n_official"] == len(closes)
    assert (got["n_tickers"], got["first_all_match"], got["match_by_minute"]) == (2, None, matches)


@pytest.mark.parametrize(("api_id", "status"), [
    ("ka10060", P.KW.CallStatus.OK),
    ("ka10095", P.KW.CallStatus.OK),
    ("ka10060", P.KW.CallStatus.ERROR)], ids=["t-row", "bundle", "error-row"])
def test_collect_does_not_hold_write_lock_between_calls(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_id: str, status: P.KW.CallStatus
) -> None:
    """L-02: collect 가 반복 전체를 트랜잭션 하나로 잡으면, 같은 DB 에 쓰는 다른 프로세스
    (minute·sweep)가 최대 60초 막힌다. 콜 사이에는 쓰기 잠금을 쥐지 않아야 한다."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    other: list[str] = []          # 두 번째 콜부터 시도한 다른 연결의 쓰기 결과
    calls = 0

    def fake_call_tr(client: object, spec: object, ticker: str, end_dt: str) -> P.KW.CallOutcome:
        nonlocal calls
        calls += 1
        if calls > 1:
            side = sqlite3.connect(db, timeout=0.2)
            try:
                side.execute("INSERT INTO obs VALUES (?,?,?,?,?,1,NULL,NULL)",
                             ("2026-10-06T15:20:00", "other", "ka10060", "000000", T))
                side.commit()
                other.append("ok")
            except sqlite3.OperationalError as e:
                other.append(str(e))
            finally:
                side.close()
        rows: list[dict[str, object]] = (
            [{"dt": T, "cur_prc": "-100"}] if status is P.KW.CallStatus.OK else [])
        return P.KW.CallOutcome(status=status, rows=rows, code=None, detail="")

    monkeypatch.setattr(P.KW, "call_tr", fake_call_tr)
    monkeypatch.setattr(P.KW, "RATE_PER_SEC", 1e9)               # 콜 간격 대기를 없앤다
    stats = P.collect(con, P.CountingClient(None), "minute", api_id, ["a", "b", "c"], T)
    con.close()
    assert stats["n_req"] == 3
    assert other == ["ok", "ok"]
