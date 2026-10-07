"""장 마감 직후 프로브(`src/probe_postclose.py`) — 행 고르기·표본·확정 시각·채점(합성 DB 왕복).

키움을 부르지 않는다. 채점은 합성 postclose.db · krx.db · kiwoom.db 로 판독 셋
(가격 일치 시각, 수급 확정 시각,
후보 전량 소요)을 본다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

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
        for tk, prc in (("005930", prc_a), ("000660", "-50")):
            _put(con, f"{ts}:05", "minute", "ka10060", tk, [_row(prc, fl)])
            _put(con, f"{ts}:05", "minute", "ka10095", tk,
                 {"stk_cd": tk, "cur_prc": prc, "close_pric": "-100" if tk == "005930" else "-50",
                  "base_pric": "99"})
    for ts in ("16:01", "16:10"):                                  # NXT — 애프터마켓 가격
        _put(con, f"{ts}:09", "minute", "ka10060", "005930_NX", [_row("+104", FLOWS_A)])
    for tk, prc in (("005930", "-100"), ("000660", "-50"), ("035720", "-7")):
        _put(con, "16:00:30", "sweep@1600", "ka10060", tk, [_row(prc, FLOWS_A)])
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("sweep@1600", "ka10060", T, "2026-10-06T16:00:00", "2026-10-06T16:02:30", 3, 3, 0,
                 1, ""))
    con.commit()
    con.close()
    krx = _krx_official(tmp, {"005930": "100", "000660": "50", "035720": "7"})
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
    # 15:25 장중 → 15:31 공식 종가. 칸은 실행 회차라 15:31:05 관측은 15:30 회차다(L-03)
    assert cur["first_all_match"] == "15:30"
    assert cur["first_mismatch_after_1600"] == "16:10"       # 애프터마켓 체결로 달라짐
    assert cur["switch_window"] == {"last_mismatch": "15:25:05", "all_matched_at": "15:31:05"}
    assert cur["leave_window"] == {"last_match": "16:01:05", "first_mismatch": "16:10:05"}
    close = rep["minute_price"]["KRX.ka10095.close_pric"]
    assert close["first_mismatch_after_1600"] is None
    # 첫 관측부터 일치 — 전환 구간은 관측 전부터 열려 있다(마지막 불일치 없음)
    assert close["switch_window"] == {"last_mismatch": None, "all_matched_at": "15:25:05"}
    nxt = rep["minute_price"]["NXT.ka10060.cur_prc"]                   # 거래소별로 따로 센다
    assert nxt["n_tickers"] == 1 and nxt["first_all_match"] is None
    # 공식 종가를 준 적이 없어 이탈 구간 앞끝을 모른다
    assert nxt["leave_window"] == {"last_match": None, "first_mismatch": "16:01:09"}
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
    assert ("| KRX.ka10060.cur_prc | 2 | 15:30 | 16:10 | 15:25:05 ~ 15:31:05 | "
            "16:01:05 ~ 16:10:05 |") in md                                  # 종목 수(M-4)
    assert "| KRX.ka10095.close_pric | 2 | 15:25 | None | 관측 전 ~ 15:25:05 | None |" in md


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


def _price(con: sqlite3.Connection, hms: str, api: str, key: str, prc: str | None) -> None:
    """minute 가격 관측 한 행을 TR 별 응답 모양으로 넣는다 — `prc` None 은 T 행 없음
    (ka10060·ka10086 은 빈 목록, ka10095 는 값이 빈 행)."""
    if api == "ka10095":
        payload: object = {"stk_cd": key, "cur_prc": prc or "", "close_pric": prc or ""}
    else:
        date_key, field = ("dt", "cur_prc") if api == "ka10060" else ("date", "close_pric")
        payload = [] if prc is None else [{date_key: T, field: prc}]
    _put(con, hms, "minute", api, key, payload)


def _grade(tmp: Path, closes: dict[str, str]) -> dict[str, Any]:
    """`tmp` 의 합성 postclose.db 를 공식 종가 `closes` 와 대조해 채점한다(원장 kiwoom.db 없음)."""
    return P.grade(T, db=tmp / "postclose.db", krx_db=_krx_official(tmp, closes),
                   kw_db=tmp / "kiwoom.db")


@pytest.mark.parametrize(("ex", "sfx"), P.EXCHANGES)
def test_grade_minute_denominator_skips_ticker_with_no_t_row(
        tmp_path: Path, ex: str, sfx: str) -> None:
    """L-01: 그 거래소에 T 행이 한 번도 없는 종목(NXT 미상장 001820)은 채점 분모에서 뺀다.

    남은 두 종목이 매 분 공식 종가와 같으면 첫 분부터 전 종목 일치고 16:00 뒤 불일치는 없다.
    규칙은 거래소(KRX·NXT·SOR)와 TR 에 관계없이 같다."""
    con = P.connect(tmp_path / "postclose.db")
    for hm in ("15:25", "15:35", "16:01", "16:10"):
        # 001820 은 미상장 — ka10060·ka10086 은 T 행 없음(빈 목록), ka10095 는 값이 빈 행
        for tk, close in (("005930", "-100"), ("086520", "-200"), ("001820", None)):
            for sec, api in ((5, "ka10060"), (6, "ka10086"), (7, "ka10095")):
                _price(con, f"{hm}:{sec:02d}", api, tk + sfx, close)
    con.commit()
    con.close()
    rep = _grade(tmp_path, {"005930": "100", "086520": "200", "001820": "7000"})
    for key in ("ka10060.cur_prc", "ka10086.close_pric", "ka10095.cur_prc",
                "ka10095.close_pric"):
        got = rep["minute_price"][f"{ex}.{key}"]
        assert (got["n_tickers"], got["first_all_match"],
                got["first_mismatch_after_1600"]) == (2, "15:25", None), key


def test_grade_minute_keeps_ticker_whose_t_row_appears_later(tmp_path: Path) -> None:
    """L-01 경계: T 행이 한 번이라도 있는 종목은 분모에 남는다 — 값이 전부 None 인 종목만 뺀다."""
    con = P.connect(tmp_path / "postclose.db")
    for hm, second in (("15:25", None), ("16:01", "-200")):
        _price(con, f"{hm}:05", "ka10060", "005930", "-100")
        _price(con, f"{hm}:05", "ka10060", "086520", second)   # 16:01 에야 T 행이 생긴다
    con.commit()
    con.close()
    rep = _grade(tmp_path, {"005930": "100", "086520": "200"})
    assert rep["minute_price"]["KRX.ka10060.cur_prc"]["n_tickers"] == 2


def test_grade_sweep_does_not_count_none_equals_none_as_close_match(tmp_path: Path) -> None:
    """L-05: 공식 종가가 없는 종목·stk_cd 빈 묶음 행은 값이 None 이어도 일치로 세지 않는다.

    L-01 의 sweep 판(값 있는 행 수 n_value)도 함께 고정한다."""
    con = P.connect(tmp_path / "postclose.db")
    run = "sweep@1601"
    # ka10060 sweep(KRX) — 777777 은 공식 종가표에 없고 현재가가 빈 값이다
    for tk, prc in (("005930", "-100"), ("000660", "-51"), ("777777", "")):
        _put(con, "16:01:30", run, "ka10060", tk, [{"dt": T, "cur_prc": prc}])
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run, "ka10060", T, "2026-10-06T16:01:00", "2026-10-06T16:02:00", 3, 3, 0, 0, ""))
    # ka10095 묶음 — 거래소마다 정상 1행 + 공식 종가가 없는 종목의 빈 값 행 + stk_cd 빈 행
    # + 현재가만 있는 행(필드마다 값 유무가 달라야 n_value 가 필드별로 세는지 가려진다)
    bundle = ({"stk_cd": "005930", "cur_prc": "-100", "close_pric": "-100", "base_pric": "99"},
              {"stk_cd": "888888", "cur_prc": "", "close_pric": "", "base_pric": ""},
              {"stk_cd": "", "cur_prc": "", "close_pric": "", "base_pric": ""},
              {"stk_cd": "000660", "cur_prc": "-51", "close_pric": "", "base_pric": ""})
    for _name, sfx in P.EXCHANGES:
        for r in bundle:
            _put(con, "16:01:40", run, "ka10095", P.norm_code(r["stk_cd"]) + sfx, r)
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run, "ka10095" + sfx, T, "2026-10-06T16:01:35", "2026-10-06T16:01:45",
                     1, 1, 0, 0, ""))
    con.commit()
    con.close()
    rep = _grade(tmp_path, {"005930": "100", "000660": "50"})
    sweeps = rep["sweeps"]
    got: dict[str, tuple[int, ...]] = {
        "ka10060/KRX": (sweeps[f"{run}/ka10060/KRX"]["n_t_rows"],
                        sweeps[f"{run}/ka10060/KRX"]["close_match"])}
    n_value: dict[str, tuple[int, ...]] = {}
    for name, _sfx in P.EXCHANGES:
        e = sweeps[f"{run}/ka10095/{name}"]
        got[f"ka10095/{name}"] = (e["n_rows"], e["cur_prc_match_close"],
                                  e["close_pric_match_close"], e["base_pric_match_close"])
        n_value[name] = (e["cur_prc_n_value"], e["close_pric_n_value"], e["base_pric_n_value"])
    # 빈 행 둘도 n_rows 에는 들어 있다 — 일치로만 세지 않는다
    assert got == {"ka10060/KRX": (3, 1), "ka10095/KRX": (4, 1, 1, 0),
                   "ka10095/NXT": (4, 1, 1, 0), "ka10095/SOR": (4, 1, 1, 0)}
    # n_rows 는 빈 값 행까지 센 원시값이고, n_value 는 필드마다 값이 있는 행만 센다.
    # 그래서 일치 수 ÷ n_value 가 '그 필드에 값이 있는 행 중'의 비율이다(L-01 의 sweep 판)
    assert n_value == {"KRX": (2, 1, 1), "NXT": (2, 1, 1), "SOR": (2, 1, 1)}


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
    con = P.connect(tmp_path / "postclose.db")
    # (칸, 005930 의 현재가, 086520 의 현재가) — None 은 T 행 없음. 15:20 은 둘 다 없고,
    # 15:25 는 086520 만 없다
    for hm, first, second in (("15:20", None, None), ("15:25", "-100", None),
                              ("15:30", "-100", "-200")):
        _price(con, f"{hm}:05", "ka10060", "005930", first)
        _price(con, f"{hm}:05", "ka10060", "086520", second)
    con.commit()
    con.close()
    rep = _grade(tmp_path, closes)
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
    (minute·sweep)가 최대 60초 막힌다. 콜 사이에는 쓰기 잠금을 쥐지 않아야 하고, 앞 콜의
    기록은 커밋되어 있어야 한다(rollback 으로 잠금만 푸는 변이를 잡는다)."""
    db = tmp_path / "postclose.db"
    con = P.connect(db)
    other: list[str] = []          # 두 번째 콜부터 시도한 다른 연결의 쓰기 결과
    visible: list[int] = []        # 같은 시점에 다른 연결에서 보이는 collect 의 기록 행 수
    calls = 0

    def fake_call_tr(client: object, spec: object, ticker: str, end_dt: str) -> P.KW.CallOutcome:
        nonlocal calls
        calls += 1
        if calls > 1:
            side = sqlite3.connect(db, timeout=0.2)
            try:
                visible.append(
                    side.execute("SELECT COUNT(*) FROM obs WHERE run = 'minute'").fetchone()[0])
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
    assert visible == [1, 2]       # 콜마다 한 행씩 — 앞 콜의 기록이 이미 커밋되어 보인다


