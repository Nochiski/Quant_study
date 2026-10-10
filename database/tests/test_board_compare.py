"""두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 T(컷오버 PR-7 · 판정 기준 T-36).

판은 실제 판 규약으로 만든다: fi 8표는 `test_model_build.write_fi_tree`(계약 dtype parquet · `_meta.json`)
+ `_runs/<D>_<basis>.json`, 모델 판은 그 fi 판 위에서 **실제** `model.build`(장 마감 판은 basis evening).
증거 원천도 실물 규약이다 — 장 마감 stage `stg_flow_postclose_kiwoom`·연구 판 equity `adj_factor` 는
`conftest._make_stage_tree`(MANIFEST 포함), 달력은 `daily.calendar` 연도 파일. fi 값은
`test_model_v4_rank.Board` 40(+1)종목 합성 보드이고 D' = 2026-09-25(금), T = 2026-09-28(월)이다.
장 마감 판 = 기준 보드 사본 + 구조 표식, 연구 판 D' = 기준 보드에서 T 행을 뺀 것, 연구 판 T = 기준 보드 +
T 에 바뀐 것. 범주마다 증거가 있는 차이(양성)와 증거가 없는 차이(음성 — 미설명)를 둘 다 본다(T-36).
작은 판 셋으로 보는 경계는 모델 없이 `compare_fi` 를 직접 부른다.
장 마감 판 fi 를 실제 `factor_inputs.build --basis evening` 으로 짓는 왕복은 T 행 얹기(PR-5) 머지 뒤다.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import _make_stage_tree, allow_skips
from daily import board_compare as bc
from factor_inputs import queries as fiq
from factor_inputs.build import RULES_VERSION as FI_RULES
from model.build import build as model_build
from model.contracts import FI_TABLES, FactorInputs
from stage import manifest
from test_model_build import SMALL, board_fi, write_fi_tree
from test_model_v4_rank import _r

T = "2026-09-28"
T_S = "20260928"
T_DATE = dt.date(2026, 9, 28)
DP = "2026-09-25"                      # T 의 직전 거래일(휴장 없는 2026 달력 — 금요일)
DP_S = "20260925"
DP_DATE = dt.date(2026, 9, 25)
EV_FI = "e_20260928T065500_000000Z"
RS_FI = "m_20260929T000500_000000Z"
RD_FI = "m_20260926T000500_000000Z"
PC_BID = "m_20260928T065000_000000Z"   # 장 마감 stage 판
EQ_D = "m_20260926T000000_000000Z"     # D' 확정판 equity(장 마감 판이 고정한 판)
EQ_T = "m_20260929T000000_000000Z"     # T 확정판 equity(연구 판 T 가 읽은 판)
N_STOCKS = 40

# 심은 차이 — 범주마다 종목 하나
P_VOL, P_FLOW, P_HALT, P_INFO, P_SECTOR, P_FIN, P_T6, P_CUT, P_CREDIT, P_EV = (
    f"{100001 + i:06d}" for i in range(10))
P_NEW = f"{100000 + N_STOCKS:06d}"     # 연구 판에만 있는 T 신규 상장
P_PRE_T = "100020"                      # 범주 밖 — T 전 행 종가
EV_FROM = dt.date(2026, 9, 21)          # P_EV 의 (D', T] 공개 기업행위 적용일


def _tables(fi: FactorInputs) -> dict[str, list[dict[str, object]]]:
    """보드 표 사본 — D' 와 T 사이(주말) 행은 뺀다(달력에 없는 세션)."""
    out = {k: [dict(r) for r in v] for k, v in fi.tables.items()}
    for name in bc.DATED_TABLES:
        out[name] = [r for r in out[name]
                     if not DP_DATE < r["date"] < T_DATE]  # type: ignore[operator]
    return out


def _rows(tables: dict[str, list[dict[str, object]]], table: str, ticker: str,
          day: dt.date | None = None) -> list[dict[str, object]]:
    return [r for r in tables[table]
            if r["ticker"] == ticker and (day is None or r.get("date") == day)]


def _evening_copy(base: dict[str, list[dict[str, object]]],
                  drop: tuple[str, ...] = ()) -> dict[str, list[dict[str, object]]]:
    """기준 보드 → 장 마감 판: 구조 표식만(시총 기준·T 행 출처, T 행 시·고·저가·거래대금 NULL)."""
    ev = {k: [copy.deepcopy(r) for r in v if r["ticker"] not in drop] for k, v in base.items()}
    for r in ev["fi_universe"]:
        r["mktcap_basis"] = fiq.T_MKTCAP_BASIS
    for r in ev["fi_prices"]:
        if r["date"] == T_DATE:
            r.update(price_source=fiq.T_PRICE_SOURCE, open=None, high=None, low=None, amount=None)
    return ev


def _dprime_of(base: dict[str, list[dict[str, object]]],
               drop: tuple[str, ...] = ()) -> dict[str, list[dict[str, object]]]:
    """기준 보드 → 연구 판 D'(T 행 없음 · 신용은 available ≤ D' · 유니버스 date = D')."""
    rd = {k: [copy.deepcopy(r) for r in v if r["ticker"] not in drop] for k, v in base.items()}
    for name in bc.DATED_TABLES:
        rd[name] = [r for r in rd[name] if r["date"] <= DP_DATE]  # type: ignore[operator]
    rd["fi_credit"] = [r for r in rd["fi_credit"] if r["available_date"] is None
                       or r["available_date"] <= DP_DATE]  # type: ignore[operator]
    for r in rd["fi_universe"]:
        r["date"] = DP_DATE
    return rd


def write_fi(root: Path, tables: dict[str, list[dict[str, object]]], *, basis: str, bid: str,
             day: str, asof: str, latest: bool = True, **run: object) -> Path:
    """fi 판(계약 dtype) + 표마다 MANIFEST 기록(`stage.manifest.commit` — 수집기 `fi_candidates` 가
    이것으로 판을 푼다) + 판 기록 `_runs/<D>_<basis>.json`."""
    write_fi_tree(root, FactorInputs(day, basis, bid, tables), bid, basis=basis, date=day,
                  latest=latest)
    for name in FI_TABLES:
        manifest.commit(root / name, manifest.BuildRecord(
            build_id=bid, snapshot_id="", rules_version=FI_RULES, basis=basis,
            built_at_utc="2026-09-29T00:00:00+00:00", n_rows=len(tables[name]), content_hash="",
            partitions=[{"path": f"v={bid}"}]))
    rec = {"layer": "factor_inputs", "status": "ok", "build_id": bid, "date": day, "basis": basis,
           "asof": asof, "rules_version": FI_RULES, **run}
    (root / "_runs").mkdir(parents=True, exist_ok=True)
    (root / "_runs" / f"{day.replace('-', '')}_{basis}.json").write_text(json.dumps(rec),
                                                                         encoding="utf-8")
    return root


def _calendar(dir_: Path, holidays: tuple[str, ...] = ()) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "kis_holidays_2026.json").write_text(
        json.dumps({"year": 2026, "holidays": list(holidays)}), encoding="utf-8")
    return dir_


def _pair(base: Path, rs: dict[str, list[dict[str, object]]],
          ev: dict[str, list[dict[str, object]]],
          rd: dict[str, list[dict[str, object]]] | None = None, *,
          stage_missing: tuple[str, ...] = (), stage_invalid: tuple[str, ...] = (),
          stage_no_pred: tuple[str, ...] = (), krx_base: dict[str, int] | None = None,
          events: tuple[tuple[str, dt.date], ...] = ()) -> tuple[Path, Path]:
    """세 판 + 증거 원천을 짓는다 → (장 마감 루트, 연구 루트). `rd` 를 안 주면 연구 판 T 에서 D' 를
    만든다(연구 판에서 T 에 바뀐 것까지 D' 에 들어가 '바뀌지 않았다'가 된다 — 보수적인 기본).
    연구 판 equity `price_daily` T 행의 KRX 기준가는 연구 판 D' 종가(기업행위 없음)이고 `krx_base` 가
    덮는다. 장 마감 stage 의 pred_pre 는 `stage_no_pred` 종목만 NULL."""
    rd = _dprime_of(rs) if rd is None else rd
    pinned = {"price_daily": EQ_D, "adj_factor": EQ_D}
    data = base / "data"
    _calendar(data / "calendar")
    eq_rows = [{"ticker": "999999", "apply_date": dt.date(2020, 1, 2),
                "available_date": dt.date(2020, 1, 2), "price_resolution": "factor",
                "factor_ok": True}]
    eq_rows += [{"ticker": tk, "apply_date": d, "available_date": T_DATE,
                 "price_resolution": "unresolved", "factor_ok": False} for tk, d in events]
    _make_stage_tree(base / "eq", "adj_factor", eq_rows, build_id=EQ_T)
    bases = {str(r["ticker"]): r["close"] for r in rs["fi_prices"] if r["date"] == DP_DATE}
    bases.update(krx_base or {})
    _make_stage_tree(base / "eq", "price_daily",
                     [{"ticker": tk, "date": T_DATE, "basis": "krx", "base_price_krw": int(str(b))}
                      for tk, b in sorted(bases.items())], build_id=EQ_T)
    write_fi(data / "factor_inputs", rd, basis="morning", bid=RD_FI, day=DP, asof=DP,
             latest=False, equity_builds=pinned)
    write_fi(data / "factor_inputs", rs, basis="morning", bid=RS_FI, day=T, asof=T,
             equity_root=str(base / "eq" / "stage"),
             equity_builds={"price_daily": EQ_T, "adj_factor": EQ_T})
    res = model_build(T_S, "morning", data / "model", data / "factor_inputs", **SMALL)
    assert res.ok, res.summary()

    mdb = base / "model_db"
    write_fi(mdb / "factor_inputs", ev, basis="evening", bid=EV_FI, day=T, asof=DP,
             builds_from_date=DP_S, equity_builds=pinned,
             postclose_stage_root=str(mdb / "stage"),
             postclose_builds={bc.T_SOURCE_TABLE: PC_BID})
    stage = [{"ticker": str(r["ticker"]), "date": T_DATE,
              "price_valid": r["ticker"] not in stage_invalid, "close_krw": 10_000,
              "pred_pre_krw": None if r["ticker"] in stage_no_pred else 0, "volume_shr": 1_000}
             for r in ev["fi_universe"] if r["ticker"] not in stage_missing]
    _make_stage_tree(mdb, bc.T_SOURCE_TABLE, stage, build_id=PC_BID)
    res = model_build(T_S, "evening", mdb / "model", mdb / "factor_inputs", **SMALL)
    assert res.ok, res.summary()
    return mdb, data


