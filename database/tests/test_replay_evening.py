"""`scripts/replay.sh --basis evening` — 장 마감 판 과거 재생(컷오버 트랙 PR-8b · P5 실행기).

끝까지 도는 셸 테스트(①~⑥)는 **실물 빌더**로 돈다: HOME 을 임시 폴더로 바꿔 `~/quant-ledger`(운영
루트 대역)에 판정 달력·stage(fi 가 직접 읽는 WISE 3표)·21:05 키움 원장(`data/raw/kiwoom.db`)을
두고, 출력 루트에는 앞 패스가 지은 것처럼 equity 현판(`test_factor_inputs` 합성 트리 + T·T+1 세션
행)과 그 패스 기록(summary.tsv)을 둔 뒤 `--steps board` 로 돌린다. 운영 venv python 대역은 진짜
python 이고 `-m` 실행 앞에서 **합성 픽스처 크기만** 맞춘다(15종목이라 fi eligible 하한 300·모델
MG1/MG4 하한을 1 로, 운영 골든 종목이 없어 fi FG4 no_fixtures SKIP 허용 — `test_factor_inputs` 의
`allow_skips`·min_eligible 과 같은 조정). 빌드 코드·SQL·게이트는 그대로다.


합성 세계: D' = 2026-09-28(월) · T = 2026-09-29(화). 현판은 T+1(09-30)까지 왔다(그래서 `--replay`
세션 자르기 없이는 장 마감 판 MD-SEAM 이 서지 않고 연구 판 T 가격에 T+1 행이 섞인다).
21:05 원장 T 행:
  · 층 종목 대부분 — 종가 = KRX T 종가, 기준가(종가 − pred_pre) = D' KRX 종가(당일 기업행위 없음)
  · CLOSE_DIFF — 종가가 KRX 와 10원 다르다(애프터마켓 마지막 체결가 — 재생 대조의 '종가 정의')
  · L(대상)·K(대상, D' 가격 없음) — 원장 행 없음(장 마감 판에 T 가격 없음)
  · G(우선주 — 수집 대상 밖)·ETF — 원장에는 있지만 재생 원장에 옮기지 않는다
연속 이틀 변형(`_home(second_day=True)`): T+1 도 성공하는 세계 — 21:05 원장에 T+1 행(종가 = KRX T+1
종가, 기준가 = KRX T 종가), 현판에 T+1 수정주가·수급 행과 T+1 기준가, WISE 컨센서스(연간·매트릭스)에 T 수집분(D 수집분
사본 — 없으면 연구 판 T+1 이 FG-fresh 수집 지연 2세션으로 실패한다).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_factor_inputs as tf

DB = Path(__file__).resolve().parents[1]
SCRIPT = DB / "scripts" / "replay.sh"
TOOL = DB / "scripts" / "replay_tool.py"
EVT = DB / "scripts" / "replay_evening.py"
T, T_S = tf.T, tf.T_S                       # 2026-09-29
DP_S = tf.D_S                               # D' = 2026-09-28
T1 = dt.date(2026, 9, 30)                   # 현판이 온 마지막 세션(연속 이틀 변형에서만 재생 대상)
T1_S = "20260930"
EQ_CUR = "m_20261001T000500_000000Z"
ST_BUILD = tf.ST_BUILD
KW_STAMP = "2026-09-29T12:05:31"            # 21:05 KST(UTC) — 원장 행의 수집 시각
KW_STAMP_T1 = "2026-09-30T12:05:44"         # 연속 이틀 변형의 T+1 행
CLOSE_DIFF = tf.EXTRA[2]
NO_ROW = (tf.L, tf.K)                       # 수집 대상인데 21:05 원장에 행이 없다
NOT_TARGET = (tf.G, tf.ETF)                 # 21:05 원장에는 있지만 수집 대상 밖

# 운영 venv python 대역 — 진짜 python. `-m` 실행만 합성 픽스처 크기에 맞춰 하한을 낮춘다(빌드
# 코드는 그대로)
_SHIM = '''#!{real}
import os
import runpy
import sys

argv = sys.argv[1:]
with open(os.environ["QL_CALLS"], "a", encoding="utf-8") as f:
    f.write(" ".join(argv) + "\\n")
if argv[:1] != ["-m"]:
    os.execv(sys.executable, [sys.executable, *argv])
import factor_inputs.build  # noqa: E402,F401
from model import gates  # noqa: E402
from stage import skip_allow  # noqa: E402

skip_allow.ALLOW = skip_allow.ALLOW + (skip_allow.Allow(
    "factor_inputs", "FG4", "no_fixtures", "합성 트리에는 운영 골든 종목이 없다", ()),)
sys.modules["factor_inputs.build"].MIN_ELIGIBLE_DEFAULT = 1
gates.MIN_PRICES_ON_D = 1
gates.MIN_RANKED = 1
sys.argv = [argv[1], *argv[2:]]
runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
'''


def _close(t: str) -> int:
    """KRX T 종가 — D' 종가 + 300(전 종목 같은 움직임)."""
    return tf.D_CLOSE[t] + 300


def _kw_flows(t: str) -> list[int]:
    """21:05 원장 투자자 13열(백만원)."""
    return [(-1 if i == 0 else 1) * (tf.IDX[t] * 10 + i) for i in range(13)]


T_TICKERS = [t for t in tf.SPEC if t not in (tf.K, tf.DELISTED)]      # KRX T 가격이 있는 종목


def _current_rows(second_day: bool = False) -> dict[str, list[dict]]:
    """현판에만 있는 D' 뒤 행 — T 는 연구 판 T(KRX) 값, T+1 은 달력·유니버스·가격만(연속 이틀 변형은
    T+1 수정주가·수급도)."""
    out: dict[str, list[dict]] = {
        "trading_calendar": [{"date": T, "prev_td": tf.D, "next_td": T1},
                             {"date": T1, "prev_td": T, "next_td": None}],
        "universe_daily": [dict(r, date=d) for d in (T, T1) for r in tf._universe()],
        "price_daily": [], "price_adj_daily": [], "flow_daily": []}
    for t in T_TICKERS:
        for day, close in ((T, _close(t)), (T1, _close(t) + 50)):
            out["price_daily"].append(tf._price_row(t, day, close))
        for day, close in ((T, _close(t)), (T1, _close(t) + 50))[:2 if second_day else 1]:
            share, po = tf.ADJ_FACTOR.get(t, 1.0), tf._price_only(t, day)
            out["price_adj_daily"].append({"ticker": t, "date": day, "adj_close": close * share / po,
                                           "cum_share_factor": share, "cum_price_only_factor": po,
                                           "n_unadjusted_events": 0, "basis": "krx"})
            if t in (tf.ETF, tf.L):                # 21:05 수집 밖·원장 없음 — 연구 판 수급도 없다
                continue
            row: dict = {"date": day, "ticker": t, "src": "kiwoom"}
            for i, col in enumerate(tf._FLOW_COLS):
                row[col] = _kw_flows(t)[i] * 1_000_000
            out["flow_daily"].append(row)
    return out