# ── 감사 수정 재현 테스트: L-03(칸 = 실행 회차, 관측 없음 ≠ 불일치) · L-04(전환 구간) · M-5 ──────
# minute 크론 회차(15:20~16:30, 5분마다 :00 에 시작)와 고정 3종목의 공식 종가
SLOTS = [f"15:{m:02d}" for m in range(20, 60, 5)] + [f"16:{m:02d}" for m in range(0, 35, 5)]
CLOSES = {"005930": "100", "086520": "200", "001820": "300"}


@pytest.mark.parametrize("late", ["15:35", "15:45", "16:00"])
def test_grade_minute_cell_is_the_run_even_when_it_crosses_a_minute(
        tmp_path: Path, late: str) -> None:
    """L-03 ①: 칸은 실행 회차(관측 시각을 5분 격자로 내림)다 — 응답이 늦어 회차가 분 경계를
    넘어도 다음 칸으로 밀리지 않는다.

    3종목이 15:35 회차부터 매 회차 공식 종가와 같고, 수급은 `late` 회차부터 마지막 값이다.
    `late` 회차는 첫 종목만 :59 에, 나머지는 다음 분 :00·:01 에 찍힌다. 분(`ts[11:16]`)으로
    묶으면 그 회차가 두 칸으로 쪼개져 어느 칸도 전 종목이 아니다 — 15:35 면 '첫 전 종목 일치'가
    15:40 으로 늦고, 16:00 이면 거짓 '16:00 뒤 첫 불일치'가 찍히고, 15:45 면 15:45·15:46 두 칸이
    생긴다. 수급 확정 시각도 다음 분으로 찍힌다."""
    con = P.connect(tmp_path / "postclose.db")
    for slot in SLOTS:
        nxt = f"{slot[:3]}{int(slot[3:]) + 1:02d}"
        for i, (tk, close) in enumerate(CLOSES.items()):
            hms = f"{slot}:{i + 1:02d}"
            if slot == late:
                hms = f"{slot}:59" if i == 0 else f"{nxt}:{i - 1:02d}"
            _put(con, hms, "minute", "ka10060", tk,
                 [{"dt": T, "cur_prc": close if slot >= "15:35" else "1",
                   "ind_invsr": "2" if slot >= late else "1"}])
    con.commit()
    con.close()
    rep = _grade(tmp_path, CLOSES)
    got = rep["minute_price"]["KRX.ka10060.cur_prc"]
    assert (got["first_all_match"], got["first_mismatch_after_1600"]) == ("15:35", None)
    assert got["match_by_minute"] == {s: 3 if s >= "15:35" else 0 for s in SLOTS}
    assert rep["minute_flows"]["KRX"]["settle_by_ticker"] == dict.fromkeys(CLOSES, late)