@pytest.fixture(scope="module")
def research_tables() -> dict[str, list[dict[str, object]]]:
    return _tables(board_fi(N_STOCKS + 1))


def _baseline(research: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    """D' 시점 기준 보드 — 신선한 정보의 날짜를 D' 로 둔다(장 마감 판 = 연구 판 D')."""
    base = copy.deepcopy(research)
    for r in _rows(base, "fi_consensus", P_INFO):
        r["fetched_date"] = DP_DATE
    for r in _rows(base, "fi_fin_summary", P_FIN):
        r["available_date"] = DP_DATE
    for r in _rows(base, "fi_credit", P_CREDIT, DP_DATE):
        r["available_date"] = T_DATE                 # D' 잔고는 T 에 실입수(랙)
    return base


def _plant(research: dict[str, list[dict[str, object]]]) -> tuple[dict[str, Any], ...]:
    """범주마다 증거가 있는 차이 하나 — (연구 T, 장 마감, 연구 D')."""
    base = _baseline(research)
    rs = copy.deepcopy(base)
    ev = _evening_copy(base, drop=(P_NEW,))           # 이월: T 신규 상장은 장 마감 판에 없다
    rd = _dprime_of(base, drop=(P_NEW,))
    # 거래량 정의 — 15:41 누적 ≤ KRX 일 거래량
    for r in _rows(rs, "fi_prices", P_VOL, T_DATE):
        r["volume"] = 1_000
    for r in _rows(ev, "fi_prices", P_VOL, T_DATE):
        r["volume"] = 900
    # 수급 정의 — T 행 외국인(주체 판 통계는 상한 안)
    for r in _rows(ev, "fi_flows", P_FLOW, T_DATE):
        r["foreign_investor"] = float(str(r["foreign_investor"])) + 3.0
    # 이월 — T 에 정지(연구 판 T 만)
    for r in _rows(rs, "fi_universe", P_HALT):
        r["is_halted"] = True
    # 정보 시점 — WISE 컨센서스 T 판 · 추정기관 수 · WICS · 재무(T 공시)
    for r in _rows(rs, "fi_consensus", P_INFO):
        r["fetched_date"] = T_DATE
        if r["horizon"] == "cur":
            r["op"] = float(str(r["op"])) * 2
    for r in _rows(rs, "fi_universe", P_INFO):
        r["n_analysts"] = 9
    for r in _rows(rs, "fi_universe", P_SECTOR):
        r["sector_l2"] = "G1099"
    for r in _rows(rs, "fi_fin_summary", P_FIN):
        r["available_date"] = T_DATE
        if r["period_type"] == "annual":
            r["ni"] = float(str(r["ni"])) + 4.0
    # 정보 시점 — (D', T] 에 공개된 기업행위: 연구 판 계수가 적용일부터 T 까지 1.5
    for r in _rows(rs, "fi_adj_prices", P_EV):
        if r["date"] >= EV_FROM:  # type: ignore[operator]
            r.update(adj_factor=1.5, adj_close=float(str(r["adj_close"])) * 1.5)
    # T-6 — 장 마감 판 보류, 연구 판 T 행에 새 계수(액면분할 2:1)
    for r in _rows(ev, "fi_universe", P_T6):
        r.update(eligible=False, exclude_reason=bc.T6_REASON)
    for r in _rows(rs, "fi_adj_prices", P_T6, T_DATE):
        r.update(adj_factor=2.0, adj_close=float(str(r["adj_close"])) * 2)
    # 16:00 컷오프 — stage 에 행이 없다 → 장 마감 판에 T 가격·수정주가·수급이 없다
    for table in ("fi_prices", "fi_adj_prices", "fi_flows"):
        ev[table] = [r for r in ev[table] if not (r["ticker"] == P_CUT and r["date"] == T_DATE)]
    for r in _rows(ev, "fi_universe", P_CUT):
        r.update(eligible=False, exclude_reason="no_price", market_cap=None)
    # 신용 available_date ≤ T — T 에 실입수한 행이 장 마감 판에 없다
    ev["fi_credit"] = [r for r in ev["fi_credit"]
                       if not (r["ticker"] == P_CREDIT and r["available_date"] == T_DATE)]
    return rs, ev, rd


@pytest.fixture(scope="module")
def same_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    """차이 없는 세 판(구조 표식만 다르다)."""
    base = _baseline(research_tables)
    return _pair(tmp_path_factory.mktemp("same"), copy.deepcopy(base), _evening_copy(base),
                 _dprime_of(base))


@pytest.fixture(scope="module")
def planted_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    rs, ev, rd = _plant(research_tables)
    return _pair(tmp_path_factory.mktemp("planted"), rs, ev, rd, stage_missing=(P_CUT,),
                 events=((P_EV, EV_FROM),))


@pytest.fixture(scope="module")
def unexplained_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    """범주 밖 차이 1건 — T 전 행(09-20) 종가가 다르다."""
    base = _baseline(research_tables)
    ev = _evening_copy(base)
    for r in _rows(ev, "fi_prices", P_PRE_T, dt.date(2026, 9, 20)):
        r["close"] = int(str(r["close"])) + 1
    return _pair(tmp_path_factory.mktemp("unexplained"), copy.deepcopy(base), ev,
                 _dprime_of(base))


def run_cli(pair: tuple[Path, Path], out: Path | None = None, *extra: str) -> int:
    ev, rs = pair
    argv = ["--date", T_S, "--evening-root", str(ev), "--research-root", str(rs)]
    if out is not None:
        argv += ["--out-root", str(out)]
    return bc.main([*argv, *extra])


def report(root: Path) -> dict[str, Any]:
    return json.loads((root / "compare" / f"{T_S}.json").read_text(encoding="utf-8"))


def cats(rep: dict[str, Any]) -> dict[str, int]:
    return {k: int(v["count"]) for k, v in rep["categories"].items()}


# ── 판정 ─────────────────────────────────────────────────────────────────────
def test_same_boards_pass_with_no_difference(same_pair, capsys) -> None:
    """구조 표식(시총 기준·T 행 출처·T 행 시·고·저가)만 다른 판 — 차이 0, Spearman 1, rc 0.
    JSON 은 출력 루트의 compare/<T>.json(기본 = 장 마감 루트)."""
    assert run_cli(same_pair) == 0
    out = capsys.readouterr().out
    assert "판정 pass" in out and f"compare/{T_S}.json" in out
    rep = report(same_pair[0])
    assert (rep["verdict"], rep["rc"], rep["date"], rep["dprime"], rep["replay"]) == (
        "pass", 0, T, DP, False)
    assert rep["n_unexplained"] == 0 and set(cats(rep).values()) == {0}
    assert all(t["n_rows_diff"] == 0 for t in rep["fi"].values()), rep["fi"]
    assert {n for n, t in rep["fi"].items() if not t["hash_equal"]} == {"fi_universe", "fi_prices"}
    boards = rep["boards"]
    assert (boards["evening"]["fi_build_id"], boards["research"]["fi_build_id"],
            boards["research_dprime"]["fi_build_id"]) == (EV_FI, RS_FI, RD_FI)
    for sid, m in rep["model"].items():
        assert m["spearman"] == pytest.approx(1.0), sid
        assert m["candidates"]["overlap"] == len(m["candidates"]["evening"]), sid
    assert rep["flow_stats"]["foreign_investor"]["n"] >= bc.FLOW_STAT_MIN_N


def test_each_registered_category_is_classified_with_evidence(planted_pair, tmp_path) -> None:
    """범주마다 증거가 있는 차이가 그 범주로 간다. 미설명 0 → rc 0(Spearman 하한은 따로 본다)."""
    rc = run_cli(planted_pair, tmp_path, "--spearman-min", "0")
    rep = report(tmp_path)
    assert rep["unexplained"] == [] and rep["n_unexplained"] == 0, rep["unexplained"]
    assert rc == 0, rep["reasons"]
    got = cats(rep)
    for key in (bc.VOLUME_DEF, bc.FLOW_DEF, bc.CARRY, bc.INFO, bc.T6, bc.CUTOFF, bc.CREDIT_T):
        assert got[key] >= 1, (key, got)
    assert got[bc.CLOSE_DEF] == got[bc.FY] == got[bc.FILING] == got[bc.NOT_TARGETED] == 0
    tick = rep["ticker_categories"]
    assert tick[P_VOL] == [bc.VOLUME_DEF] and tick[P_FLOW] == [bc.FLOW_DEF]
    assert tick[P_HALT] == [bc.CARRY] and tick[P_NEW] == [bc.CARRY]
    for tk in (P_INFO, P_SECTOR, P_FIN, P_EV):
        assert tick[tk] == [bc.INFO], tk
    assert tick[P_T6] == [bc.T6] and tick[P_CUT] == [bc.CUTOFF]
    assert tick[P_CREDIT] == [bc.CREDIT_T]
    assert rep["categories"][bc.CARRY]["n_tickers"] == 2 < rep["categories"][bc.CARRY]["count"]
    assert rep["fi"]["fi_consensus_annual"]["hash_equal"] is True


def test_model_differences_trace_back_to_fi_categories(planted_pair, tmp_path) -> None:
    """한 판에만 있는 점수 행은 적격성 범주로 설명되고, 목록 종목에는 그 spec 엔진이 읽는 표의 fi
    범주만 붙는다(v3 엔진은 fi_credit 을 읽지 않는다)."""
    run_cli(planted_pair, tmp_path, "--spearman-min", "0")
    rep = report(tmp_path)
    scope = rep["model"]["scope@1.0"]
    only = {e["ticker"]: e["categories"] for e in scope["research_only"]}
    assert only == {P_T6: [bc.T6], P_CUT: [bc.CUTOFF], P_NEW: [bc.CARRY]}
    assert scope["evening_only"] == [] and scope["n_common"] == N_STOCKS - 2
    cand = scope["candidates"]
    for entry in [*scope["research_only"], *cand["entered"], *cand["dropped"],
                  *scope["composite"]["top"],
                  *(e for f in scope["factors"].values() for e in f["top"])]:
        assert entry["categories"] or entry["trace"] == bc.CROSS_SECTION
        assert bc.CREDIT_T not in entry["categories"], entry
    for col, tk in {"op_change_1m": P_INFO, "flow_for_5d": P_FLOW}.items():
        top = scope["factors"][col]["top"][0]
        assert (top["ticker"], top["categories"]) == (tk, [bc.INFO if tk == P_INFO
                                                           else bc.FLOW_DEF]), (col, top)


def test_one_unexplained_difference_gives_rc_1(unexplained_pair, tmp_path, capsys) -> None:
    assert run_cli(unexplained_pair, tmp_path) == 1
    out = capsys.readouterr().out
    assert "판정 fail" in out and "미설명" in out and P_PRE_T in out
    rep = report(tmp_path)
    assert (rep["verdict"], rep["rc"], rep["n_unexplained"]) == ("fail", 1, 1)
    (u,) = rep["unexplained"]
    assert (u["table"], u["ticker"], u["key"]["date"], u["columns"]) == (
        "fi_prices", P_PRE_T, "2026-09-20", ["close"])


def test_spearman_floor_is_a_gate(planted_pair, tmp_path) -> None:
    """미설명 0 이어도 Spearman 이 하한(기본 0.975 임시) 밑이면 rc 1. 0 이면 기록형."""
    assert run_cli(planted_pair, tmp_path) == 1
    rep = report(tmp_path)
    assert rep["n_unexplained"] == 0 and rep["thresholds"]["spearman_min"] == bc.SPEARMAN_MIN
    low = {sid for sid, m in rep["model"].items() if not m["spearman_ok"]}
    assert "scope@1.0" in low
    assert all(any(sid in r and "Spearman" in r for r in rep["reasons"]) for sid in low)
    assert run_cli(planted_pair, tmp_path, "--spearman-min", "0") == 0


def test_window_judge_reads_the_real_reports(same_pair, unexplained_pair, tmp_path) -> None:
    """X-2(`daily.window_judge.read_compare`)가 이 도구의 실제 `compare/<T>.json` 을 읽는다 — 키
    (schema·tool·date·verdict·rc·n_unexplained·reasons·replay·thresholds.spearman_min) 계약.
    실운영 pass·fail 은 판정 재료, 재생(`--replay`)·기록형 하한(`--spearman-min 0`)의 pass 는 거절."""
    from daily import window_judge as wj
    assert (wj.COMPARE_SCHEMA, wj.COMPARE_TOOL, wj.COMPARE_SPEARMAN_MIN) == (
        bc.SCHEMA, bc.TOOL, bc.SPEARMAN_MIN)

    def judged(pair: tuple[Path, Path], name: str, *extra: str) -> dict[str, object] | None:
        run_cli(pair, tmp_path / name, *extra)
        return wj.read_compare(tmp_path / name / "compare" / f"{T_S}.json", T_DATE)

    ok = judged(same_pair, "pass")
    assert ok is not None and (ok["verdict"], ok["rc"], ok["n_unexplained"]) == ("pass", 0, 0)
    bad = judged(unexplained_pair, "fail")
    assert bad is not None and (bad["verdict"], bad["rc"]) == ("fail", 1) and bad["reasons"]
    for name, extra in (("replay", ("--replay",)), ("record", ("--spearman-min", "0"))):
        with pytest.raises(wj.InputError, match="replay" if name == "replay" else "spearman_min"):
            judged(same_pair, name, *extra)
    ev, rs = _copy_pair(same_pair, tmp_path / "err")
    (rs / "calendar" / "kis_holidays_2026.json").unlink()
    assert bc.main(["--date", T_S, "--evening-root", str(ev), "--research-root", str(rs)]) == 2
    err = wj.read_compare(ev / "compare" / f"{T_S}.json", T_DATE)
    assert err is not None and (err["verdict"], err["rc"]) == ("error", 2)


def test_default_spearman_floor_is_the_temporary_t36_value() -> None:
    assert bc.SPEARMAN_MIN == 0.975 and bc.FLOW_REL_MAX == bc.FLOW_FLIP_MAX == 0.10


# ── 실제 빌더가 쓰는 판 기록 ─────────────────────────────────────────────────
def test_reads_the_run_records_the_real_builders_write(tmp_path) -> None:
    """판 기록 키(status·basis·date·build_id·asof·rules_version)는 실제 `factor_inputs.build`·
    `model.build` 가 쓰는 그대로 읽는다 — 합성 원천(`test_factor_inputs.make_roots`) 위 아침판."""
    from factor_inputs import build as fi_build
    from test_factor_inputs import D_S, D, make_roots

    eq, st = make_roots(tmp_path / "src")
    with allow_skips(("factor_inputs", "FG4", "no_fixtures",
                      "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")):
        fi = fi_build(D_S, "morning", tmp_path / "data" / "factor_inputs", st, eq, min_eligible=5,
                      golden_path=None)
    assert fi.ok, fi.summary()
    res = model_build(D_S, "morning", tmp_path / "data" / "model", tmp_path / "data" /
                      "factor_inputs", specs=["v2_percentrank@1.0"],
                      primary="v2_percentrank@1.0", min_prices_on_d=1)
    assert res.ok
    side = bc.load_side(tmp_path / "data", "morning", D)
    assert (side.fi.build_id, side.model_run.build_id) == (fi.build_id, res.build_id)
    run = side.fi_run
    assert (run["asof"], run["rules_version"]) == (D.isoformat(), FI_RULES)
    assert isinstance(run["equity_builds"], dict) and "adj_factor" in run["equity_builds"]
    assert run["equity_root"]


# ── 입력 오류(rc 2) ──────────────────────────────────────────────────────────
def _copy_pair(pair: tuple[Path, Path], dest: Path) -> tuple[Path, Path]:
    ev, rs = pair
    return (Path(shutil.copytree(ev, dest / "model_db")), Path(shutil.copytree(rs, dest / "data")))


def _edit_json(path: Path, **vals: object) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj.update(vals)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _edit_manifest_rules(path: Path, rules: str) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    for b in obj["builds"]:
        b["rules_version"] = rules
    path.write_text(json.dumps(obj), encoding="utf-8")


def _add_column(part: Path) -> None:
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT *, 1 AS extra FROM read_parquet('{part}')) TO '{part}.x' "
                    "(FORMAT PARQUET)")
    finally:
        con.close()
    Path(f"{part}.x").replace(part)