def _with_krx_base(eq_root: Path) -> None:
    """현판 equity `price_daily` 에 KRX 기준가 열(`base_price_krw`)을 붙인다 — fi 는 읽지 않아 합성
    원천에 없고, 두 판 대조가 T-6 증거로 T 행을 읽는다(`test_board_compare._with_krx_base` 와 같은
    방식). 기업행위가 없으니 T 기준가 = D' 종가, T+1 기준가 = T 종가."""
    import duckdb
    part = eq_root / "price_daily" / f"v={EQ_CUR}" / "part0.parquet"
    vals = ", ".join(f"('{t}', {tf.D_CLOSE[t]}, {_close(t)})" for t in T_TICKERS)
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT p.*, CASE WHEN p.date = DATE '{T.isoformat()}' THEN m.b "
                    f"WHEN p.date = DATE '{T1.isoformat()}' THEN m.b1 END "
                    f"AS base_price_krw FROM read_parquet('{part}') p LEFT JOIN (VALUES {vals}) "
                    f"m(t, b, b1) ON m.t = p.ticker) TO '{part}.x' (FORMAT PARQUET)")
    finally:
        con.close()
    Path(f"{part}.x").replace(part)


def _kiwoom_db(path: Path, second_day: bool = False) -> None:
    """21:05 키움 원장 — 운영 수집기와 같은 표 규약(`kw_daily.ensure_table`). 연속 이틀 변형은 T+1
    행도(종가 = KRX T+1 종가, 전일 대비 = KRX T 종가 기준)."""
    from daily import kw_daily, postclose
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    kw_daily.ensure_table(con, postclose.TABLE, postclose.COLS)
    names = ["ticker", *postclose.COLS, "src_api", "collected_at"]
    days = ((T_S, tf.D_CLOSE.__getitem__, _close, KW_STAMP),
            (T1_S, _close, lambda t: _close(t) + 50, KW_STAMP_T1))[:2 if second_day else 1]
    for day, prev, krx, stamp in days:
        for t in T_TICKERS:
            if t in NO_ROW:
                continue
            close = krx(t) + (10 if t == CLOSE_DIFF else 0)
            move = close - prev(t)
            vals = [t, day, f"+{close}", f"{move:+d}", str(1_000 * tf.IDX[t]),
                    *[str(v) for v in _kw_flows(t)], "ka10060", stamp]
            con.execute(f'INSERT INTO "{postclose.TABLE}" ({",".join(names)}) VALUES '
                        f'({",".join("?" * len(names))})', vals)
    # 다른 날 행 — 재생은 dt=T 만 옮긴다
    con.execute(f'INSERT INTO "{postclose.TABLE}" (ticker, dt, cur_prc, collected_at) VALUES '
                "(?, ?, ?, ?)", (tf.A, DP_S, "+1", KW_STAMP))
    con.commit()
    con.close()


def _snapshot(root: Path) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", "")
            else:
                out[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _home(base: Path, second_day: bool = False) -> SimpleNamespace:
    """운영 루트 대역 + 앞 패스가 equity 를 지은 출력 루트. `second_day` 는 T+1 도 성공하는 연속 이틀
    변형(모듈 docstring)."""
    home = base / "home"
    ops = home / "quant-ledger"
    py = ops / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(_SHIM.replace("{real}", sys.executable), encoding="utf-8")
    py.chmod(0o755)
    eq, st = tf.make_roots(base / "src", eq_build=EQ_CUR, extra=_current_rows(second_day))
    _with_krx_base(eq)
    if second_day:
        # WISE 컨센서스 T 수집분(D 수집분 사본) — 같은 판(ST_BUILD)으로 두 표를 다시 세운다
        from conftest import _make_stage_tree
        for table, rows in (("stg_consensus_annual", tf._consensus_annual()),
                            ("stg_consensus_matrix", tf._matrix())):
            rows += [dict(r, fetched_date=T) for r in rows if r["fetched_date"] == tf.D]
            _make_stage_tree(base / "src2", table, rows, build_id=ST_BUILD)
            shutil.rmtree(st / table)
            shutil.copytree(base / "src2" / "stage" / table, st / table)
    shutil.copytree(st, ops / "data" / "stage")
    tf._cal_dir(ops / "data")
    _kiwoom_db(ops / "data" / "raw" / "kiwoom.db", second_day)
    out = home / "replay" / "ev"
    shutil.copytree(eq, out / "data" / "equity")
    (out / "logs" / "pass1").mkdir(parents=True)
    (out / "logs" / "pass1" / "summary.tsv").write_text(
        "step\ttable\trc\tsec\tbuild_id\tcontent_hash\tn_rows\n" + "".join(
            f"equity\t{t}\t0\t1\t{EQ_CUR}\th\t1\n" for t in sorted(p.name for p in eq.iterdir())),
        encoding="utf-8")
    return SimpleNamespace(home=home, ops=ops, out=out)


def _run(home: Path, *args: str) -> SimpleNamespace:
    calls = home / "calls.txt"
    calls.write_text("", encoding="utf-8")
    env = dict(os.environ, HOME=str(home), QL_CALLS=str(calls))
    for k in ("QL_HOME", "PYTHONPATH"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                       timeout=600, check=False)
    return SimpleNamespace(rc=p.returncode, out=p.stdout + p.stderr,
                           calls=calls.read_text(encoding="utf-8").splitlines())


@pytest.fixture(scope="module")
def replayed(tmp_path_factory) -> SimpleNamespace:
    w = _home(tmp_path_factory.mktemp("replay_ev").resolve())
    before = _snapshot(w.ops)
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S, "--steps", "board",
             "--code", str(DB), "--spearman-min", "0")
    return SimpleNamespace(**vars(w), r=r, before=before, after=_snapshot(w.ops),
                           mdb=w.out / "data" / "model_db", log=w.out / "logs" / "pass2")


def _tsv(path: Path) -> list[list[str]]:
    return [ln.split("\t") for ln in path.read_text(encoding="utf-8").splitlines()]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── ①~⑥ 한 날 끝까지 ─────────────────────────────────────────────────────────
def test_one_day_runs_every_step_in_order(replayed) -> None:
    """연구 판 D' → ② T 행 원천·stage → ③ fi 장 마감 판 → ④ 모델 → ⑤ 연구 판 T → ⑥ 대조 —
    전부 rc 0."""
    r = replayed.r
    steps = [(x[0], x[2]) for x in _tsv(replayed.log / "summary.tsv")[1:] if x[1] == "-"]
    want = ["fi_r@" + DP_S, "postclose@" + T_S, "pc_stage@" + T_S, "fi_e@" + T_S,
            "model_e@" + T_S, "fi_r@" + T_S, "model_r@" + T_S, "compare@" + T_S]
    assert [s for s, _ in steps] == want, r.out
    assert all(rc == "0" for _, rc in steps) and r.rc == 0, r.out
    # 표별 해시도 남는다(같은 루트 두 패스의 재현성 대조 — EG5a)
    rows = {(x[0], x[1]) for x in _tsv(replayed.log / "summary.tsv")[1:]}
    assert ("fi_e@" + T_S, "fi_universe") in rows and ("model_r@" + T_S, "scope@1.0") in rows


