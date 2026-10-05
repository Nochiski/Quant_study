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