INPUT_ERRORS: dict[str, tuple[Callable[[Path, Path], None], list[str]]] = {
    "research_model_missing": (lambda ev, rs: [
        (rs / "model" / "_runs" / f"{T_S}_morning.json").unlink(),
        (rs / "model" / "latest_morning.json").unlink()], ["model", "판이 없다"]),
    "evening_fi_missing": (lambda ev, rs: (ev / "factor_inputs" / "_runs" /
                                           f"{T_S}_evening.json").unlink(),
                           ["factor_inputs", "_runs", "없다"]),
    "evening_fi_failed": (lambda ev, rs: _edit_json(
        ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json", status="gate_failed"),
        ["status", "gate_failed"]),
    "evening_basis_morning": (lambda ev, rs: _edit_json(
        ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json", basis="morning"),
        ["basis", "evening"]),
    "asof_not_prev_session": (lambda ev, rs: _edit_json(
        ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json", asof="2026-09-24"),
        ["asof", "직전 거래일"]),
    "builds_from_not_asof": (lambda ev, rs: _edit_json(
        ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json", builds_from_date="20260924"),
        ["builds_from_date"]),
    "calendar_missing": (lambda ev, rs: shutil.rmtree(rs / "calendar"), ["daily.calendar"]),
    "research_asof_not_t": (lambda ev, rs: _edit_json(
        rs / "factor_inputs" / "_runs" / f"{T_S}_morning.json", asof=DP), ["연구 판 asof"]),
    "research_dprime_missing": (lambda ev, rs: (rs / "factor_inputs" / "_runs" /
                                                f"{DP_S}_morning.json").unlink(),
                                ["연구 판 D'", "없다"]),
    "research_dprime_asof": (lambda ev, rs: _edit_json(
        rs / "factor_inputs" / "_runs" / f"{DP_S}_morning.json", asof=T), ["연구 판 D' asof"]),
    "model_points_other_fi": (lambda ev, rs: _edit_json(
        rs / "model" / "_runs" / f"{T_S}_morning.json", fi_build_id="m_20260101T000000_000000Z"),
        ["fi_build_id"]),
    "fi_rules_differ": (lambda ev, rs: _edit_json(
        rs / "factor_inputs" / "_runs" / f"{DP_S}_morning.json", rules_version="fi0.0.0"),
        ["rules_version"]),
    "model_rules_differ": (lambda ev, rs: _edit_manifest_rules(
        ev / "model" / "scope@1.0" / "MANIFEST.json", "mb0.0.0"), ["scope@1.0", "rules_version"]),
    "schema_differs": (lambda ev, rs: _add_column(
        ev / "factor_inputs" / "fi_credit" / f"v={EV_FI}" / "part0.parquet"),
        ["스키마", "fi_credit"]),
    "postclose_builds_missing": (lambda ev, rs: _edit_json(
        ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json", postclose_builds=None),
        ["postclose_builds"]),
    "stage_build_gone": (lambda ev, rs: (
        _edit_json(ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json",
                   postclose_stage_root=str(ev / "stage")),
        shutil.rmtree(ev / "stage")), ["장 마감 stage"]),
    "equity_adj_factor_gone": (lambda ev, rs: _edit_json(
        rs / "factor_inputs" / "_runs" / f"{T_S}_morning.json",
        equity_builds={"adj_factor": "m_gone"}), ["adj_factor"]),
}


@pytest.mark.parametrize("case", sorted(INPUT_ERRORS))
def test_input_errors_give_rc_2(same_pair, tmp_path, capsys, case: str) -> None:
    ev, rs = _copy_pair(same_pair, tmp_path)
    edit, words = INPUT_ERRORS[case]
    edit(ev, rs)
    rc = bc.main(["--date", T_S, "--evening-root", str(ev), "--research-root", str(rs)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert all(w in err for w in words), err
    rep = report(ev)                         # rc 2 도 판정 파일을 남긴다(X-2 가 '대조 실패'로 센다)
    assert (rep["verdict"], rep["rc"]) == ("error", 2) and rep["error"]


def test_report_write_failure_is_rc_2(same_pair, tmp_path, capsys) -> None:
    """보고서를 못 쓰면 판정이 남지 않는다 — '대조 못 함'(rc 2), 미설명(rc 1)으로 읽히지 않게."""
    bad = tmp_path / "out_is_a_file"
    bad.write_text("x")
    assert run_cli(same_pair, bad) == 2
    assert "보고서를 쓰지 못했다" in capsys.readouterr().err


def test_bad_date_is_rc_2_without_report(same_pair, tmp_path, capsys) -> None:
    ev, rs = same_pair
    assert bc.main(["--date", "2026-09-28", "--evening-root", str(ev), "--research-root",
                    str(rs), "--out-root", str(tmp_path)]) == 2
    assert "YYYYMMDD" in capsys.readouterr().err
    assert not (tmp_path / "compare").exists()


# ── 모델 층 음성(판 파일을 고쳐 증거 없는 점수 차이를 만든다) ────────────────────────
def _scores(root: Path, spec: str) -> Path:
    run = json.loads((root / "model" / "_runs" / f"{T_S}_evening.json").read_text())
    return root / "model" / spec / f"v={run['build_id']}" / "scores.parquet"


def _rewrite(path: Path, sql: str) -> None:
    """점수 parquet 을 SQL(`{src}` = 원래 파일)로 고쳐 쓴다."""
    con = duckdb.connect()
    try:
        query = sql.replace("{src}", f"read_parquet('{path}')")
        con.execute(f"COPY ({query}) TO '{path}.x' (FORMAT PARQUET)")
    finally:
        con.close()
    Path(f"{path}.x").replace(path)


def _notes(rep: dict[str, Any]) -> list[str]:
    return [u["note"] for u in rep["unexplained"]]


def test_model_row_only_in_one_board_without_eligibility_is_unexplained(same_pair,
                                                                       tmp_path) -> None:
    ev, rs = _copy_pair(same_pair, tmp_path)
    _rewrite(_scores(ev, "scope@1.0"), "SELECT * FROM {src} WHERE stock_code <> '100012'")
    assert run_cli((ev, rs), tmp_path) == 1
    notes = _notes(report(tmp_path))
    assert any("적격성 차이" in n for n in notes) and any("fi 8표가 같은데" in n for n in notes)


def test_composite_null_on_one_side_without_own_fi_difference_is_unexplained(
        planted_pair, tmp_path) -> None:
    ev, rs = _copy_pair(planted_pair, tmp_path)
    _rewrite(_scores(ev, "v4_rank@0.1"),
             "SELECT * REPLACE (CASE WHEN ticker = '100030' THEN NULL ELSE composite END "
             "AS composite) FROM {src}")
    run_cli((ev, rs), tmp_path, "--spearman-min", "0")
    rep = report(tmp_path)
    assert any(u["table"] == "model:v4_rank@0.1" and u["ticker"] == "100030"
               and "종합점수가 비었는데" in u["note"] for u in rep["unexplained"]), rep["unexplained"]


def test_spec_on_one_side_is_unexplained_with_the_exclusion_reason(same_pair, tmp_path) -> None:
    ev, rs = _copy_pair(same_pair, tmp_path)
    path = ev / "model" / "_runs" / f"{T_S}_evening.json"
    run = json.loads(path.read_text())
    run["specs"].pop("v4_rank@0.2")
    run["excluded_specs"] = {"v4_rank@0.2": {"error": "ZeroDivisionError: 지표 분모 0"}}
    path.write_text(json.dumps(run))
    assert run_cli((ev, rs), tmp_path) == 1
    (u,) = [u for u in report(tmp_path)["unexplained"] if u["table"] == "model:v4_rank@0.2"]
    assert u["kind"] == "research_only" and "ZeroDivisionError" in u["note"]


def test_evening_pinned_other_equity_builds_is_unexplained(same_pair, tmp_path) -> None:
    ev, rs = _copy_pair(same_pair, tmp_path)
    _edit_json(ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json",
               equity_builds={"price_daily": "m_other", "adj_factor": EQ_D})
    assert run_cli((ev, rs), tmp_path) == 1
    (u,) = [u for u in report(tmp_path)["unexplained"] if u["table"] == "boards"]
    assert u["columns"] == ["equity_builds"]


# ── T 행 종가·수급 상한·컷오프(41종목 판) ────────────────────────────────────
def _variant(tmp: Path, research: dict[str, list[dict[str, object]]],
             mutate: Callable[[dict[str, Any], dict[str, Any]], None],
             **kw: Any) -> tuple[Path, Path]:
    base = _baseline(research)
    rs, ev = copy.deepcopy(base), _evening_copy(base)
    mutate(rs, ev)
    return _pair(tmp, rs, ev, _dprime_of(base), **kw)


def _close_up(rs: dict[str, Any], ev: dict[str, Any]) -> None:
    for t, col in (("fi_prices", "close"), ("fi_adj_prices", "adj_close")):
        for r in _rows(ev, t, "100025", T_DATE):
            r[col] = type(r[col])(round(float(r[col]) * 1.03))


def test_model_row_on_one_side_is_explained_only_by_eligibility_findings(research_tables,
                                                                           tmp_path) -> None:
    """한 판에만 있는 점수 행은 적격성 범주(종목 집합·eligible·시총·T 가격)로만 설명한다 — 그 종목의
    fi 차이가 정보 시점(WICS 업종)뿐이면 점수 행이 빠진 것은 미설명이다(재리뷰 MINOR-1 ①)."""
    def sector(rs: dict[str, Any], ev: dict[str, Any]) -> None:
        for r in _rows(rs, "fi_universe", "100030"):
            r["sector_l2"] = "G1099"
    ev_root, rs_root = _variant(tmp_path / "b", research_tables, sector)
    _rewrite(_scores(ev_root, "scope@1.0"), "SELECT * FROM {src} WHERE stock_code <> '100030'")
    assert run_cli((ev_root, rs_root), tmp_path, "--spearman-min", "0") == 1
    rep = report(tmp_path)
    assert rep["ticker_categories"]["100030"] == [bc.INFO]
    (u,) = rep["unexplained"]
    assert (u["table"], u["ticker"], u["kind"]) == ("model:scope@1.0", "100030", "research_only")
    assert "적격성 차이" in u["note"]


def test_t_close_difference_is_unexplained_live_and_close_definition_in_replay(
        research_tables, tmp_path) -> None:
    """N-35 ① — 실운영 T 종가는 공식 종가와 같아야 한다(T-36). 재생(T 행 = 21:05 원장)만 종가 정의."""
    pair = _variant(tmp_path / "b", research_tables, _close_up)
    assert run_cli(pair, tmp_path / "live", "--spearman-min", "0") == 1
    live = report(tmp_path / "live")
    assert {(u["table"], u["ticker"]) for u in live["unexplained"]} >= {("fi_prices", "100025")}
    assert run_cli(pair, tmp_path / "replay", "--replay", "--spearman-min", "0") == 0
    rep = report(tmp_path / "replay")
    assert rep["replay"] is True and rep["ticker_categories"]["100025"] == [bc.CLOSE_DEF]


@pytest.mark.parametrize(("t", "dp", "want"), [
    (dt.date(2026, 9, 11), dt.date(2026, 9, 10), bc.UNEXPLAINED),    # 애프터마켓 시행 전(금)
    (dt.date(2026, 9, 14), dt.date(2026, 9, 11), bc.CLOSE_DEF)])     # 시행일(월)부터
def test_replay_close_definition_starts_with_the_after_market(t: dt.date, dp: dt.date,
                                                              want: str) -> None:
    """PR-8b 리뷰 MINOR-2 — 재생 T 종가 차이는 T ≥ 2026-09-14(KRX 애프터마켓 시행일)일 때만 종가 정의다.
    그 전 날은 21:05 키움 종가 = 정규장 종가라 재생에서도 미설명이다. 실운영은 날짜와 무관하게 미설명."""
    def verdict(replay: bool) -> tuple[str, str]:
        return bc._Ctx(t, dp, replay, bc.Evidence({}, {}, frozenset()),
                       {}, {}, {}, {}, {}, {}, {}, {}).close_verdict()
    assert bc.AFTER_MARKET_START == dt.date(2026, 9, 14)
    cat, note = verdict(True)
    assert cat == want
    assert ("애프터마켓 시행일" in note) == (want == bc.UNEXPLAINED)
    assert verdict(False)[0] == bc.UNEXPLAINED


def test_replay_close_difference_before_the_after_market_is_unexplained(tmp_path,
                                                                        monkeypatch) -> None:
    """같은 T 종가 차이를 재생으로 대조해도, T 가 애프터마켓 시행일 전이면(시행일을 T 다음 날로 옮겨
    본다) 종가 정의가 아니라 미설명이다 — `compare_fi` 가 T 를 판정에 넘긴다."""
    def close_up(e: dict[str, Any], r: dict[str, Any], d: dict[str, Any]) -> None:
        _on(e, "fi_prices", A, T_DATE, close=10_050)
    assert _cats(_fi3(tmp_path / "after", close_up, replay=True))[("fi_prices", A)] == {
        bc.CLOSE_DEF}
    monkeypatch.setattr(bc, "AFTER_MARKET_START", T_DATE + dt.timedelta(days=1))
    res = _fi3(tmp_path / "before", close_up, replay=True)
    assert _cats(res)[("fi_prices", A)] == {bc.UNEXPLAINED}
    assert any("애프터마켓 시행일" in f.note for f in res.tally.samples[bc.UNEXPLAINED])


def test_flow_unit_or_subject_swap_breaks_the_flow_cap(research_tables, tmp_path) -> None:
    """단위(×1e6)·주체 열 뒤바뀜 — 주체 판 통계가 상한을 넘거나 한쪽만 값이 있어 미설명."""
    def swap(rs: dict[str, Any], ev: dict[str, Any]) -> None:
        for r in ev["fi_flows"]:
            if r["date"] == T_DATE and r["foreign_investor"] is not None:
                r["foreign_investor"] = float(r["foreign_investor"]) * 1e6
    assert run_cli(_variant(tmp_path / "b", research_tables, swap), tmp_path) == 1
    rep = report(tmp_path)
    assert rep["flow_stats"]["foreign_investor"]["over"] is True
    assert rep["categories"][bc.FLOW_DEF]["count"] == 0


@pytest.mark.parametrize(("kw", "want"), [
    ({"stage_missing": ("100025",)}, bc.CUTOFF),
    ({"stage_invalid": ("100025",)}, bc.CUTOFF),
    ({}, bc.UNEXPLAINED),                     # stage 에 유효 가격이 있다 — 결함
])
def test_missing_t_price_needs_stage_evidence(research_tables, tmp_path, kw: dict[str, Any],
                                              want: str) -> None:
    def drop(rs: dict[str, Any], ev: dict[str, Any]) -> None:
        for t in ("fi_prices", "fi_adj_prices"):
            ev[t] = [r for r in ev[t] if not (r["ticker"] == "100025" and r["date"] == T_DATE)]
        if not kw.get("stage_invalid"):
            ev["fi_flows"] = [r for r in ev["fi_flows"]
                              if not (r["ticker"] == "100025" and r["date"] == T_DATE)]
        for r in _rows(ev, "fi_universe", "100025"):
            r.update(eligible=False, exclude_reason="no_price", market_cap=None)
    pair = _variant(tmp_path / "b", research_tables, drop, **kw)
    run_cli(pair, tmp_path, "--spearman-min", "0")
    rep = report(tmp_path)
    assert want in rep["ticker_categories"]["100025"], rep["ticker_categories"]["100025"]
    assert set(rep["ticker_categories"]["100025"]) == {want}


# ── fi 분류 경계(작은 판 셋 — 모델 없이 `compare_fi`) ─────────────────────────
A, B, C = "200010", "200020", "200030"      # C 는 우선주·직전 판 후보 아님 → 수집 대상 밖
PRE = (dt.date(2026, 9, 21), dt.date(2026, 9, 22), dt.date(2026, 9, 23), dt.date(2026, 9, 24),
       DP_DATE)


def _mini() -> dict[str, list[dict[str, object]]]:
    """세 종목 · T 전 5세션 + T. 기준(D' 시점) 판 — 장 마감·연구 T·연구 D' 가 여기서 갈라진다."""
    tables: dict[str, list[dict[str, object]]] = {n: [] for n in FI_TABLES}
    for tk in (A, B, C):
        pref = tk == C
        tables["fi_universe"].append(_r(
            "fi_universe", ticker=tk, date=T_DATE, name=tk, market="KOSPI",
            sec_type="preferred" if pref else "common", shares=1_000, market_cap=5_000.0,
            mktcap_basis="krx", sector_l2="G1010", has_estimates=True, coverage_state="fresh",
            coverage_age_days=0, n_analysts=5, is_halted=False, filing_late=False,
            eligible=not pref, exclude_reason="sec_type" if pref else None))
        for day in (*PRE, T_DATE):
            tables["fi_prices"].append(_r("fi_prices", ticker=tk, date=day, close=10_000,
                                          volume=100, price_source="krx"))
            tables["fi_adj_prices"].append(_r("fi_adj_prices", ticker=tk, date=day,
                                              adj_close=10_000.0, adj_factor=1.0, adj_ok=True))
            tables["fi_flows"].append(_r("fi_flows", ticker=tk, date=day, foreign_investor=5.0))
        tables["fi_consensus_annual"].append(_r(
            "fi_consensus_annual", ticker=tk, period="2026/12", data_type="E", op=1.0, ni=1.0,
            fetched_date=DP_DATE))
        tables["fi_credit"].append(_r("fi_credit", ticker=tk, date=PRE[0], credit_balance=10,
                                      available_date=PRE[3]))
    return tables


Mut = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]


def _fi3(tmp: Path, mutate: Mut, *, t: dt.date = T_DATE, dp: dt.date = DP_DATE,
         stage: dict[str, str] | None = None,
         events: dict[str, tuple[dt.date, ...]] | None = None,
         krx_base: dict[str, int] | None = None, no_pred: frozenset[str] = frozenset(),
         replay: bool = False) -> bc.FiResult:
    """기준 판 → (장 마감 e, 연구 T r, 연구 D' d) 를 `mutate(e, r, d)` 로 고쳐 `compare_fi`. KRX T 기준가는
    기본으로 D' 종가(10,000 — 기업행위 없음), `krx_base` 가 덮는다."""
    base = _mini()
    e, r = copy.deepcopy(base), copy.deepcopy(base)
    d = {k: [dict(x) for x in v if k not in bc.DATED_TABLES or x["date"] < t]
         for k, v in base.items()}
    for x in d["fi_universe"]:
        x["date"] = dp
    mutate(e, r, d)
    iso = t.isoformat()
    boards = []
    for name, tables, basis, bid, day in (("e", e, "evening", EV_FI, iso),
                                          ("r", r, "morning", RS_FI, iso),
                                          ("d", d, "morning", RD_FI, dp.isoformat())):
        write_fi_tree(tmp / name, FactorInputs(day, basis, bid, tables), bid, basis=basis, date=day)
        boards.append(bc.FiBoard(tmp / name, bid))
    ev = bc.Evidence({A: bc.STAGE_USABLE, B: bc.STAGE_USABLE} if stage is None else stage,
                     events or {}, frozenset({A, B}),     # C 는 우선주·후보 아님 — 수집 대상 밖
                     {A: 10_000, B: 10_000, C: 10_000, **(krx_base or {})}, no_pred)
    return bc.compare_fi(*boards, t, dp, ev, replay=replay)


def _cats(res: bc.FiResult) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for cat, fs in res.tally.samples.items():
        for f in fs:
            out.setdefault((f.table, f.ticker), set()).add(cat)
    return out


def _uni(tables: dict[str, Any], tk: str, **vals: object) -> None:
    for x in _rows(tables, "fi_universe", tk):
        x.update(vals)


def _on(tables: dict[str, Any], table: str, tk: str, day: dt.date, **vals: object) -> None:
    for x in _rows(tables, table, tk, day):
        x.update(vals)


def _drop_t(tables: dict[str, Any], tk: str, *names: str) -> None:
    """그 종목의 T 행을 표들에서 뺀다."""
    for n in names:
        tables[n] = [x for x in tables[n] if not (x["ticker"] == tk and x["date"] == T_DATE)]


def _scale_adj(tables: dict[str, Any], tk: str, start: dt.date, factor: float = 1.5) -> None:
    """그 종목 수정주가의 `start` 부터 계수를 바꾼다(적용일부터 T 까지 — 끝 구간)."""
    for x in _rows(tables, "fi_adj_prices", tk):
        if x["date"] >= start:
            x.update(adj_factor=factor, adj_close=10_000.0 * factor)


Q4_REPORT = dt.date(2026, 3, 20)      # 사업보고서 공개일(D' 훨씬 앞)


def _q4(tables: dict[str, Any], avail: dt.date, revenue: int | None = None) -> None:
    """A 의 DART 4Q 파생 분기 행(2025/12) — 값은 파생값이 섰을 때만 싣는다(fi_fin_summary)."""
    tables["fi_fin_summary"].append(_r("fi_fin_summary", ticker=A, period="2025/12",
                                       period_type="quarter", revenue=revenue,
                                       fs_basis="DART:CFS", available_date=avail))


U = bc.UNEXPLAINED
# (이름, 변경, 기대 {(표, 종목): 범주}, compare_fi 추가 인자) — 범주마다 양성과 '증거 없음' 음성
CASES: list[tuple[str, Mut, dict[tuple[str, str], set[str]], dict[str, Any]]] = [
    # 이월 — 3자 대조(E = D, R ≠ D)
    ("carry_ok", lambda e, r, d: _uni(r, A, is_halted=True),
     {("fi_universe", A): {bc.CARRY}}, {}),
    ("carry_evening_not_dprime", lambda e, r, d: _uni(e, A, shares=500),          # 장 마감 판이 틀림
     {("fi_universe", A): {U}}, {}),
    ("carry_research_unchanged", lambda e, r, d: (_uni(e, A, adv20=1.0), _uni(d, A, adv20=2.0),
                                                  _uni(r, A, adv20=2.0)),
     {("fi_universe", A): {U}}, {}),
    # 정보 시점 — 3자 대조 + 연구 판 날짜 > D'
    ("info_uni_ok", lambda e, r, d: _uni(r, A, sector_l2="G2010"),
     {("fi_universe", A): {bc.INFO}}, {}),
    ("info_uni_no_3way", lambda e, r, d: _uni(e, A, sector_l2="G2010"),
     {("fi_universe", A): {U}}, {}),
    ("info_table_ok", lambda e, r, d: [x.update(op=2.0, fetched_date=T_DATE)
                                       for x in _rows(r, "fi_consensus_annual", A)],
     {("fi_consensus_annual", A): {bc.INFO}}, {}),
    ("info_table_older_research",
     lambda e, r, d: [x.update(op=2.0, fetched_date=dt.date(2026, 9, 24))
                      for x in _rows(r, "fi_consensus_annual", A)],
     {("fi_consensus_annual", A): {U}}, {}),
    ("info_table_same_date", lambda e, r, d: [x.update(op=2.0)
                                              for x in _rows(r, "fi_consensus_annual", A)],
     {("fi_consensus_annual", A): {U}}, {}),
    ("info_table_evening_not_dprime",
     lambda e, r, d: ([x.update(op=3.0) for x in _rows(e, "fi_consensus_annual", A)],
                      [x.update(op=2.0, fetched_date=T_DATE)
                       for x in _rows(r, "fi_consensus_annual", A)]),
     {("fi_consensus_annual", A): {U}}, {}),
    ("info_row_dropped_with_newer_snapshot", lambda e, r, d: (
        r.__setitem__("fi_consensus_annual",
                      [x for x in r["fi_consensus_annual"] if x["ticker"] != A]
                      + [_r("fi_consensus_annual", ticker=A, period="2027/12", data_type="E",
                            op=1.0, ni=1.0, fetched_date=T_DATE)])),
     {("fi_consensus_annual", A): {bc.INFO}}, {}),
    ("info_row_dropped_without_newer_snapshot", lambda e, r, d: r.__setitem__(
        "fi_consensus_annual", [x for x in r["fi_consensus_annual"] if x["ticker"] != A]),
     {("fi_consensus_annual", A): {U}}, {}),
    ("info_evening_after_dprime",
     lambda e, r, d: [x.update(fetched_date=T_DATE) for x in _rows(e, "fi_consensus_annual", A)],
     {("fi_consensus_annual", A): {U}}, {}),
    # 4Q 파생 분기 행(T-43 · fi1.8.0) — 1~3Q 정정이 (D', T] 에 들어와 연구 판 T 에 파생값이 처음 실린다.
    # 행 날짜 = 파생값 날짜면 정보 시점, 사업보고서 날짜 그대로면(fi1.7.0 모양 — P5 시험 145720) 미설명
    ("info_fin_q4_derived", lambda e, r, d: (_q4(e, Q4_REPORT), _q4(d, Q4_REPORT),
                                             _q4(r, T_DATE, 800_000)),
     {("fi_fin_summary", A): {bc.INFO}}, {}),
    ("info_fin_q4_derived_report_date", lambda e, r, d: (_q4(e, Q4_REPORT), _q4(d, Q4_REPORT),
                                                         _q4(r, Q4_REPORT, 800_000)),
     {("fi_fin_summary", A): {U}}, {}),
    # filing_late — 3자 대조(또는 기한 ∈ (D', T] 라 장 마감 false · D' NULL)
    ("filing_ok", lambda e, r, d: _uni(r, B, filing_late=True),
     {("fi_universe", B): {bc.FILING}}, {}),
    ("filing_session_axis",
     lambda e, r, d: (_uni(d, B, filing_late=None), _uni(r, B, filing_late=True)),
     {("fi_universe", B): {bc.FILING}}, {}),
    ("filing_no_3way", lambda e, r, d: _uni(e, B, filing_late=True), {("fi_universe", B): {U}}, {}),
    # 장 마감 ≠ 연구 D' 이고 연구 T ≠ 연구 D' — 세션 축 예외(장 마감 false · D' NULL)도 아니다
    ("filing_evening_and_research_both_moved",
     lambda e, r, d: (_uni(d, B, filing_late=True), _uni(r, B, filing_late=None)),
     {("fi_universe", B): {U}}, {}),
    # 시총·적격성
    ("mktcap_carry", lambda e, r, d: _uni(r, A, shares=2_000, market_cap=10_000.0),
     {("fi_universe", A): {bc.CARRY}}, {}),
    ("mktcap_no_evidence", lambda e, r, d: _uni(r, A, market_cap=9_999.0),
     {("fi_universe", A): {U}}, {}),
    ("eligibility_no_evidence",
     lambda e, r, d: _uni(e, A, eligible=False, exclude_reason="estimates_none"),
     {("fi_universe", A): {U}}, {}),
    ("research_t6_reason", lambda e, r, d: _uni(r, A, eligible=False, exclude_reason=bc.T6_REASON),
     {("fi_universe", A): {U}}, {}),
    ("eligibility_research_no_price", lambda e, r, d: (
        _drop_t(r, A, "fi_prices"), _uni(r, A, eligible=False, exclude_reason="no_price")),
     {("fi_universe", A): {U}}, {}),
    ("eligibility_research_halted_at_t", lambda e, r, d: (
        _drop_t(r, A, "fi_prices"),
        _uni(r, A, eligible=False, exclude_reason="no_price", is_halted=True)),
     {("fi_universe", A): {bc.CARRY}}, {}),
    ("unknown_universe_column", lambda e, r, d: _uni(e, A, date=dt.date(2026, 9, 27)),
     {("fi_universe", A): {U}}, {}),
    # T-6 — 정의의 갈래만큼 증거(`_Ctx.t6_verdict`): KRX 기준가·pred_pre·D' 종가·제한폭·계수
    ("t6_krx_base_differs", lambda e, r, d: _uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
     {("fi_universe", A): {bc.T6}}, {"krx_base": {A: 9_990}}),     # 스팩·전일 무거래 모양
    ("t6_pred_pre_missing", lambda e, r, d: _uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
     {("fi_universe", A): {bc.T6}}, {"no_pred": frozenset({A})}),
    ("t6_dprime_close_missing", lambda e, r, d: (
        _uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
        r.__setitem__("fi_prices", [x for x in r["fi_prices"]
                                    if not (x["ticker"] == A and x["date"] == DP_DATE)])),
     {("fi_universe", A): {bc.T6}}, {}),
    ("t6_factor_change", lambda e, r, d: (_uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
                                          _on(r, "fi_adj_prices", A, T_DATE, adj_factor=2.0,
                                              adj_close=20_000.0)),
     {("fi_universe", A): {bc.T6}, ("fi_adj_prices", A): {bc.T6}}, {}),
    ("t6_price_limit", lambda e, r, d: (_uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
                                        _on(r, "fi_prices", A, T_DATE, close=13_500)),
     {("fi_universe", A): {bc.T6}, ("fi_prices", A): {U}}, {}),
    # KRX 기준가도 D' 종가와 같고 pred_pre·D' 종가가 있고 계수·수익률 흔적도 없는데 T-6 — 미설명
    ("t6_no_trace", lambda e, r, d: _uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
     {("fi_universe", A): {U}}, {"krx_base": {A: 10_000}}),
    ("t6_adj_row_without_trace", lambda e, r, d: (
        _uni(e, A, eligible=False, exclude_reason=bc.T6_REASON),
        _on(e, "fi_adj_prices", A, T_DATE, adj_ok=False)),
     {("fi_universe", A): {U}, ("fi_adj_prices", A): {U}}, {}),
    # T 가격 없음 — stage 증거
    ("cutoff_stage_missing", lambda e, r, d: (
        _drop_t(e, A, "fi_prices", "fi_adj_prices", "fi_flows"),
        _uni(e, A, eligible=False, exclude_reason="no_price", market_cap=None)),
     {("fi_universe", A): {bc.CUTOFF}, ("fi_prices", A): {bc.CUTOFF},
      ("fi_adj_prices", A): {bc.CUTOFF}, ("fi_flows", A): {bc.CUTOFF}},
     {"stage": {B: bc.STAGE_USABLE}}),
    ("cutoff_price_invalid", lambda e, r, d: (
        _drop_t(e, A, "fi_prices", "fi_adj_prices"),
        _uni(e, A, eligible=False, exclude_reason="no_price", market_cap=None)),
     {("fi_universe", A): {bc.CUTOFF}, ("fi_prices", A): {bc.CUTOFF},
      ("fi_adj_prices", A): {bc.CUTOFF}}, {"stage": {A: bc.STAGE_INVALID, B: bc.STAGE_USABLE}}),
    ("price_predicate_false_is_not_cutoff", lambda e, r, d: (
        _drop_t(e, A, "fi_prices", "fi_adj_prices"),
        _uni(e, A, eligible=False, exclude_reason="no_price", market_cap=None)),
     {("fi_universe", A): {U}, ("fi_prices", A): {U}, ("fi_adj_prices", A): {U}},
     {"stage": {A: bc.STAGE_UNUSABLE, B: bc.STAGE_USABLE}}),
    ("cutoff_stage_valid", lambda e, r, d: (
        _drop_t(e, A, "fi_prices", "fi_adj_prices"),
        _uni(e, A, eligible=False, exclude_reason="no_price", market_cap=None)),
     {("fi_universe", A): {U}, ("fi_prices", A): {U}, ("fi_adj_prices", A): {U}}, {}),
    ("not_targeted", lambda e, r, d: _drop_t(e, C, "fi_prices", "fi_adj_prices", "fi_flows"),
     {("fi_prices", C): {bc.NOT_TARGETED}, ("fi_adj_prices", C): {bc.NOT_TARGETED},
      ("fi_flows", C): {bc.NOT_TARGETED}}, {}),
    ("flows_missing_with_stage_row", lambda e, r, d: _drop_t(e, A, "fi_flows"),
     {("fi_flows", A): {U}}, {}),
    ("t_row_missing_in_research", lambda e, r, d: _drop_t(r, A, "fi_prices"),
     {("fi_prices", A): {U}}, {}),
    # T 종가·거래량
    ("t_close_live", lambda e, r, d: _on(e, "fi_prices", A, T_DATE, close=10_050),
     {("fi_prices", A): {U}}, {}),
    ("t_close_replay", lambda e, r, d: _on(e, "fi_prices", A, T_DATE, close=10_050),
     {("fi_prices", A): {bc.CLOSE_DEF}}, {"replay": True}),
    ("volume_ok", lambda e, r, d: _on(e, "fi_prices", A, T_DATE, volume=90),
     {("fi_prices", A): {bc.VOLUME_DEF}}, {}),
    ("volume_evening_larger", lambda e, r, d: _on(e, "fi_prices", A, T_DATE, volume=150),
     {("fi_prices", A): {U}}, {}),
    ("flow_null_mismatch", lambda e, r, d: _on(e, "fi_flows", A, T_DATE, foreign_investor=None),
     {("fi_flows", A): {U}}, {}),
    ("pre_t_price", lambda e, r, d: _on(e, "fi_prices", A, PRE[2], close=9_999),
     {("fi_prices", A): {U}}, {}),
    # 수정주가 — (D', T] 공개 사건 · 끝 구간 · 3자 대조
    ("adj_tail_with_event", lambda e, r, d: _scale_adj(r, A, PRE[2]),
     {("fi_adj_prices", A): {bc.INFO}}, {"events": {A: (PRE[2],)}}),
    ("adj_tail_without_event", lambda e, r, d: _scale_adj(r, A, PRE[2]),
     {("fi_adj_prices", A): {U}}, {}),
    ("adj_not_a_tail", lambda e, r, d: _on(r, "fi_adj_prices", A, PRE[2], adj_factor=1.5,
                                           adj_close=15_000.0),
     {("fi_adj_prices", A): {U}}, {"events": {A: (PRE[2],)}}),
    ("adj_close_same_factor_pre_t",
     lambda e, r, d: _on(r, "fi_adj_prices", A, PRE[4], adj_close=9_000.0),
     {("fi_adj_prices", A): {U}}, {"events": {A: (PRE[4],)}}),
    ("t_factor_without_event", lambda e, r, d: _scale_adj(r, A, T_DATE),
     {("fi_adj_prices", A): {U}}, {}),
    ("t_factor_with_event", lambda e, r, d: _scale_adj(r, A, T_DATE),
     {("fi_adj_prices", A): {bc.INFO}}, {"events": {A: (T_DATE,)}}),
    ("t_adj_close_same_factor_and_close", lambda e, r, d: _on(r, "fi_adj_prices", A, T_DATE,
                                                              adj_close=9_000.0),
     {("fi_adj_prices", A): {U}}, {}),
    ("t_flag_without_event", lambda e, r, d: _on(e, "fi_adj_prices", A, T_DATE, adj_ok=False),
     {("fi_adj_prices", A): {U}}, {}),
    ("t_flag_with_event", lambda e, r, d: _on(r, "fi_adj_prices", A, T_DATE, adj_ok=False),
     {("fi_adj_prices", A): {bc.INFO}}, {"events": {A: (T_DATE,)}}),
    ("t_flag_close_replay", lambda e, r, d: (_on(e, "fi_adj_prices", A, T_DATE, adj_ok=False),
                                             _on(e, "fi_prices", A, T_DATE, close=10_050)),
     {("fi_adj_prices", A): {bc.CLOSE_DEF}, ("fi_prices", A): {bc.CLOSE_DEF}}, {"replay": True}),
    # 신용
    ("credit_arrived_at_t", lambda e, r, d: r["fi_credit"].append(_r(
        "fi_credit", ticker=A, date=PRE[1], credit_balance=11, available_date=T_DATE)),
     {("fi_credit", A): {bc.CREDIT_T}}, {}),
    ("credit_lost_in_research", lambda e, r, d: e["fi_credit"].append(_r(
        "fi_credit", ticker=A, date=PRE[1], credit_balance=11, available_date=T_DATE)),
     {("fi_credit", A): {U}}, {}),
    ("credit_before_t", lambda e, r, d: _on(r, "fi_credit", A, PRE[0], credit_balance=99),
     {("fi_credit", A): {U}}, {}),
    # 종목 집합
    ("member_new_at_t", lambda e, r, d: [t.__setitem__(n, [x for x in t[n] if x["ticker"] != B])
                                         for t in (e, d) for n in FI_TABLES],
     {("fi_universe", B): {bc.CARRY}}, {}),
    ("member_dropped_by_evening", lambda e, r, d: e.__setitem__(
        "fi_universe", [x for x in e["fi_universe"] if x["ticker"] != B]),
     {("fi_universe", B): {U}}, {}),
]


@pytest.mark.parametrize(("name", "mutate", "want", "kw"), CASES, ids=[c[0] for c in CASES])
def test_category_needs_evidence(tmp_path, name: str, mutate: Mut,
                                 want: dict[tuple[str, str], set[str]], kw: dict[str, Any]) -> None:
    """범주마다 증거가 있으면 그 범주, 없으면 미설명(T-36 · 리뷰 MAJOR-6 — 생존 변이 목록)."""
    got = _cats(_fi3(tmp_path, mutate, **kw))
    for key, cats_want in want.items():
        assert got.get(key) == cats_want, (name, key, got)


@pytest.mark.parametrize(("t", "dp"), [(dt.date(2027, 1, 4), dt.date(2026, 12, 30)),
                                       (T_DATE, DP_DATE)])
def test_evening_info_after_dprime_is_never_covered(tmp_path, t: dt.date, dp: dt.date) -> None:
    """장 마감 판 정보가 D' 뒤면 D' 자르기 위반 — 연초 첫 거래일의 연도 창 예외(3자 대조 없음)로도 덮지
    않는다."""
    def leak(e: dict[str, Any], r: dict[str, Any], d: dict[str, Any]) -> None:
        for x in _rows(e, "fi_consensus_annual", A):
            x["fetched_date"] = t
    res = _fi3(tmp_path, leak, t=t, dp=dp)
    (f,) = res.tally.samples[bc.UNEXPLAINED]
    assert (f.table, f.ticker) == ("fi_consensus_annual", A) and "D' 자르기 위반" in f.note
    assert res.tally.count[bc.FY] == 0


@pytest.mark.parametrize(("t", "dp", "want_annual", "want_uni"), [
    (dt.date(2027, 1, 4), dt.date(2026, 12, 30), bc.FY, bc.FY),
    (T_DATE, DP_DATE, bc.UNEXPLAINED, bc.INFO)])
def test_fy_window_is_the_only_exception_to_three_way(tmp_path, t: dt.date, dp: dt.date,
                                                      want_annual: str, want_uni: str) -> None:
    """연초 첫 거래일 — 연간 컨센서스 창·신선도 차이는 3자 대조·날짜 방향 없이 연도 창. 해가 같은 날
    같은 차이는 증거대로: 신선도는 3자 대조(E = D', R ≠ D')라 정보 시점, D' 날짜로 연구 판에만 생긴
    연간 컨센서스 행은 날짜가 D' 뒤가 아니라 미설명."""
    def mutate(e: dict[str, Any], r: dict[str, Any], d: dict[str, Any]) -> None:
        for tab in (e, d):
            _uni(tab, A, coverage_state="none", has_estimates=False, eligible=False,
                 exclude_reason="estimates_none")
            for x in _rows(tab, "fi_consensus_annual", A):
                x["period"] = "2025/12"
        _uni(r, A, coverage_state="none", has_estimates=False, eligible=False,
             exclude_reason="estimates_none")
        for x in _rows(r, "fi_universe", A):
            x["coverage_state"] = "fresh"
            x.update(has_estimates=True, eligible=True, exclude_reason=None)
        for x in _rows(r, "fi_consensus_annual", A):
            x["period"] = "2025/12"
        r["fi_consensus_annual"].append(_r("fi_consensus_annual", ticker=A, period="2028/12",
                                           data_type="E", op=1.0, ni=1.0, fetched_date=dp))
    got = _cats(_fi3(tmp_path, mutate, t=t, dp=dp))
    assert got[("fi_consensus_annual", A)] == {want_annual}
    assert got[("fi_universe", A)] == {want_uni}


def test_large_difference_is_counted_in_sql_not_classified(tmp_path, monkeypatch) -> None:
    """차이 행이 상한을 넘으면 행 분류 없이 SQL 로 열별로 세고 전부 미설명(표본만)."""
    monkeypatch.setattr(bc, "DIFF_ROW_MAX", 3)

    def mutate(e: dict[str, Any], r: dict[str, Any], d: dict[str, Any]) -> None:
        for x in e["fi_prices"]:
            x["volume"] = 1
    res = _fi3(tmp_path, mutate)
    info = res.tables["fi_prices"]
    assert info["bulk"] is True and info["n_rows_diff"] == 18
    assert res.tally.count[bc.UNEXPLAINED] == 18
    assert res.tally.by_column[bc.UNEXPLAINED]["fi_prices.volume"] == 18


def test_stage_states_follow_the_shared_price_predicate(tmp_path) -> None:
    """장 마감 stage T 행 상태 = 공유 가격 술어(`kw_daily.ka10060_postclose_price_usable_sql`) — 쓸 수
    있음 · price_valid 참 아님(16:00 컷오프) · price_valid 참인데 종가 0 이하·거래량 없음(범주 밖). 키움
    pred_pre 가 없는 종목(T-6 판정 불가 갈래의 증거)도 함께 낸다. 루트·판 id 는 장 마감 fi 판 기록의
    `postclose_stage_root`·`postclose_builds` 그대로다."""
    from types import SimpleNamespace

    rows = [{"ticker": tk, "date": T_DATE, "price_valid": pv, "close_krw": close,
             "pred_pre_krw": None if tk == "100002" else 0, "volume_shr": vol}
            for tk, pv, close, vol in (("100001", True, 10_000, 100),
                                       ("100002", False, 10_000, 100),
                                       ("100003", None, 10_000, 100), ("100004", True, 0, 100),
                                       ("100005", True, 10_000, None))]
    rows.append({"ticker": "100009", "date": DP_DATE, "price_valid": True, "close_krw": 1,
                 "pred_pre_krw": None, "volume_shr": 1})  # T 가 아닌 날 — 읽지 않는다
    _make_stage_tree(tmp_path, bc.T_SOURCE_TABLE, rows, build_id=PC_BID)
    side = SimpleNamespace(fi_run={"postclose_stage_root": str(tmp_path / "stage"),
                                   "postclose_builds": {bc.T_SOURCE_TABLE: PC_BID}})
    states, no_pred = bc.read_stage(side, T_DATE)  # type: ignore[arg-type]
    assert states == {"100001": bc.STAGE_USABLE, "100002": bc.STAGE_INVALID,
                      "100003": bc.STAGE_INVALID, "100004": bc.STAGE_UNUSABLE,
                      "100005": bc.STAGE_UNUSABLE}
    assert no_pred == {"100002"}


def test_vocabulary_is_the_factor_inputs_canon() -> None:
    """제외 사유·T 행 원천 표는 fi 정본(`queries.EXCLUDE_REASONS`·`T_SOURCE_TABLE`, PR-5)이다."""
    assert {bc.NO_PRICE, bc.T6_REASON, *bc.CARRY_REASONS, *bc.ESTIMATE_REASONS} <= set(
        fiq.EXCLUDE_REASONS)
    assert bc.T_SOURCE_TABLE == fiq.T_SOURCE_TABLE


def test_category_table_cites_the_canon() -> None:
    """범주 정의는 한 곳(`CATEGORIES`)이고 각 범주가 정본 근거를 단다."""
    assert set(bc.CATEGORIES) == {bc.CLOSE_DEF, bc.VOLUME_DEF, bc.FLOW_DEF, bc.CARRY, bc.INFO,
                                  bc.T6, bc.CUTOFF, bc.NOT_TARGETED, bc.CREDIT_T, bc.FY, bc.FILING}
    assert all(c.basis and c.label and c.definition for c in bc.CATEGORIES.values())
    assert all("T-36" in bc.CATEGORIES[k].basis for k in (bc.CLOSE_DEF, bc.VOLUME_DEF, bc.CARRY,
                                                           bc.INFO, bc.T6, bc.CUTOFF, bc.FILING))


# ── 왕복 — 실제 빌더(PR-5 픽스처 방식): 15:41 원장 → stage → fi 장 마감 판 → 모델 ────────────
# 장 마감 판은 수집기 실물 함수로 `postclose.db` 를 쓰고(`test_factor_inputs.make_postclose_stage` —
# `daily.postclose.insert_first` → stage 빌더 `stg_flow_postclose_kiwoom`), `factor_inputs.build
# --basis evening`(D' 인계 이력 고정) → `model.build` 로 짓는다. 연구 판 D'·T 는 같은 합성 원천의
# D' 상태·T 상태(KRX T 행 = 원장과 같은 값)에서 `factor_inputs.build` 아침판 → `model.build`.
# 원장 계획: 보통 종목(종가 = KRX). T-6 네 갈래 — C 분할(키움 기준가 ≠ D' 종가 · 연구 판 T 계수 2배 ·
# KRX 기준가도 절반) · H +100%(제한폭 밖) · P pred_pre 없음(판정 불가) · X2 기준가 10원 이동(스팩·전일
# 무거래 모양 — 계수·수익률 흔적 없이 KRX 기준가만 다르다). DD(price_valid 거짓)·F(NULL)·L(원장 행
# 없음) — 16:00 컷오프.
RT_EQ = "m_20260930T000500_000000Z"
V2 = "v2_percentrank@1.0"


def _rt_plan(close_bump: str | None = None) -> dict[str, tuple[int, int | None, bool | None]]:
    import test_factor_inputs as tf
    plan = {t: v for t, v in tf.PC_PLAN.items() if t not in (tf.K, tf.ETF)}
    plan[tf.X2] = (tf.D_CLOSE[tf.X2] + 500, 490, True)   # 키움 기준가 = D' 종가 + 10
    if close_bump is not None:                          # 결함: 원장 종가 +1(기준가는 그대로)
        close, pred, valid = plan[close_bump]
        plan[close_bump] = (close + 1, None if pred is None else pred + 1, valid)
    return plan


def _t_state(plan: dict[str, tuple[int, int | None, bool | None]]) -> dict[str, list[dict]]:
    """D' 상태 원천에 더할 T 행(KRX) — 종가·거래량·수급 = 원장 계획, C 는 T 에 누적계수 2배."""
    import test_factor_inputs as tf
    t = tf.T
    out: dict[str, list[dict]] = {
        "trading_calendar": [{"date": t, "prev_td": tf.D, "next_td": None}],
        "universe_daily": [dict(r, date=t) for r in tf._universe()],
        "price_daily": [], "price_adj_daily": [], "flow_daily": []}
    closes = {tk: c for tk, (c, _, _) in _rt_plan().items()}   # KRX 종가 = 결함 없는 원장 종가
    closes[tf.L] = tf.D_CLOSE[tf.L] + 500                       # 원장에는 없다(수집 못 함)
    for tk, close in closes.items():
        out["price_daily"].append(tf._price_row(tk, t, close))
        share = tf.ADJ_FACTOR.get(tk, 1.0) * (2.0 if tk == tf.C else 1.0)
        po = tf._price_only(tk, t)
        out["price_adj_daily"].append({"ticker": tk, "date": t, "adj_close": close * share / po,
                                       "cum_share_factor": share, "cum_price_only_factor": po,
                                       "n_unadjusted_events": 0, "basis": "krx"})
        if tk in plan and tk not in tf.PC_NO_FLOWS:
            row: dict = {"date": t, "ticker": tk, "src": "kiwoom"}
            for i, col in enumerate(tf._FLOW_COLS):
                row[col] = (-1 if i == 0 else 1) * (tf.IDX[tk] * 10 + i) * 1_000_000
            out["flow_daily"].append(row)
    return out


def _with_krx_base(eq_root: Path) -> None:
    """T 상태 원천의 equity `price_daily` 에 KRX 기준가 열(`base_price_krw`)을 붙인다 — fi 는 읽지 않아
    합성 원천에 없다. T 행 = 키움 기준가(|cur_prc| − pred_pre, I-1), pred_pre 가 없으면 D' 종가."""
    import test_factor_inputs as tf
    bases = {tk: (c - pred if pred is not None else tf.D_CLOSE[tk])
             for tk, (c, pred, _) in _rt_plan().items()}
    bases[tf.L] = tf.D_CLOSE[tf.L]
    part = eq_root / "price_daily" / f"v={RT_EQ}" / "part0.parquet"
    vals = ", ".join(f"('{tk}', {b})" for tk, b in bases.items())
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT p.*, CASE WHEN p.date = DATE '{tf.T.isoformat()}' THEN m.b END "
                    f"AS base_price_krw FROM read_parquet('{part}') p LEFT JOIN (VALUES {vals}) "
                    f"m(t, b) ON m.t = p.ticker) TO '{part}.x' (FORMAT PARQUET)")
    finally:
        con.close()
    Path(f"{part}.x").replace(part)


def _real_boards(base: Path, close_bump: str | None = None) -> tuple[Path, Path, Path]:
    """(장 마감 루트, 연구 루트, 달력) — 전부 실제 빌더."""
    import test_factor_inputs as tf
    from factor_inputs import build as fi_build

    plan = _rt_plan(close_bump)
    eq1, st1 = tf.make_roots(base / "src_dp")
    eq2, st2 = tf.make_roots(base / "src_t", eq_build=RT_EQ, extra=_t_state(plan))
    _with_krx_base(eq2)
    cal = tf._cal_dir(base)
    data = base / "data"
    pc = tf.make_postclose_stage(base / "pc", plan)
    mdb = pc.parent                                     # <pc>/model_db — stage 옆에 fi·model
    hist = tf._history(base / f"{tf.D_S}_morning.json")
    with allow_skips(("factor_inputs", "FG4", "no_fixtures",
                      "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")):
        rd = fi_build(tf.D_S, "morning", data / "factor_inputs", st1, eq1, grace_days=5,
                      min_eligible=5, golden_path=None)
        rt = fi_build(tf.T_S, "morning", data / "factor_inputs", st2, eq2, grace_days=5,
                      min_eligible=5, golden_path=None)
        ev = fi_build(tf.T_S, "evening", mdb / "factor_inputs", st1, eq1, grace_days=5,
                      min_eligible=5, golden_path=None, builds_from=hist, calendar_dir=cal,
                      postclose_stage_root=pc, candidates_root=data / "factor_inputs")
    assert rd.ok and rt.ok and ev.ok, [(b.basis, b.date, b.summary()) for b in (rd, rt, ev)]
    for root, basis in ((data, "morning"), (mdb, "evening")):
        res = model_build(tf.T_S, basis, root / "model", root / "factor_inputs", specs=[V2],
                          primary=V2, min_prices_on_d=1)
        assert res.ok, res.summary()
    return mdb, data, cal


@pytest.fixture(scope="module")
def real_pair(tmp_path_factory) -> tuple[Path, Path, Path]:
    return _real_boards(tmp_path_factory.mktemp("real"))


def _real_cli(pair: tuple[Path, Path, Path], out: Path, *extra: str) -> int:
    import test_factor_inputs as tf
    ev, rs, cal = pair
    return bc.main(["--date", tf.T_S, "--evening-root", str(ev), "--research-root", str(rs),
                    "--calendar-dir", str(cal), "--out-root", str(out), *extra])


def test_real_builders_same_inputs_have_nothing_unexplained(real_pair, tmp_path) -> None:
    """같은 입력이면 미설명 0 — 남는 차이는 증거가 있는 T-6 네 갈래(C 분할·H +100%·P pred_pre 없음·
    X2 기준가 10원 — 마지막은 KRX 기준가만이 증거)·16:00 컷오프(DD price_valid 거짓 · F NULL · L 원장
    행 없음)와 H 의 filing_late(합성 원천의 재제출본이 T 에 공개 — 장 마감 판 = 연구 D' = false, 연구 T =
    true)뿐이다. 판 기록 키(`postclose_stage_root`·`postclose_builds`)를 그대로 읽는다."""
    import test_factor_inputs as tf
    rc = _real_cli(real_pair, tmp_path, "--spearman-min", "0")
    rep = json.loads((tmp_path / "compare" / f"{tf.T_S}.json").read_text(encoding="utf-8"))
    assert rep["unexplained"] == [] and rc == 0, rep["unexplained"]
    tick = rep["ticker_categories"]
    assert tick[tf.C] == tick[tf.P] == tick[tf.X2] == [bc.T6] and tick[tf.H] == [bc.T6, bc.FILING]
    notes = {f["ticker"]: f["note"] for f in rep["categories"][bc.T6]["samples"]
             if f["table"] == "fi_universe"}
    assert (notes[tf.X2], notes[tf.P]) == ("KRX 기준가 ≠ D' 종가", "키움 pred_pre 없음")
    assert tick[tf.DD] == tick[tf.F] == tick[tf.L] == [bc.CUTOFF]
    assert {k for k, v in rep["categories"].items() if v["count"]} == {bc.T6, bc.CUTOFF, bc.FILING}
    only = {e["ticker"]: e["categories"] for e in rep["model"][V2]["research_only"]}
    assert only == {tf.C: [bc.T6], tf.H: [bc.T6, bc.FILING], tf.P: [bc.T6], tf.X2: [bc.T6]}
    ev_run = json.loads((real_pair[0] / "factor_inputs" / "_runs" / f"{tf.T_S}_evening.json")
                        .read_text(encoding="utf-8"))
    assert ev_run["postclose_stage_root"] and set(ev_run["postclose_builds"]) == {bc.T_SOURCE_TABLE}


def test_real_builders_one_defect_gives_rc_1(tmp_path) -> None:
    """원장 종가가 KRX 공식 종가와 1원 다르면(기준가는 그대로 — T-6 아님) 실운영 미설명 → rc 1.
    같은 차이가 재생(`--replay`)이면 종가 정의다."""
    import test_factor_inputs as tf
    pair = _real_boards(tmp_path / "b", close_bump=tf.A)
    assert _real_cli(pair, tmp_path / "live", "--spearman-min", "0") == 1
    rep = json.loads((tmp_path / "live" / "compare" / f"{tf.T_S}.json").read_text(encoding="utf-8"))
    assert {(u["table"], u["ticker"]) for u in rep["unexplained"]} >= {("fi_prices", tf.A)}
    assert all(u["ticker"] == tf.A for u in rep["unexplained"]), rep["unexplained"]
    assert _real_cli(pair, tmp_path / "replay", "--replay", "--spearman-min", "0") == 0