def test_operating_root_is_untouched_and_every_write_is_under_the_out_root(replayed) -> None:
    assert replayed.after == replayed.before
    out = str(replayed.out)
    for call in replayed.r.calls:
        a = call.split()
        for flag in ("--root", "--fi-root", "--equity-root", "--stage-root", "--snapshot-root",
                     "--evening-root", "--research-root", "--postclose-stage-root",
                     "--candidates-root", "--builds-from", "--out", "--base", "--dest", "--data"):
            if flag in a:
                v = a[a.index(flag) + 1]
                assert v.startswith(out + "/") or v == out, call
    assert f'rm -rf -- "{out}"' in replayed.r.out


def test_evening_board_pins_the_d_prime_research_board_with_a_session_cut(replayed) -> None:
    """③ 장 마감 판: 합성 인계 이력 `<D'>_morning.json`(이 패스 equity 현판 + 고정 stage 판)을
    고정하고 `--replay` 로 세션 축을 D' 에서 자른다 — 현판이 T+1 까지 왔어도 MD-SEAM 이 서고, T
    가격 행은 장 마감 원천 하나다."""
    run = _json(replayed.mdb / "factor_inputs" / "_runs" / f"{T_S}_evening.json")
    assert run["status"] == "ok" and run["asof"] == "2026-09-28"
    assert run["replay"]["session_cut"] == "2026-09-28"
    hist = replayed.out / "data" / "deliver" / "history" / f"{DP_S}_morning.json"
    assert run["builds_from"] == str(hist) and run["builds_from_date"] == DP_S
    assert run["postclose_stage_root"] == str(replayed.mdb / "stage")
    assert run["equity_builds"]["price_daily"] == EQ_CUR
    assert run["stage_root"] == str(replayed.out / "stage_pin" / "pass2")
    h = _json(hist)
    assert h["health"] == {"stage": "ok", "equity": "ok"} and "replay" in h
    # 선택 표(WISE 분기)는 운영에 현판이 없다
    assert h["stage_builds"] == {t: ST_BUILD for t in tf.ST_TABLES}
    # 연구 판 D'·T 도 각자 asof 에서 잘랐다
    for day, cut in ((DP_S, "2026-09-28"), (T_S, "2026-09-29")):
        rr = _json(replayed.out / "data" / "factor_inputs" / "_runs" / f"{day}_morning.json")
        assert rr["replay"]["session_cut"] == cut and rr["asof"] == cut


def test_t_rows_come_from_the_2105_ledger_only_for_collection_targets(replayed) -> None:
    """② 재생 T 행 = 21:05 원장(price_valid '1', 수집 시각 그대로) 중 수집기 대상 종목. 원장 밖
    종목(우선주· ETF)과 다른 날 행은 옮기지 않는다. 재생 표시는 `replay_source` 표."""
    db = replayed.out / "data" / "postclose_replay" / "pass2" / T_S / "postclose.db"
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT ticker, dt, cur_prc, price_valid, collected_at "
                           "FROM ka10060_investor_flows ORDER BY ticker").fetchall()
        mark = con.execute("SELECT dt, n_rows, n_missing, n_candidates_missing, d_prime "
                           "FROM replay_source").fetchall()
    finally:
        con.close()
    got = {r[0] for r in rows}
    assert got == set(T_TICKERS) - set(NO_ROW) - set(NOT_TARGET)
    assert {(r[1], r[3], r[4]) for r in rows} == {(T_S, "1", KW_STAMP)}
    assert {r[0]: r[2] for r in rows}[CLOSE_DIFF] == f"+{_close(CLOSE_DIFF) + 10}"
    # 대상 중 원장에 없는 것 = L · K · 상장폐지 종목(v3 유니버스 필터는 상태를 보지 않는다), 후보는
    # 다 있다
    assert mark == [(T_S, len(got), 3, 0, DP_S)]


def test_evening_t_prices_are_the_2105_values(replayed) -> None:
    """③ 장 마감 판 fi_prices T 행 = 재생 원장 값('postclose') — 연구 판 T(KRX)와 CLOSE_DIFF 만
    다르다."""
    sql = f"SELECT ticker, close, price_source FROM t WHERE date = DATE '{T.isoformat()}'"
    ev = {t: (c, s) for t, c, s in tf.q(replayed.mdb / "factor_inputs", "fi_prices", sql)}
    rs = {t: (c, s) for t, c, s in tf.q(replayed.out / "data" / "factor_inputs", "fi_prices", sql)}
    assert {s for _, s in ev.values()} == {"postclose"} and {s for _, s in rs.values()} == {"krx"}
    assert ev[CLOSE_DIFF][0] == rs[CLOSE_DIFF][0] + 10
    assert {t: c for t, (c, _) in ev.items() if t != CLOSE_DIFF} == {
        t: c for t, (c, _) in rs.items() if t in ev and t != CLOSE_DIFF}
    assert tf.L not in ev and tf.L in rs           # 원장 행 없음 → 장 마감 판 T 가격 없음
    # 연구 판 T 는 T 에서 잘랐다 — 현판의 T+1 행이 섞이지 않는다
    assert tf.q(replayed.out / "data" / "factor_inputs", "fi_prices",
                "SELECT max(date) FROM t") == [(T,)]


def test_board_summary_lists_the_day(replayed) -> None:
    rows = _tsv(replayed.log / "board.tsv")
    assert rows[0][:4] == ["date", "verdict", "rc", "n_unexplained"]
    assert rows[0][7:] == ["failed_steps", "n_missing", "n_candidates_missing", "reasons"]
    assert rows[1][0] == T_S
    # 실패 단계 없음 · 재생 원장 결손 = replay_source(대상 중 원장에 없는 L·K·상장폐지 3, 후보 0)
    assert rows[1][7:10] == ["-", "3", "0"]
    assert rows[-1][0].startswith("대조 1일")
    line = (replayed.log / "summary.txt").read_text(encoding="utf-8")
    for word in ("basis=evening", f"D'={DP_S}", f"T={T_S}..{T_S}(1일)", "대조 1일"):
        assert word in line


def test_compare_runs_in_replay_mode_and_explains_every_difference(replayed) -> None:
    """⑥ 두 판 대조는 재생 모드다(`replay: true`) — T 종가 차이(애프터마켓)는 '종가 정의', 원장 행
    없는 대상 종목은 16:00 컷오프 자리, 대상 밖 종목은 '수집 대상 밖'. 미설명 0."""
    rep = _json(replayed.mdb / "compare" / f"{T_S}.json")
    assert rep["replay"] is True and rep["verdict"] == "pass" and rep["n_unexplained"] == 0, rep
    tick = rep["ticker_categories"]
    assert "close_definition" in tick[CLOSE_DIFF]
    assert tick[tf.L] == ["postclose_cutoff"]
    assert tick[tf.G] == ["not_targeted"]
    assert _tsv(replayed.log / "board.tsv")[1][1] == "pass"