@pytest.mark.parametrize("abort", ["error", "token"])
def test_grade_minute_dropped_ticker_is_missing_not_mismatch(tmp_path: Path, abort: str) -> None:
    """L-03 ②: 회차 안에서 빠진 종목은 그 칸에서 '관측 없음'이지 불일치가 아니다.

    3종목이 15:35 회차부터 매 회차 공식 종가와 같은데 15:35·16:00 회차에서 086520 콜이 실패한다.
    - error: call_tr ERROR(유량 재시도 소진) — ok=0 행이라 채점이 거른다
    - token: TOKEN 으로 collect 가 멈춘다 — ok=0 행 뒤 001820 은 관측 행조차 없다(부분 기록)
    '첫 전 종목 일치'는 모든 종목이 관측된 칸만 될 수 있어 15:40 이고 '16:00 뒤 첫 불일치'는
    없다. 지금 코드는 그 칸의 일치 수가 종목 수보다 작다는 것만 보고 16:00 을 불일치로 찍는다."""
    con = P.connect(tmp_path / "postclose.db")
    for slot in SLOTS:
        for i, (tk, close) in enumerate(CLOSES.items()):
            hms = f"{slot}:{i + 1:02d}"
            if slot in ("15:35", "16:00") and tk == "086520":
                # collect 의 실패 기록과 같은 모양 — ok=0, row_json 없음, err '<상태> <사유>'
                con.execute("INSERT INTO obs VALUES (?,?,?,?,?,0,NULL,?)",
                            (f"2026-10-06T{hms}", "minute", "ka10060", tk, T, f"{abort} 시험"))
                if abort == "token":
                    break                       # collect 중단 — 남은 종목은 관측 행이 없다
                continue
            _price(con, hms, "ka10060", tk, close if slot >= "15:35" else "1")
    con.commit()
    con.close()
    got = _grade(tmp_path, CLOSES)["minute_price"]["KRX.ka10060.cur_prc"]
    assert (got["first_all_match"], got["first_mismatch_after_1600"]) == ("15:40", None)
    lost = 1 if abort == "error" else 2         # 16:00 칸에서 관측 없는 종목 수
    assert (got["match_by_minute"]["16:00"], got["mismatch_by_minute"]["16:00"],
            got["missing_by_minute"]["16:00"]) == (3 - lost, 0, lost)


