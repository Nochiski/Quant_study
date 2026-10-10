"""factor_inputs `--replay` — 과거 D 의 판을 현판(최근 세션까지 온 equity)에서 짓는 재생
모드(컷오버 PR-8b).

`replay.sh --basis evening` 은 과거 T 의 장 마감 판을 다시 짓는다. 그날 고정했던 D' 연구 판(stage
keep 3 · equity keep 10)은 GC 로 사라졌으므로 X-1 원칙('현판 + D 시점 자르기')으로 현판을 그날
모양으로 되돌린다. 정보 입력은 SQL 이 이미 asof 로 자르지만 **세션 축**(달력·가격·수급 …)은 자르지
않는다 — 현판으로는 장 마감 판 이음매(MD-SEAM: 연구 판 trading_calendar 마지막 = D')가 서지
않고(rc 2), 아침판은 달력 뒤 세션이 filing_late 실효 기한을 정해 그날 판과 값이 갈린다.

`--replay` 는 equity 세션 축 표 뷰를 asof(아침판 D · 장 마감 판 D') 이하로 자른다. 여기서 보는 것:
  ① 현판 + 자르기 = 그날 판(8표 content_hash 같음) — 아침판·장 마감 판 둘 다
  ② 자르지 않으면 장 마감 판은 MD-SEAM rc 2, 아침판은 filing_late 가 다르다(이 기능이 필요한 이유)
  ③ 재생 모드의 SQL 차이는 세션 축 뷰의 `WHERE date <= DATE '<asof>'` 하나뿐 — 운영 경로(재생 아님)
     SQL 은 그 꼬리가 없는 원문 그대로다
  ④ 판 manifest 의 재생 표시(`replay`)는 재생 판에만 있다

원천은 `test_factor_inputs` 합성 트리다. '그날 판' = 세션이 D(09-28)에서 끝나는 원천, '현판' = 같은
원천에 T(09-29)·T+1(09-30) 세션 행을 더한 것(D 이하 행은 바이트까지 같다).
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import test_factor_inputs as tf
from conftest import allow_skips
from factor_inputs import FactorInputsError, build
from factor_inputs.__main__ import main as cli_main

# 패키지가 build 함수를 내보내 모듈 이름을 가린다 — 모듈은 sys.modules 로 잡는다
fi_build_mod = importlib.import_module("factor_inputs.build")


@pytest.fixture(scope="module", autouse=True)
def _k17a_fixture_skips() -> Iterator[None]:
    with allow_skips(("factor_inputs", "FG4", "no_fixtures",
                      "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")):
        yield


EQ_DAY = "m_20260929T000500_000000Z"        # 그날(D) 판 — test_factor_inputs.EQ_BUILD 와 같다
EQ_CUR = "m_20261001T000500_000000Z"        # 현판(T+1 까지 온 판)
T2 = dt.date(2026, 9, 30)
# D 뒤 세션에 법정기한이 있는 보고서(접수 09-01 ≤ D) — 그날 판 달력에는 기한 뒤 세션이 없어 NULL,
# 현판 달력에는 T 가 있어 false 가 된다(아침판에서 자르기가 필요한 이유)
LATE_TICKER = tf.EXTRA[4]
FILING_AFTER_D = {"rcept_no": "20260901" + LATE_TICKER, "corp_code": tf.corp(LATE_TICKER),
                  "rcept_dt": dt.date(2026, 9, 1), "kind": "half", "is_correction": False,
                  "legal_deadline": tf.T, "delay_days": (dt.date(2026, 9, 1) - tf.T).days,
                  "available_date": dt.date(2026, 9, 1)}


def _later_sessions() -> dict[str, list[dict]]:
    """현판에만 있는 D 뒤 세션 행(T · T+1) — equity 세션 축 7표 전부."""
    out: dict[str, list[dict]] = {
        "trading_calendar": [{"date": tf.T, "prev_td": tf.D, "next_td": T2},
                             {"date": T2, "prev_td": tf.T, "next_td": None}],
        "universe_daily": [], "price_daily": [], "price_adj_daily": [], "flow_daily": [],
        "credit_daily": [], "coverage_daily": []}
    for k, day in enumerate((tf.T, T2), start=1):
        out["universe_daily"] += [dict(r, date=day) for r in tf._universe()]
        for t in tf.SPEC:
            if t == tf.K:
                continue
            close = tf.D_CLOSE[t] + 300 * k
            out["price_daily"].append(tf._price_row(t, day, close))
            share, po = tf.ADJ_FACTOR.get(t, 1.0), tf._price_only(t, day)
            out["price_adj_daily"].append({"ticker": t, "date": day,
                                           "adj_close": close * share / po,
                                           "cum_share_factor": share, "cum_price_only_factor": po,
                                           "n_unadjusted_events": 0, "basis": "krx"})
        out["flow_daily"].append(tf._flow(tf.A, day, "kiwoom", 9_000_000 * k))
        out["credit_daily"].append({"date": day, "ticker": tf.A, "whol_loan_rmnd_stcn_shr": 99 + k,
                                    "whol_loan_rmnd_rate_pct": 0.9})
        out["coverage_daily"].append({"ticker": tf.A, "date": day, "analyst_count": 50 + k})
    return out


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> SimpleNamespace:
    """그날 판 원천(day) · 현판 원천(cur) · 장 마감 stage(T 행) · 판정 달력 · 후보(그날 아침판)."""
    base = tmp_path_factory.mktemp("fi_replay")
    eq_day, st = tf.make_roots(base / "day", eq_build=EQ_DAY,
                               extra={"disclosure_version": [FILING_AFTER_D]})
    eq_cur, _ = tf.make_roots(base / "cur", eq_build=EQ_CUR,
                              extra={"disclosure_version": [FILING_AFTER_D], **_later_sessions()})
    cal = tf._cal_dir(base)
    pc = tf.make_postclose_stage(base / "pc")
    cands = base / "cands"
    res = build(tf.D_S, "morning", cands, st, eq_day, min_eligible=1, golden_path=None)
    assert res.ok, res.summary()
    return SimpleNamespace(base=base, eq_day=eq_day, eq_cur=eq_cur, st=st, cal=cal, pc=pc,
                           cands=cands)


def _hashes(run_manifest: Path) -> dict[str, str]:
    return {t: str(v["content_hash"])
            for t, v in json.loads(run_manifest.read_text(encoding="utf-8"))["tables"].items()}


def _run(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _evening(w: SimpleNamespace, out: Path, eq: Path, eq_build: str, *,
             build_id: str | None = None, replay: bool = False):
    hist = tf._history(w.base / f"hist_{eq_build}.json", equity_build=eq_build)
    return build(tf.T_S, "evening", out, w.st, eq, min_eligible=1, golden_path=None,
                 builds_from=hist, calendar_dir=w.cal, postclose_stage_root=w.pc,
                 candidates_root=w.cands, build_id=build_id, replay=replay)


# ── ① · ② 현판 + 자르기 = 그날 판 ───────────────────────────────────────────────
def test_morning_replay_on_the_current_board_equals_the_board_of_that_day(world, tmp_path) -> None:
    """아침판 D: 현판 + 자르기 = 그날 판(8표). 자르지 않으면 filing_late 만 다르다 — 현판 달력에는
    기한(T) 뒤 세션이 있어 실효 기한이 서고(false), 그날 판에는 없어 NULL 이다."""
    day = build(tf.D_S, "morning", tmp_path / "day", world.st, world.eq_day, min_eligible=1,
                golden_path=None)
    cut = build(tf.D_S, "morning", tmp_path / "cut", world.st, world.eq_cur, min_eligible=1,
                golden_path=None, replay=True)
    raw = build(tf.D_S, "morning", tmp_path / "raw", world.st, world.eq_cur, min_eligible=1,
                golden_path=None)
    assert day.ok and cut.ok and raw.ok
    assert _hashes(cut.run_manifest) == _hashes(day.run_manifest)
    diff = {t for t, h in _hashes(raw.run_manifest).items() if _hashes(day.run_manifest)[t] != h}
    assert diff == {"fi_universe"}
    sql = f"SELECT filing_late FROM t WHERE ticker = '{LATE_TICKER}'"
    assert tf.q(tmp_path / "day", "fi_universe", sql) == [(None,)]
    assert tf.q(tmp_path / "raw", "fi_universe", sql) == [(False,)]
    assert tf.q(tmp_path / "cut", "fi_universe", sql) == [(None,)]


def test_evening_replay_on_the_current_board_equals_the_pinned_d_prime_board(world,
                                                                              tmp_path) -> None:
    """장 마감 판 T: 현판(T·T+1 세션까지)을 고정하면 MD-SEAM 이 서지 않는다(rc 2). `--replay`(CLI)
    는 세션 축을 D' 에서 잘라 이음매가 서고, 8표가 그날 D' 판을 고정한 장 마감 판과 같다 — 현판에
    있는 연구 판 T 가격 행(KRX)이 장 마감 T 행(postclose)과 겹치지 않는다."""
    day = _evening(world, tmp_path / "day", world.eq_day, EQ_DAY)
    assert day.ok, day.summary()
    with pytest.raises(FactorInputsError, match="MD-SEAM"):
        _evening(world, tmp_path / "raw", world.eq_cur, EQ_CUR)
    hist = tf._history(world.base / "hist_cli.json", equity_build=EQ_CUR)
    rc = cli_main(["build", "--date", tf.T_S, "--basis", "evening", "--root",
                   str(tmp_path / "cut"), "--stage-root", str(world.st), "--equity-root",
                   str(world.eq_cur), "--builds-from", str(hist), "--calendar-dir", str(world.cal),
                   "--postclose-stage-root", str(world.pc), "--candidates-root", str(world.cands),
                   "--min-eligible", "1", "--replay"])
    assert rc == 0
    cut = tmp_path / "cut" / "_runs" / f"{tf.T_S}_evening.json"
    assert _hashes(cut) == _hashes(day.run_manifest)
    assert tf.q(tmp_path / "cut", "fi_prices", "SELECT price_source, count(*) FROM t "
                "WHERE date = DATE '2026-09-29' GROUP BY 1") == [("postclose", 12)]


# ── ③ 운영 경로 SQL ────────────────────────────────────────────────────────────
class _Recorder:
    """duckdb 연결 대역 — execute 한 SQL 을 적고 진짜 연결에 넘긴다."""

    def __init__(self, con: duckdb.DuckDBPyConnection, log: list[str]) -> None:
        self._con, self._log = con, log

    def execute(self, sql: str, *args: object) -> duckdb.DuckDBPyConnection:
        self._log.append(sql)
        return self._con.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._con, name)


def _record(monkeypatch, fn) -> list[str]:
    log: list[str] = []
    real = duckdb.connect
    monkeypatch.setattr(fi_build_mod.duckdb, "connect",
                        lambda *a, **k: _Recorder(real(*a, **k), log))
    try:
        fn()
    finally:
        monkeypatch.setattr(fi_build_mod.duckdb, "connect", real)
    return log


@pytest.mark.parametrize("basis", ["morning", "evening"])
def test_replay_differs_from_the_operating_sql_only_by_the_session_view_cut(world, tmp_path,
                                                                            monkeypatch,
                                                                            basis: str) -> None:
    """같은 원천·같은 판 id 로 운영 빌드와 재생 빌드의 SQL 을 적어 견준다. 차이는 세션 축 7표 뷰
    끝의 `WHERE date <= DATE '<asof>'` 뿐이고, 운영 빌드의 뷰는 꼬리 없는 원문(`… AS SELECT * FROM
    read_parquet([…], …)`)이다 — `queries` SQL 은 재생 여부와 무관하게 같은 글자다."""
    asof = "2026-09-28"

    def one(tag: str, replay: bool) -> list[str]:
        out = tmp_path / tag
        bid = ("e_" if basis == "evening" else "m_") + "20261010T000000_000000Z"
        if basis == "evening":
            log = _record(monkeypatch, lambda: _evening(world, out, world.eq_day, EQ_DAY,
                                                         build_id=bid, replay=replay))
        else:
            log = _record(monkeypatch, lambda: build(tf.D_S, "morning", out, world.st,
                                                     world.eq_day, min_eligible=1,
                                                     golden_path=None, build_id=bid,
                                                     replay=replay))
        return [s.replace(str(out), "<ROOT>") for s in log]

    ops, rep = one("ops", False), one("rep", True)
    assert len(ops) == len(rep) > 50
    cut = f" WHERE date <= DATE '{asof}'"
    changed = []
    for a, b in zip(ops, rep, strict=True):
        if a != b:
            assert b == a + cut, (a, b)
            changed.append(a.split('"')[1])
    assert sorted(changed) == sorted(fi_build_mod.REPLAY_SESSION_TABLES)
    views = [s for s in ops if s.startswith("CREATE OR REPLACE TEMP VIEW")
             and "read_parquet" in s]
    assert views and all(s.endswith(")") for s in views)


def test_replay_session_tables_are_equity_sources_with_a_session_date() -> None:
    """자르는 표는 fi 가 읽는 equity 원천 중 세션 날짜(`date`) 축 표다 — 정보 표(공개일
    축)·마스터·stage 원천은 SQL 이 asof 로 자르거나 날짜 축이 없다."""
    assert set(fi_build_mod.REPLAY_SESSION_TABLES) <= set(fi_build_mod.EQUITY_SOURCES)
    assert set(fi_build_mod.REPLAY_SESSION_TABLES) == {
        "trading_calendar", "universe_daily", "price_daily", "price_adj_daily", "flow_daily",
        "credit_daily", "coverage_daily"}


# ── ④ 재생 표시 ────────────────────────────────────────────────────────────────
def test_replay_mark_is_only_on_replay_boards(world, tmp_path) -> None:
    ops = build(tf.D_S, "morning", tmp_path / "ops", world.st, world.eq_day, min_eligible=1,
                golden_path=None)
    rep = _evening(world, tmp_path / "rep", world.eq_cur, EQ_CUR, replay=True)
    assert ops.ok and rep.ok
    assert "replay" not in _run(ops.run_manifest)
    mark = _run(rep.run_manifest)["replay"]
    assert mark["session_cut"] == "2026-09-28"
    assert mark["tables"] == list(fi_build_mod.REPLAY_SESSION_TABLES)
    latest = _run(tmp_path / "rep" / "latest_evening.json")
    assert latest["replay"] == mark


def test_cli_replay_defaults_off() -> None:
    from factor_inputs.__main__ import _parser
    a = _parser().parse_args(["build", "--date", tf.D_S, "--basis", "morning"])
    assert a.replay is False