def test_dates_range_keeps_going_past_a_failed_day_and_a_second_pass_reproduces(tmp_path) -> None:
    """--dates 는 T 마다 차례로 돈다. T+1(09-30)은 21:05 원장에 행이 없어 ② 가 rc 2 — 그날 ③④⑥ 은
    건너뛰고 ⑤(연구 판, 다음 T 의 D')는 돈다. 대조 집계에는 '없음'이고 전체 rc 1. 같은 루트를 다시
    돌리면 새 패스 경로(stage_pin·postclose_replay)에 짓고 장 마감 판 fi 해시가 같다(EG5a)."""
    w = _home(tmp_path.resolve())
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--dates", f"{T_S}-20260930",
             "--steps", "board", "--code", str(DB), "--spearman-min", "0")
    assert r.rc == 1, r.out
    log = w.out / "logs" / "pass2"
    rc = {x[0]: x[2] for x in _tsv(log / "summary.tsv")[1:] if x[1] == "-"}
    assert rc["postclose@20260930"] == "2"
    for s in ("pc_stage", "fi_e", "model_e", "compare"):
        assert rc[f"{s}@20260930"] == "skip"
    assert rc["fi_r@20260930"] != "skip"
    assert rc["fi_e@" + T_S] == "0"
    board = _tsv(log / "board.tsv")
    assert [x[0] for x in board[1:3]] == [T_S, "20260930"] and board[2][1] == "없음"
    # 결과가 없는 날의 사유는 실패 단계(② rc 2 · ⑤ 연구 판 T+1 — 현판에 T+1 수정주가·수급 행이 없다) —
    # 재생 원장을 못 썼으니 결손 수는 '-'
    assert board[2][7:10] == ["postclose@20260930,fi_r@20260930", "-", "-"]
    assert board[2][10].startswith("실패 단계 postclose@20260930")
    assert "없음 1(실패 단계 1 · 미실행 0)" in board[-1][0]
    assert "dt=20260930" in (log / "postclose_20260930.log").read_text(encoding="utf-8")

    r2 = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S, "--steps", "board",
              "--code", str(DB), "--spearman-min", "0")
    log3 = w.out / "logs" / "pass3"
    assert (w.out / "stage_pin" / "pass3").is_dir(), r2.out
    assert (w.out / "data" / "postclose_replay" / "pass3" / T_S / "postclose.db").is_file()

    def hashes(path: Path) -> dict[str, str]:
        return {x[1]: x[5] for x in _tsv(path)[1:] if x[0] == "fi_e@" + T_S and x[1] != "-"}
    assert hashes(log3 / "summary.tsv") == hashes(log / "summary.tsv") != {}


def test_two_consecutive_days_both_pass_end_to_end(tmp_path) -> None:
    """PR-8b 리뷰 MINOR-5 — 연속 이틀이 둘 다 성공하는 경로를 끝까지 본다(여러 날 테스트의 픽스처에 T+1
    원장·현판 행을 더한 변형): 장 마감 stage 두 번째 단독 빌드(직전 판 대비 G5 통과) · 둘째 날 장 마감 판은
    첫날 재생 연구 판 T 를 D' 로 고정(인계 이력 · 세션 자르기 · 후보) · board.tsv 두 줄 다 pass."""
    w = _home(tmp_path.resolve(), second_day=True)
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--dates", f"{T_S}-{T1_S}",
             "--steps", "board", "--code", str(DB), "--spearman-min", "0")
    assert r.rc == 0, r.out
    log, mdb = w.out / "logs" / "pass2", w.out / "data" / "model_db"
    steps = [(x[0], x[2]) for x in _tsv(log / "summary.tsv")[1:] if x[1] == "-"]
    want = ["fi_r@" + DP_S] + [f"{s}@{day}" for day in (T_S, T1_S)
                               for s in ("postclose", "pc_stage", "fi_e", "model_e", "fi_r",
                                         "model_r", "compare")]
    assert [s for s, _ in steps] == want and {rc for _, rc in steps} == {"0"}, r.out

    # ② 장 마감 stage 두 번째 단독 빌드 — 첫 판은 G5 기준 없음(skip), 둘째 판은 첫 판 대비 G5 pass
    m = _json(mdb / "stage" / "stg_flow_postclose_kiwoom" / "MANIFEST.json")
    builds = m["builds"]
    assert len(builds) == 2 and m["current_build"] == builds[1]["build_id"]
    g5 = [{g["name"]: g for g in b["gates"]}["G5"] for b in builds]
    assert [g["status"] for g in g5] == ["skip", "pass"], g5
    assert g5[1]["metrics"]["d_stage"] == 0                # 같은 대상 · 같은 원장 행 수

    # ③ 둘째 날 장 마감 판 = 첫날 재생 연구 판 T 를 D' 로 고정
    first_r = _json(w.out / "data" / "factor_inputs" / "_runs" / f"{T_S}_morning.json")
    run = _json(mdb / "factor_inputs" / "_runs" / f"{T1_S}_evening.json")
    hist = w.out / "data" / "deliver" / "history" / f"{T_S}_morning.json"
    assert run["status"] == "ok" and run["asof"] == T.isoformat()
    assert run["builds_from"] == str(hist) and run["builds_from_date"] == T_S
    assert run["replay"]["session_cut"] == T.isoformat()
    assert run["postclose_builds"] == {"stg_flow_postclose_kiwoom": builds[1]["build_id"]}
    db = w.out / "data" / "postclose_replay" / "pass2" / T1_S / "postclose.db"
    con = sqlite3.connect(db)
    try:
        (src,) = con.execute("SELECT d_prime, fi_build FROM replay_source").fetchall()
    finally:
        con.close()
    assert src == (T_S, first_r["build_id"])               # 수집 대상 ① = 첫날 연구 판 후보
    ev_t1 = {t: c for t, c in tf.q(mdb / "factor_inputs", "fi_prices",
                                   f"SELECT ticker, close FROM t WHERE date = DATE '{T1}'")}
    assert ev_t1[tf.A] == _close(tf.A) + 50 and ev_t1[CLOSE_DIFF] == _close(CLOSE_DIFF) + 60

    # ⑥ 두 날 다 대조 pass, board.tsv 두 줄 pass
    for day in (T_S, T1_S):
        rep = _json(mdb / "compare" / f"{day}.json")
        assert rep["replay"] is True and rep["verdict"] == "pass" and rep["n_unexplained"] == 0, rep
    board = _tsv(log / "board.tsv")
    assert [(x[0], x[1], x[7]) for x in board[1:3]] == [(T_S, "pass", "-"), (T1_S, "pass", "-")]
    assert board[-1][0].startswith("대조 2일 — pass 2 · fail 0 · error 0 · 없음 0")


# ── 인자 · 입력 검사(아무것도 쓰기 전) ──────────────────────────────────────────
@pytest.fixture(scope="module")
def bare(tmp_path_factory) -> SimpleNamespace:
    """검사용 — 운영 대역만(출력 루트 없음)."""
    base = tmp_path_factory.mktemp("replay_args").resolve()
    home = base / "home"
    ops = home / "quant-ledger"
    py = ops / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(_SHIM.replace("{real}", sys.executable), encoding="utf-8")
    py.chmod(0o755)
    (ops / "data" / "stage").mkdir(parents=True)
    tf._cal_dir(ops / "data")
    _kiwoom_db(ops / "data" / "raw" / "kiwoom.db")
    return SimpleNamespace(home=home, ops=ops)