def test_report_marks_switch_windows_that_overlap_within_one_run(tmp_path: Path) -> None:
    """L-04: minute 은 한 회차(약 7초) 안에서 KRX→NXT→SOR, ka10060→ka10086→ka10095 순서로 쏜다.
    종가 반영이 그 몇 초 사이에 일어나면 먼저 부른 TR·거래소는 다음 칸에 첫 일치로 찍힌다.

    15:30 회차에서 KRX ka10060(15:30:01)은 아직 장중 값, SOR ka10095(15:30:07)는 공식 종가다 —
    칸만 보면 'SOR 묶음이 5분 먼저 준다'지만 반영은 15:30:01~15:30:07 어디서든 일어날 수 있다.
    전환 구간(마지막 불일치 ~ 첫 일치, 초)을 싣고, 칸이 다른데 구간이 겹치면 '같은 회차 안 호출
    순서 차이 — 선후 판정 불가'로 적는다. 대조: KRX ka10086(15:30:02 일치)과 NXT ka10086
    (15:30:05 불일치)은 구간이 겹치지 않는다 — KRX 가 먼저 준 것이 확실하므로 적지 않는다."""
    con = P.connect(tmp_path / "postclose.db")
    # (TR, 거래소 접미사, 회차 안 관측 초, 공식 종가를 처음 준 회차)
    for api, sfx, sec, first in (("ka10060", "", 1, "15:35"), ("ka10086", "", 2, "15:30"),
                                 ("ka10086", "_NX", 5, "15:35"), ("ka10095", "_AL", 7, "15:30")):
        for slot in ("15:25", "15:30", "15:35"):
            for tk, close in CLOSES.items():
                _price(con, f"{slot}:{sec:02d}", api, tk + sfx, close if slot >= first else "1")
    con.commit()
    con.close()
    rep = _grade(tmp_path, CLOSES)
    phrase = "같은 회차 안 호출 순서 차이 — 선후 판정 불가"
    _, _, switch_part = P.report_md(rep).partition("### 전환 구간")
    flagged = [ln for ln in switch_part.split("\n### ")[0].splitlines()
               if ln.startswith("- ") and ln.endswith(phrase)]
    assert any("KRX.ka10060.cur_prc" in ln and "SOR.ka10095.cur_prc" in ln for ln in flagged)
    assert not any("KRX.ka10086.close_pric" in ln and "NXT.ka10086.close_pric" in ln
                   for ln in flagged)
    got = {key: (v["first_all_match"], v["switch_window"])
           for key, v in rep["minute_price"].items()}
    assert got["KRX.ka10060.cur_prc"] == (
        "15:35", {"last_mismatch": "15:30:01", "all_matched_at": "15:35:01"})
    assert got["SOR.ka10095.cur_prc"] == (
        "15:30", {"last_mismatch": "15:25:07", "all_matched_at": "15:30:07"})
    # all_matched_at = 첫 전 종목 일치 칸에서 전 종목 일치를 확인한 시각(그 칸의 마지막 일치 관측)
    assert P.grade_minute_prices({"a": [("15:30:01", 1)], "b": [("15:30:03", 2)]},
                                 {"a": 1, "b": 2})["switch_window"]["all_matched_at"] == "15:30:03"