@pytest.mark.parametrize("args, word", [
    (["--date", T_S, "--stage-at", T_S], "--stage-at"),
    (["--date", T_S, "--dates", f"{T_S}-{T_S}"], "하나만"),
    (["--dates", "20260929"], "--dates"),
    (["--dates", f"{T_S}-20260901"], "날짜"),                     # 거꾸로 된 범위
    (["--dates", "20261003-20261004"], "날짜"),                   # 주말뿐
    (["--date", T_S, "--steps", "fi"], "알 수 없는 단계"),
    (["--steps", "board"], "--date"),
    (["--date", T_S, "--spearman-min", "x"], "spearman"),
    (["--date", T_S, "--steps", "board"], "equity"),             # 앞 패스 equity 없음
])
def test_argument_and_input_errors_are_rc2_and_write_nothing(bare, args: list[str],
                                                            word: str) -> None:
    out = bare.home / "replay" / "x"
    r = _run(bare.home, "--out", str(out), "--basis", "evening", "--code", str(DB), *args)
    assert r.rc == 2, r.out
    assert word in r.out
    assert not out.exists()
    assert not [c for c in r.calls if c.startswith("-m ")]


def test_dates_only_with_the_evening_basis(bare) -> None:
    out = bare.home / "replay" / "m"
    r = _run(bare.home, "--out", str(out), "--date", T_S, "--dates", f"{T_S}-{T_S}",
             "--code", str(DB))
    assert r.rc == 2 and "--basis evening" in r.out
    assert not out.exists()


def test_missing_kiwoom_ledger_is_rc2(bare, tmp_path) -> None:
    kw = bare.ops / "data" / "raw" / "kiwoom.db"
    moved = tmp_path / "kiwoom.db"
    kw.rename(moved)
    try:
        r = _run(bare.home, "--out", str(bare.home / "replay" / "k"), "--basis", "evening",
                 "--date", T_S, "--code", str(DB))
    finally:
        moved.rename(kw)
    assert r.rc == 2 and "키움 원장" in r.out


def test_failed_last_equity_pass_refuses_board_only(bare) -> None:
    """--steps board 는 앞 패스 equity 판을 쓴다 — 그 패스에 실패·건너뛴 표가 있으면 혼합 판이라
    거부한다."""
    out = bare.home / "replay" / "f"
    (out / "logs" / "pass1").mkdir(parents=True)
    (out / "logs" / "pass1" / "summary.tsv").write_text(
        "step\ttable\trc\nequity\tcorp\t0\nequity\tprice_daily\t7\nequity\tsecurity\tskip\n",
        encoding="utf-8")
    r = _run(bare.home, "--out", str(out), "--basis", "evening", "--date", T_S, "--steps", "board",
             "--code", str(DB))
    assert r.rc == 2 and "price_daily" in r.out
    assert sorted(p.name for p in (out / "logs").iterdir()) == ["pass1"]


# ── 보조 도구 ─────────────────────────────────────────────────────────────────
def _py(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ, PYTHONPATH=str(DB / "src"), **(env or {}))
    return subprocess.run([sys.executable, *args], env=e, capture_output=True, text=True,
                          check=False)


def test_days_counts_trading_days_by_the_judging_calendar(tmp_path) -> None:
    """판정 달력(daily.calendar)으로 센다 — 주말·휴장일(09-24·25)을 건너뛰고 D' 는 첫 T 의 직전
    거래일."""
    cal = tf._cal_dir(tmp_path)
    p = _py(str(EVT), "days", "--calendar-dir", str(cal), "--from", "20260924", "--to", "20260929")
    assert p.returncode == 0, p.stderr
    assert p.stdout.split() == ["20260923", "20260928", "20260929"]
    bad = _py(str(EVT), "days", "--calendar-dir", str(tmp_path / "none"), "--from", T_S,
              "--to", T_S)
    assert bad.returncode == 2 and "달력" in bad.stderr


def test_postclose_refuses_an_existing_file_and_a_day_without_rows(replayed, tmp_path) -> None:
    kw = replayed.ops / "data" / "raw" / "kiwoom.db"
    existing = tmp_path / "x" / "postclose.db"
    existing.parent.mkdir()
    existing.write_bytes(b"")
    p = _py(str(EVT), "postclose", "--kiwoom-db", str(kw), "--base", str(replayed.out),
            "--date", T_S, "--d-prime", DP_S, "--out", str(existing))
    assert p.returncode == 2 and "이미" in p.stderr
    p = _py(str(EVT), "postclose", "--kiwoom-db", str(kw), "--base", str(replayed.out),
            "--date", "20260930", "--d-prime", T_S, "--out", str(tmp_path / "y" / "postclose.db"))
    assert p.returncode == 2 and "dt=20260930" in p.stderr
    assert not (tmp_path / "y" / "postclose.db").exists()


def _stage_table(root: Path, table: str, bids: list[str]) -> None:
    for bid in bids:
        part = root / table / f"v={bid}" / "year=2026"
        part.mkdir(parents=True)
        (part / "part0.parquet").write_bytes(f"{table}-{bid}".encode())
    (root / table / "MANIFEST.json").write_text(json.dumps(
        {"table": table, "current_build": bids[-1], "keep": 3,
         "builds": [{"build_id": b, "content_hash": f"h-{b}",
                     "partitions": [{"path": f"v={b}/year=2026"}]} for b in bids]}),
        encoding="utf-8")


def test_stage_pin_hardlinks_the_current_builds(tmp_path) -> None:
    ops = tmp_path / "ops" / "data"
    _stage_table(ops / "stage", "stg_a", ["m_1", "m_2"])
    _stage_table(ops / "stage", "stg_b", ["m_9"])
    dest = tmp_path / "out" / "stage_pin" / "pass1"
    p = _py(str(TOOL), "stage-pin", "--ops-data", str(ops), "--tables", "stg_a,stg_b",
            "--optional", "stg_q", "--dest", str(dest))
    assert p.returncode == 0, p.stderr
    assert [ln.split("\t")[:3] for ln in p.stdout.splitlines()] == [
        ["stg_a", "m_2", "stage"], ["stg_b", "m_9", "stage"], ["stg_q", "-", "absent"]]
    m = _json(dest / "stg_a" / "MANIFEST.json")
    assert m["current_build"] == "m_2" and [b["build_id"] for b in m["builds"]] == ["m_2"]
    f = dest / "stg_a" / "v=m_2" / "year=2026" / "part0.parquet"
    assert f.stat().st_ino == (ops / "stage" / "stg_a" / "v=m_2" / "year=2026" /
                               "part0.parquet").stat().st_ino
    assert not (dest / "stg_q").exists()
    again = _py(str(TOOL), "stage-pin", "--ops-data", str(ops), "--tables", "stg_a",
                "--dest", str(dest))
    assert again.returncode == 2
    missing = _py(str(TOOL), "stage-pin", "--ops-data", str(ops), "--tables", "stg_q",
                  "--dest", str(tmp_path / "out" / "p2"))
    assert missing.returncode == 2 and not (tmp_path / "out" / "p2").exists()


def test_stage_guard_and_stage_pin_read_only_morning_or_manual_current_builds(tmp_path) -> None:
    """PR-8b 리뷰 MINOR-4 — 운영 stage 현판이 아침 확정(m_)·수동(b_) 판이 아니면 rc 2. 저녁 잠정판(e_)은
    그 표 이름 전부와 가능한 원인 둘(저녁 빌드 창 / 아침 확정 빌드가 m_ 로 못 바꿈 — 재리뷰 NIT-2)을, 그 밖의
    접두어는 fail-closed 사유를 낸다. stage-guard 와 stage-pin 이 같은 판정(`check_stage_basis`)을 부른다 —
    stage-pin 은 아무것도 고정하지 않고 멈춘다. 현판이 없는 표는 판정하지 않는다."""
    ops = tmp_path / "ops" / "data"
    _stage_table(ops / "stage", "stg_m", ["e_1", "m_2"])         # 아침 확정 뒤
    _stage_table(ops / "stage", "stg_b", ["b_1"])
    _stage_table(ops / "stage", "stg_e", ["m_1", "e_2"])         # 저녁 빌드 뒤 또는 아침에 못 바꿈
    _stage_table(ops / "stage", "stg_e2", ["e_3"])
    _stage_table(ops / "stage", "stg_x", ["20261001"])           # 접두어 없는 옛 레코드
    ok = _py(str(TOOL), "stage-guard", "--ops-data", str(ops), "--tables", "stg_m,stg_b,stg_none")
    assert ok.returncode == 0, ok.stderr
    assert "2표" in ok.stdout and "현판 없는 표 1" in ok.stdout
    both = _py(str(TOOL), "stage-guard", "--ops-data", str(ops), "--tables",
               "stg_m,stg_e,stg_e2,stg_x")
    assert both.returncode == 2
    for word in ("3/4표", "저녁 잠정판(e_) 2표: stg_e, stg_e2", "저녁 빌드 창", "21:20",
                 "아침 확정 빌드가 이 표를 m_ 로 바꾸지 못했다", "stg_x=20261001", "fail-closed"):
        assert word in both.stderr, (word, both.stderr)
    for bad, word in (("stg_e", "아침 확정 빌드가"), ("stg_x", "fail-closed")):
        p = _py(str(TOOL), "stage-guard", "--ops-data", str(ops), "--tables", f"stg_m,{bad}")
        assert p.returncode == 2 and word in p.stderr and bad in p.stderr, p.stderr
        assert "stg_m" not in p.stderr
        if bad == "stg_e":
            assert "fail-closed" not in p.stderr
        else:
            assert "저녁 빌드 창" not in p.stderr
        dest = tmp_path / "out" / bad
        p = _py(str(TOOL), "stage-pin", "--ops-data", str(ops), "--tables", "stg_m",
                "--optional", bad, "--dest", str(dest))
        assert p.returncode == 2 and word in p.stderr and not dest.exists(), p.stderr
    p = _py(str(TOOL), "stage-pin", "--ops-data", str(ops), "--tables", "stg_m,stg_b",
            "--dest", str(tmp_path / "out" / "ok"))
    assert p.returncode == 0, p.stderr
    assert [ln.split("\t")[:2] for ln in p.stdout.splitlines()] == [["stg_m", "m_2"], ["stg_b", "b_1"]]
    assert _py(str(TOOL), "stage-guard", "--ops-data", str(ops), "--tables", "").returncode == 2


def _window_home(base: Path, stage: dict[str, list[str]]) -> SimpleNamespace:
    """실행 창 검사용 — 운영 대역(달력·21:05 원장·주어진 stage 판)과 앞 패스 equity 기록만 있는 출력 루트."""
    home = base / "home"
    ops = home / "quant-ledger"
    py = ops / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(_SHIM.replace("{real}", sys.executable), encoding="utf-8")
    py.chmod(0o755)
    (ops / "data" / "stage").mkdir(parents=True)
    for table, bids in stage.items():
        _stage_table(ops / "data" / "stage", table, bids)
    tf._cal_dir(ops / "data")
    _kiwoom_db(ops / "data" / "raw" / "kiwoom.db")
    out = home / "replay" / "w"
    (out / "logs" / "pass1").mkdir(parents=True)
    (out / "logs" / "pass1" / "summary.tsv").write_text(
        "step\ttable\trc\nequity\tcorp\t0\n", encoding="utf-8")
    return SimpleNamespace(home=home, ops=ops, out=out)


FI_STAGE = ("stg_consensus_annual", "stg_consensus_matrix", "stg_fin_wise")


@pytest.mark.parametrize(("steps", "stage", "flipped"), [
    # equity 단계가 읽는 표(equity 규칙 입력)의 운영 현판이 저녁 잠정판
    ("equity", {"stg_price_daily": ["m_1", "e_2"]}, "e_) 1표: stg_price_daily"),
    # fi 가 stage 에서 직접 읽는(고정 stage) 표의 운영 현판이 저녁 잠정판
    ("board", {**{t: ["m_1"] for t in FI_STAGE}, "stg_consensus_matrix": ["m_1", "e_2"]},
     "e_) 1표: stg_consensus_matrix"),
    ("equity,board", {**{t: ["m_1"] for t in FI_STAGE}, "stg_fin_wise_q": ["e_2"]},
     "e_) 1표: stg_fin_wise_q"),
])
def test_evening_replay_refuses_outside_the_run_window(tmp_path, steps: str,
                                                       stage: dict[str, list[str]],
                                                       flipped: str) -> None:
    """PR-8b 리뷰 MINOR-4 — 21:20 연구 저녁 빌드 뒤~다음 아침 확정 전(운영 stage 현판 e_)이면 equity 단계·
    고정 stage 어느 쪽 표든 rc 2 로 거부하고 아무것도 쓰거나 돌리지 않는다."""
    w = _window_home(tmp_path.resolve(), stage)
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S, "--steps", steps,
             "--code", str(DB))
    assert r.rc == 2, r.out
    assert "실행 창 밖" in r.out and "21:20" in r.out and flipped in r.out
    assert sorted(p.name for p in (w.out / "logs").iterdir()) == ["pass1"]
    assert not (w.out / "data").exists() and not (w.out / "stage_pin").exists()
    assert not [c for c in r.calls if c.startswith("-m ")]


# 경합 대역 python — `-m equity … build <표>` 는 빌드 대신 출력 루트에 그 표 MANIFEST 만 쓰고(성공), 첫
# 호출에서 운영 stage 앞쪽 표(stg_price_daily) 현판을 저녁 잠정판으로 바꾼다(equity 를 짓는 사이 21:20 저녁
# 빌드가 그 표를 커밋한 상황). 다른 `-m` 은 적기만 한다 — 불리면 안 된다
_RACE_SHIM = '''#!{real}
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
with open(os.environ["QL_CALLS"], "a", encoding="utf-8") as f:
    f.write(" ".join(argv) + "\\n")
if argv[:1] != ["-m"]:
    os.execv(sys.executable, [sys.executable, *argv])
if argv[1] == "equity" and "build" in argv:
    table = argv[argv.index("build") + 1]
    tdir = Path(argv[argv.index("--root") + 1]) / table
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "MANIFEST.json").write_text(json.dumps(
        {"table": table, "current_build": "m_fake", "keep": 3,
         "builds": [{"build_id": "m_fake", "content_hash": "h-" + table, "n_rows": 1}]}))
    ops = Path(os.environ["HOME"]) / "quant-ledger" / "data" / "stage" / "stg_price_daily"
    m = json.loads((ops / "MANIFEST.json").read_text())
    if m["current_build"] != "e_2":
        m["current_build"] = "e_2"
        m["builds"].append(dict(m["builds"][-1], build_id="e_2"))
        (ops / "MANIFEST.json").write_text(json.dumps(m))
'''