def test_grade_minute_without_official_closes_is_ungradable(tmp_path: Path) -> None:
    """M-5: 09:20 채점 때 krx.db 에 T 행이 아직 없으면 공식 종가표가 빈다(n_official 0). 그때 가격
    칸은 일치도 불일치도 아니다 — 지금 코드는 16시대 첫 칸을 '16:00 뒤 첫 불일치'로 찍는다
    (일치 0 < 종목 3). 보고서는 '공식 종가 없음 — 채점 불가'로 적고 표에 종목 수를 싣는다(M-4)."""
    con = P.connect(tmp_path / "postclose.db")
    for slot in SLOTS:
        for i, (tk, close) in enumerate(CLOSES.items()):
            _price(con, f"{slot}:{i + 1:02d}", "ka10060", tk, close)
    con.commit()
    con.close()
    rep = _grade(tmp_path, {})
    got = rep["minute_price"]["KRX.ka10060.cur_prc"]
    assert got["first_mismatch_after_1600"] is None
    assert (got["n_tickers"], got["first_all_match"], got["switch_window"]) == (3, None, None)
    md = P.report_md(rep)
    assert "공식 종가 없음 — 채점 불가" in md
    assert "| KRX.ka10060.cur_prc | 3 | 채점 불가 | 채점 불가 | 채점 불가 | 채점 불가 |" in md