def test_evening_replay_rechecks_the_window_right_after_the_equity_step(tmp_path) -> None:
    """PR-8b 재리뷰 MINOR-A — 시작 가드는 통과했는데 equity 를 짓는 사이 21:20 저녁 빌드가 앞쪽 stg 표
    (stg_price_daily — fi 직독 표보다 먼저 지어진다)를 e_ 로 바꾸면, fi 직독 표만 보는 stage-pin 으로는
    못 잡는다. equity 단계 직후 가드 목록 전체로 다시 판정해 rc 2 로 멈추고 뒤 단계는 돌지 않는다."""
    w = _window_home(tmp_path.resolve(), {**{t: ["m_1"] for t in FI_STAGE},
                                          "stg_price_daily": ["m_1"]})
    (w.ops / ".venv" / "bin" / "python").write_text(_RACE_SHIM.replace("{real}", sys.executable),
                                                    encoding="utf-8")
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S,
             "--steps", "equity,board", "--code", str(DB))
    assert r.rc == 2, r.out
    assert "equity 단계 직후 재판정" in r.out and "e_) 1표: stg_price_daily" in r.out
    run = (w.out / "logs" / "pass2" / "run.txt").read_text(encoding="utf-8")
    lines = run.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("stage-guard: 운영 stage 현판"))
    again = next(i for i, ln in enumerate(lines) if ln.startswith("equity 뒤 재판정"))
    assert start < again and "e_) 1표: stg_price_daily" in lines[again]   # 시작 가드는 통과했다
    mods = [c.split()[1] for c in r.calls if c.startswith("-m ")]
    order = [ln for ln in (DB / "scripts" / "equity_order.txt").read_text(encoding="utf-8")
             .splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert mods == ["equity"] * len(order)                              # equity 다음은 돌지 않는다
    assert not (w.out / "stage_pin").exists()
    assert not (w.out / "data" / "deliver").exists()
    assert not (w.out / "data" / "postclose_replay").exists()
    # 같은 상태에서 fi 직독 표만 보는 고정 stage 판정은 통과한다 — 재판정이 없으면 놓친다
    p = _py(str(TOOL), "stage-pin", "--ops-data", str(w.ops / "data"), "--tables",
            ",".join(FI_STAGE), "--dest", str(tmp_path / "pin"))
    assert p.returncode == 0, p.stderr


def test_evening_replay_inside_the_run_window_passes_the_guard(tmp_path) -> None:
    """같은 판정이 m_·b_ 현판이면 통과한다 — equity 단계로 넘어가 판정 줄이 run.txt 에 남는다(합성 운영
    stage 에 equity 입력이 다 없어 equity 첫 표에서 실패 — rc 1)."""
    w = _window_home(tmp_path.resolve(), {"stg_price_daily": ["e_1", "m_2"],
                                          "stg_listing_daily": ["b_1"]})
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S, "--steps", "equity",
             "--code", str(DB))
    assert r.rc == 1, r.out
    run = (w.out / "logs" / "pass2" / "run.txt").read_text(encoding="utf-8")
    assert "stage-guard: 운영 stage 현판 2표" in run
    assert any(c.startswith("-m equity ") for c in r.calls)


def test_evening_replay_stage_pin_failure_is_an_input_error(tmp_path) -> None:
    """고정 stage(stage-pin)가 서지 못하면 입력 오류 rc 2 로 멈춘다(--stage-at 의 임시 stage 루트와 같다) —
    여기서는 현판 접두어는 m_ 인데 판 디렉터리가 없다. 날짜별 단계는 돌지 않는다."""
    w = _window_home(tmp_path.resolve(), {t: ["m_1"] for t in FI_STAGE})
    shutil.rmtree(w.ops / "data" / "stage" / "stg_fin_wise" / "v=m_1")
    r = _run(w.home, "--out", str(w.out), "--basis", "evening", "--date", T_S, "--steps", "board",
             "--code", str(DB))
    assert r.rc == 2, r.out
    assert "고정 stage 를 세우지 못했다" in r.out and "stg_fin_wise" in r.out
    assert not [c for c in r.calls if c.startswith("-m ")]
    assert not (w.out / "stage_pin" / "pass2").exists()


def test_handoff_is_readable_by_the_real_reader_and_marked_as_replay(tmp_path) -> None:
    from equity import handoff
    data = tmp_path / "out" / "data"
    _stage_table(data / "equity", "price_daily", ["m_e1", "m_e2"])
    _stage_table(data / "equity", "corp", ["m_e3"])
    (data / "equity" / "_pinned").mkdir()
    pin = tmp_path / "out" / "stage_pin" / "pass1"
    _stage_table(pin, "stg_a", ["m_s"])
    p = _py(str(TOOL), "handoff", "--data", str(data), "--stage-root", str(pin), "--date", T_S,
            "--note", "pass1")
    assert p.returncode == 0, p.stderr
    path = data / "deliver" / "history" / f"{T_S}_morning.json"
    h = handoff.load(path)
    assert (h.date, h.basis, h.health_ok) == (T_S, "morning", True)
    assert h.equity_builds == {"corp": "m_e3", "price_daily": "m_e2"}
    assert h.stage_builds == {"stg_a": "m_s"}
    assert _json(path)["replay"]["note"] == "pass1"
    empty = _py(str(TOOL), "handoff", "--data", str(tmp_path / "none"), "--stage-root", str(pin),
                "--date", T_S)
    assert empty.returncode == 2


def test_equity_pass_reads_the_last_pass_that_built_equity(tmp_path) -> None:
    logs = tmp_path / "logs"

    def write(n: int, body: str) -> None:
        (logs / f"pass{n}").mkdir(parents=True)
        (logs / f"pass{n}" / "summary.tsv").write_text("step\ttable\trc\n" + body,
                                                       encoding="utf-8")
    assert _py(str(TOOL), "equity-pass", "--logs", str(logs)).returncode == 1
    write(1, "equity\tcorp\t7\n")
    write(2, "equity\tcorp\t0\nequity\tsecurity\t0\n")
    write(10, "fi_r@20260929\t-\t0\n")                 # equity 없는 뒤 패스는 넘긴다(숫자 순)
    p = _py(str(TOOL), "equity-pass", "--logs", str(logs))
    assert p.returncode == 0 and p.stdout.strip() == "pass2 equity 2/2"
    write(11, "equity\tcorp\t0\nequity\tsecurity\tskip\n")
    p = _py(str(TOOL), "equity-pass", "--logs", str(logs))
    assert p.returncode == 1 and "security" in p.stderr


def test_board_summary_counts_verdicts_and_skips_stale_files(tmp_path) -> None:
    d = tmp_path / "compare"
    d.mkdir()
    (d / "20260929.json").write_text(json.dumps(
        {"verdict": "pass", "rc": 0, "n_unexplained": 0, "n_unexplained_tickers": 0,
         "model": {"scope@1.0": {"spearman": 0.991}, "v2@1.0": {"spearman": 0.982}}}),
        encoding="utf-8")
    (d / "20260930.json").write_text(json.dumps(
        {"verdict": "fail", "rc": 1, "n_unexplained": 3, "n_unexplained_tickers": 2,
         "reasons": ["미설명 3건"], "model": {"scope@1.0": {"spearman": 0.95}}}), encoding="utf-8")
    (d / "20261001.json").write_text(json.dumps({"verdict": "error", "rc": 2, "error": "판 없음"}),
                                     encoding="utf-8")
    old = d / "20261002.json"
    old.write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
    os.utime(old, (1_000, 1_000))
    p = _py(str(TOOL), "board-summary", "--compare-dir", str(d), "--dates",
            "20260929,20260930,20261001,20261002,20261005", "--since", "2000")
    assert p.returncode == 1, p.stderr
    rows = [ln.split("\t") for ln in p.stdout.splitlines()]
    assert [r[:4] for r in rows[1:6]] == [
        ["20260929", "pass", "0", "0"], ["20260930", "fail", "1", "3"],
        ["20261001", "error", "2", "-"], ["20261002", "없음", "-", "-"],
        ["20261005", "없음", "-", "-"]]
    assert rows[1][5] == "0.9820" and rows[1][6] == "scope@1.0=0.9910,v2@1.0=0.9820"
    assert rows[3][10] == "판 없음"
    # --summary 가 없으면 실패 단계·원장 결손은 모른다('-')
    assert {tuple(r[7:10]) for r in rows[1:6]} == {("-", "-", "-")}
    assert rows[-1][0] == ("대조 5일 — pass 1 · fail 1 · error 1 · 없음 2 · 미설명 합 3 · "
                           "Spearman 최저 0.9500(scope@1.0 20260930)")
    one = _py(str(TOOL), "board-summary", "--compare-dir", str(d), "--dates", "20260929")
    assert one.returncode == 0


def _summary_tsv(path: Path, steps: list[tuple[str, str]]) -> Path:
    path.write_text("step\ttable\trc\tsec\tbuild_id\tcontent_hash\tn_rows\n" + "".join(
        f"{s}\t-\t{rc}\t1\t-\t-\t-\n" for s, rc in steps), encoding="utf-8")
    return path


def test_board_summary_tells_a_failed_step_from_a_day_not_run(tmp_path) -> None:
    """PR-8b 리뷰 MINOR-3 — 결과가 없는 날의 사유가 '실패 단계 X'(그날 rc≠0 단계, 그 T 의 연구 판 D'
    fi_r 포함)인지 '미실행'인지 board.tsv 에서 갈린다. 재생 원장 결손은 replay_source 에서 싣는다."""
    d = tmp_path / "compare"
    d.mkdir()
    (d / "20260929.json").write_text(json.dumps(
        {"verdict": "fail", "rc": 1, "n_unexplained": 2, "reasons": ["미설명 2건"],
         "model": {"scope@1.0": {"spearman": 0.99}}}), encoding="utf-8")
    summary = _summary_tsv(tmp_path / "summary.tsv", [
        ("fi_r@20260928", "0"),
        ("postclose@20260929", "0"), ("pc_stage@20260929", "0"), ("fi_e@20260929", "0"),
        ("model_e@20260929", "0"), ("fi_r@20260929", "0"), ("model_r@20260929", "0"),
        ("compare@20260929", "1"),
        ("postclose@20260930", "0"), ("pc_stage@20260930", "0"), ("fi_e@20260930", "1"),
        ("model_e@20260930", "skip"), ("fi_r@20260930", "1"), ("model_r@20260930", "skip"),
        ("compare@20260930", "skip"),
        # 연구 판 D'(fi_r@20260930) 실패 — ②~④·⑥ 건너뜀
        ("postclose@20261001", "skip"), ("pc_stage@20261001", "skip"), ("fi_e@20261001", "skip"),
        ("model_e@20261001", "skip"), ("fi_r@20261001", "0"), ("model_r@20261001", "0"),
        ("compare@20261001", "skip")])               # 20261002 — 한 단계도 돌지 않았다
    with summary.open("a", encoding="utf-8") as f:
        f.write("fi_e@20260929\tfi_prices\t7\t-\tb\th\t1\n")     # 표 행은 단계가 아니다
    pcr = tmp_path / "postclose_replay"
    for day, miss, cand in (("20260929", 3, 0), ("20260930", 5, 2)):
        (pcr / day).mkdir(parents=True)
        con = sqlite3.connect(pcr / day / "postclose.db")
        con.execute("CREATE TABLE replay_source (dt TEXT PRIMARY KEY, n_missing INTEGER, "
                    "n_candidates_missing INTEGER)")
        con.execute("INSERT INTO replay_source VALUES (?, ?, ?)", (day, miss, cand))
        con.commit()
        con.close()
    p = _py(str(TOOL), "board-summary", "--compare-dir", str(d), "--dates",
            "20260929,20260930,20261001,20261002", "--summary", str(summary),
            "--postclose-dir", str(pcr), "--d-prime", "20260928")
    assert p.returncode == 1, p.stderr
    lines = p.stdout.splitlines()
    rows = {r[0]: r for r in (ln.split("\t") for ln in lines[1:-1])}
    # 결과가 있는 날도 그날 rc≠0 단계를 싣는다
    assert rows["20260929"][1] == "fail"
    assert rows["20260929"][7:] == ["compare@20260929", "3", "0", "미설명 2건"]
    assert rows["20260930"][7:10] == ["fi_e@20260930,fi_r@20260930", "5", "2"]
    assert rows["20260930"][10].startswith("실패 단계 fi_e@20260930, fi_r@20260930")
    # 그 T 의 연구 판 D'(앞 T 의 fi_r)가 실패해 건너뛴 날 — 그 단계가 실패 단계로 보인다
    assert rows["20261001"][7:10] == ["fi_r@20260930", "-", "-"]
    assert rows["20261001"][10].startswith("실패 단계 fi_r@20260930")
    # 그날 실패 단계가 없다 — 미실행
    assert rows["20261002"][7:10] == ["-", "-", "-"] and rows["20261002"][10].startswith("미실행")
    assert "없음 3(실패 단계 2 · 미실행 1)" in lines[-1]
    # 첫 T 의 연구 판 D' 는 --d-prime 으로 찾는다
    first = _summary_tsv(tmp_path / "first.tsv", [("fi_r@20260928", "1"),
                                                  ("postclose@20260929", "skip")])
    p = _py(str(TOOL), "board-summary", "--compare-dir", str(tmp_path / "none"), "--dates",
            "20260929", "--summary", str(first), "--d-prime", "20260928")
    assert p.stdout.splitlines()[1].split("\t")[7] == "fi_r@20260928"
    gone = _py(str(TOOL), "board-summary", "--compare-dir", str(d), "--dates", "20260929",
               "--summary", str(tmp_path / "none.tsv"))
    assert gone.returncode == 2 and "summary.tsv" in gone.stderr