def test_report_marks_leave_windows_that_overlap_within_one_run(tmp_path: Path) -> None:
    """L-04(이탈 쪽): 16:00 회차 안에서 KRX ka10060(16:00:02)·ka10086(16:00:03)은 아직 공식 종가였고
    ka10095(16:00:04)는 애프터마켓 체결로 바뀌었다(10-06 실채점 모양). 칸만 보면 '16:00 뒤 첫
    불일치'가 ka10095 16:00 · ka10060 16:05 로 갈려 '묶음 TR 이 5분 먼저 바뀐다'로 읽히지만, 바뀐
    때는 16:00:02~16:00:04 어디든 될 수 있다. 이탈 구간(마지막 일치 ~ 첫 불일치, 초)을 싣고, 칸이
    다른데 겹치면 이탈 구간 목록에 '선후 판정 불가'로 적는다. 대조: NXT ka10060(16:00:05 아직 공식
    종가)은 KRX ka10095(16:00:04 이미 바뀜)보다 늦게 바뀐 것이 확실하므로 적지 않는다."""
    con = P.connect(tmp_path / "postclose.db")
    # (TR, 거래소 접미사, 회차 안 관측 초, 공식 종가에서 처음 벗어난 회차)
    for api, sfx, sec, leave in (("ka10060", "", 2, "16:05"), ("ka10086", "", 3, "16:05"),
                                 ("ka10095", "", 4, "16:00"), ("ka10060", "_NX", 5, "16:05")):
        for slot in ("15:55", "16:00", "16:05"):
            for tk, close in CLOSES.items():
                _price(con, f"{slot}:{sec:02d}", api, tk + sfx, close if slot < leave else "1")
    con.commit()
    con.close()
    rep = _grade(tmp_path, CLOSES)
    md = P.report_md(rep)
    phrase = "같은 회차 안 호출 순서 차이 — 선후 판정 불가"
    _, _, leave_part = md.partition("### 이탈 구간")
    flagged = [ln for ln in leave_part.split("\n## ")[0].splitlines()
               if ln.startswith("- ") and ln.endswith(phrase)]
    assert any("KRX.ka10060.cur_prc" in ln and "KRX.ka10095.cur_prc" in ln for ln in flagged)
    assert not any("NXT.ka10060.cur_prc" in ln and "KRX.ka10095.cur_prc" in ln for ln in flagged)
    got = {key: (v["first_mismatch_after_1600"], v["leave_window"])
           for key, v in rep["minute_price"].items()}
    assert got["KRX.ka10060.cur_prc"] == (
        "16:05", {"last_match": "16:00:02", "first_mismatch": "16:05:02"})
    assert got["KRX.ka10095.cur_prc"] == (
        "16:00", {"last_match": "15:55:04", "first_mismatch": "16:00:04"})
    # JSON 에도 md 목록과 같은 판정의 짝을 싣는다(3일 종합 정리가 json 을 읽는다)
    assert rep["minute_undetermined"] == {"switch": [], "leave": [
        ["KRX.ka10060.cur_prc", "KRX.ka10095.cur_prc"],
        ["KRX.ka10060.cur_prc", "KRX.ka10095.close_pric"],
        ["KRX.ka10086.close_pric", "KRX.ka10095.cur_prc"],
        ["KRX.ka10086.close_pric", "KRX.ka10095.close_pric"]]}
    assert ("| KRX.ka10060.cur_prc | 3 | 15:55 | 16:05 | 관측 전 ~ 15:55:02 | "
            "16:00:02 ~ 16:05:02 |") in md


def test_leave_window_starts_at_the_earliest_last_match_across_tickers() -> None:
    """이탈 구간 앞끝 = 종목마다 그 칸 앞에서 마지막으로 일치를 본 시각 중 가장 이른 것(어느 종목이
    먼저 벗어났는지 모른다). 16:05 회차에서 086520 이 16:05:02 에 벗어났어도 그 종목의 직전 일치는
    16:00:02 라 그 사이 언제든 벗어났을 수 있다 — 같은 회차의 다른 종목 일치(005930 16:05:01)로
    앞끝을 좁히면 거짓 선후가 나온다. 공식 종가를 한 번도 못 본 종목이 있으면 앞끝을 모른다
    (None)."""
    closes = {"005930": 100, "086520": 200, "001820": 300}
    per = {tk: [(f"15:55:0{i}", c), (f"16:00:0{i}", c),
                (f"16:05:0{i}", c + 1 if tk == "086520" else c)]
           for i, (tk, c) in enumerate(closes.items(), start=1)}
    got = P.grade_minute_prices(per, closes)
    assert (got["first_mismatch_after_1600"], got["leave_window"]) == (
        "16:05", {"last_match": "16:00:01", "first_mismatch": "16:05:02"})
    never = {**per, "001820": [("15:55:03", 1), ("16:00:03", 1), ("16:05:03", 1)]}
    assert P.grade_minute_prices(never, closes)["leave_window"] == {
        "last_match": None, "first_mismatch": "16:00:03"}
