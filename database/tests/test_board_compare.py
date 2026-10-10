"""두 판 대조 — 장 마감 판 T 대 다음 날 연구 판 T(컷오버 PR-7, `python -m daily.board_compare`).

판은 실제 판 규약으로 만든다: fi 8표는 `test_model_build.write_fi_tree`(계약 dtype parquet ·
`_meta.json` · latest) + `_runs/<T>_<basis>.json`, 모델 판은 그 fi 판 위에서 **실제** `model.build` 를
돌린다(장 마감 판은 basis evening — 판 id `e_`). fi 값은 `test_model_v4_rank.Board` 40종목 합성 보드다.
장 마감 판 = 연구 판 사본 + 구조 표식(시총 기준 `t1_shares_x_t_close` · T 행 출처 `postclose`) + 범주마다
심은 차이 하나씩. 연초(연도 창)·filing_late·기업행위 계수 경계는 모델 없이 작은 fi 판 둘로 `compare_fi` 를
직접 본다.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from daily import board_compare as bc
from factor_inputs import queries as fiq
from factor_inputs.build import RULES_VERSION as FI_RULES
from model.build import build as model_build
from model.contracts import FI_TABLES, FactorInputs
from test_model_build import SMALL, board_fi, write_fi_tree
from test_model_v4_rank import _r

T = "2026-09-28"
T_S = "20260928"
T_DATE = dt.date(2026, 9, 28)
DP = "2026-09-27"                       # 합성 보드는 달력일 하루 = 세션 하나(직전 세션 D')
DP_DATE = dt.date(2026, 9, 27)
EV_FI = "e_20260928T065500_000000Z"
RS_FI = "m_20260929T000500_000000Z"
N_STOCKS = 40

# 심은 차이 — 범주마다 종목 하나
P_CLOSE, P_FLOW, P_HALT, P_INFO, P_SECTOR, P_FIN, P_T6, P_CUT, P_CREDIT = (
    f"{100001 + i:06d}" for i in range(9))
P_NEW = f"{100000 + N_STOCKS:06d}"     # 연구 판에만 있는 T 신규 상장
P_PRE_T = "100020"                      # 범주 밖 — T 전 행 종가


def _tables(fi: FactorInputs) -> dict[str, list[dict[str, object]]]:
    return {k: [dict(r) for r in v] for k, v in fi.tables.items()}


def _rows(tables: dict[str, list[dict[str, object]]], table: str, ticker: str,
          day: dt.date | None = None) -> list[dict[str, object]]:
    return [r for r in tables[table]
            if r["ticker"] == ticker and (day is None or r.get("date") == day)]


def _evening_copy(research: dict[str, list[dict[str, object]]],
                  drop: tuple[str, ...] = ()) -> dict[str, list[dict[str, object]]]:
    """연구 판 → 장 마감 판 사본: 구조 표식만 바꾼다(시총 기준·T 행 출처). `drop` 종목은 뺀다."""
    ev = {k: [copy.deepcopy(r) for r in v if r["ticker"] not in drop]
          for k, v in research.items()}
    for r in ev["fi_universe"]:
        r["mktcap_basis"] = fiq.T_MKTCAP_BASIS
    for r in ev["fi_prices"]:
        if r["date"] == T_DATE:
            r["price_source"] = fiq.T_PRICE_SOURCE
    return ev


def write_board(root: Path, tables: dict[str, list[dict[str, object]]], *, basis: str,
                fi_bid: str, asof: str, rules: str = FI_RULES) -> Path:
    """`root/factor_inputs`(fi 판 + `_runs`) · `root/model`(실제 model.build) — 판 규약 그대로."""
    fi_root = root / "factor_inputs"
    write_fi_tree(fi_root, FactorInputs(T, basis, fi_bid, tables), fi_bid, basis=basis, date=T)
    run = {"layer": "factor_inputs", "status": "ok", "build_id": fi_bid, "date": T,
           "basis": basis, "asof": asof, "rules_version": rules}
    (fi_root / "_runs").mkdir(parents=True, exist_ok=True)
    (fi_root / "_runs" / f"{T_S}_{basis}.json").write_text(json.dumps(run), encoding="utf-8")
    res = model_build(T_S, basis, root / "model", fi_root, **SMALL)
    assert res.ok, res.summary()
    return root


def _pair(base: Path, research: dict[str, list[dict[str, object]]],
          evening: dict[str, list[dict[str, object]]]) -> tuple[Path, Path]:
    ev = write_board(base / "model_db", evening, basis="evening", fi_bid=EV_FI, asof=DP)
    rs = write_board(base / "data", research, basis="morning", fi_bid=RS_FI, asof=T)
    return ev, rs


def _plant(research: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    """연구 판(41종목)에 맞춰 장 마감 판을 만들고 범주마다 차이 하나를 심는다."""
    ev = _evening_copy(research, drop=(P_NEW,))           # 이월: T 신규 상장은 장 마감 판에 없다
    # 종가 정의 — T 행 종가(수정종가도 같은 폭)
    for r in _rows(ev, "fi_prices", P_CLOSE, T_DATE):
        r["close"] = int(str(r["close"])) + 5
    for r in _rows(ev, "fi_adj_prices", P_CLOSE, T_DATE):
        r["adj_close"] = float(str(r["adj_close"])) + 5.0
    # 수급 정의 — T 행 외국인
    for r in _rows(ev, "fi_flows", P_FLOW, T_DATE):
        r["foreign_investor"] = float(str(r["foreign_investor"])) + 3.0
    # 이월 — T 에 정지된 종목(연구 판만 정지)
    for r in _rows(research, "fi_universe", P_HALT):
        r["is_halted"] = True
    # 정보 시점 — WISE 컨센서스 T 판 · 추정기관 수 · WICS · 재무(T 공시)
    for r in _rows(ev, "fi_consensus", P_INFO):
        r["fetched_date"] = DP_DATE
    for r in _rows(research, "fi_consensus", P_INFO):
        r["fetched_date"] = T_DATE
        if r["horizon"] == "cur":
            r["op"] = float(str(r["op"])) * 2
    for r in _rows(research, "fi_universe", P_INFO):
        r["n_analysts"] = 9
    for r in _rows(research, "fi_universe", P_SECTOR):
        r["sector_l2"] = "G1099"
    for r in _rows(ev, "fi_fin_summary", P_FIN):
        r["available_date"] = DP_DATE
    for r in _rows(research, "fi_fin_summary", P_FIN):
        r["available_date"] = T_DATE
        if r["period_type"] == "annual":
            r["ni"] = float(str(r["ni"])) + 4.0
    # T-6 — 장 마감 판이 당일 기업행위 대기로 뺀 종목. 연구 판은 T 행에 새 계수(액면분할 2:1)
    for r in _rows(ev, "fi_universe", P_T6):
        r.update(eligible=False, exclude_reason=bc.T6_REASON)
    for r in _rows(research, "fi_adj_prices", P_T6, T_DATE):
        r.update(adj_factor=2.0, adj_close=float(str(r["adj_close"])) * 2)
    # 16:00 컷오프 — 장 마감 판에 T 가격·수정주가·수급이 없다(no_price · 시총 NULL)
    for table in ("fi_prices", "fi_adj_prices", "fi_flows"):
        ev[table] = [r for r in ev[table] if not (r["ticker"] == P_CUT and r["date"] == T_DATE)]
    for r in _rows(ev, "fi_universe", P_CUT):
        r.update(eligible=False, exclude_reason="no_price", market_cap=None)
    # 신용 available_date ≤ T — T 에 실입수한 행이 장 마감 판에 없다
    ev["fi_credit"] = [r for r in ev["fi_credit"]
                       if not (r["ticker"] == P_CREDIT and r["available_date"] == T_DATE)]
    return ev


@pytest.fixture(scope="module")
def research_tables() -> dict[str, list[dict[str, object]]]:
    return _tables(board_fi(N_STOCKS + 1))


@pytest.fixture(scope="module")
def same_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    """차이 없는 두 판(구조 표식만 다르다)."""
    rs = copy.deepcopy(research_tables)
    return _pair(tmp_path_factory.mktemp("same"), rs, _evening_copy(rs))


@pytest.fixture(scope="module")
def planted_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    rs = copy.deepcopy(research_tables)
    ev = _plant(rs)
    return _pair(tmp_path_factory.mktemp("planted"), rs, ev)


@pytest.fixture(scope="module")
def unexplained_pair(tmp_path_factory, research_tables) -> tuple[Path, Path]:
    """범주 밖 차이 1건 — T 전 행(09-20) 종가가 다르다."""
    rs = copy.deepcopy(research_tables)
    ev = _evening_copy(rs)
    for r in _rows(ev, "fi_prices", P_PRE_T, dt.date(2026, 9, 20)):
        r["close"] = int(str(r["close"])) + 1
    return _pair(tmp_path_factory.mktemp("unexplained"), rs, ev)


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
def test_same_boards_pass_with_no_difference(same_pair, tmp_path, capsys) -> None:
    """구조 표식(시총 기준·T 행 출처)만 다른 두 판 — 차이 0, Spearman 1, 후보 겹침 = 후보 수, rc 0.
    JSON 은 출력 루트의 compare/<T>.json(기본 = 장 마감 루트)."""
    assert run_cli(same_pair) == 0
    out = capsys.readouterr().out
    assert "판정 pass" in out and f"compare/{T_S}.json" in out
    rep = report(same_pair[0])
    assert (rep["verdict"], rep["rc"], rep["date"], rep["dprime"]) == ("pass", 0, T, DP)
    assert rep["n_unexplained"] == 0 and set(cats(rep).values()) == {0}
    # 구조 표식이 든 두 표는 해시가 달라도 대조 열 차이는 0, 나머지 6표는 해시로 끝난다
    assert all(t["n_rows_diff"] == 0 for t in rep["fi"].values()), rep["fi"]
    assert {n for n, t in rep["fi"].items() if not t["hash_equal"]} == {"fi_universe", "fi_prices"}
    assert rep["boards"]["evening"]["fi_build_id"] == EV_FI
    assert rep["boards"]["research"]["fi_build_id"] == RS_FI
    assert rep["boards"]["evening"]["model_build_id"].startswith("e_")
    for sid, m in rep["model"].items():
        assert m["spearman"] == pytest.approx(1.0), sid
        assert m["candidates"]["overlap"] == len(m["candidates"]["evening"]), sid
        assert m["evening_only"] == m["research_only"] == [], sid
    assert set(rep["model"]) == {"scope@1.0", "v2_percentrank@1.0", "v3_zscore@1.0",
                                 "v4_rank@0.1", "v4_rank@0.2"}


def test_each_registered_category_is_classified(planted_pair, tmp_path) -> None:
    """범주마다 심은 차이가 그 범주로 간다. 미설명 0 → rc 0(Spearman 하한은 따로 본다 — 40종목
    보드에 충격을 한꺼번에 심어 scope 순위가 크게 흔들린다, `test_spearman_floor_is_a_gate`)."""
    rc = run_cli(planted_pair, tmp_path, "--spearman-min", "0")
    rep = report(tmp_path)
    assert rep["unexplained"] == [] and rep["n_unexplained"] == 0, rep["unexplained"]
    assert rc == 0, rep["reasons"]
    got = cats(rep)
    for key in (bc.CLOSE_DEF, bc.FLOW_DEF, bc.CARRY, bc.INFO, bc.T6, bc.CUTOFF, bc.CREDIT_T):
        assert got[key] >= 1, (key, got)
    assert got[bc.FY] == got[bc.FILING] == 0
    tick = rep["ticker_categories"]
    assert tick[P_CLOSE] == [bc.CLOSE_DEF]
    assert tick[P_FLOW] == [bc.FLOW_DEF]
    assert tick[P_HALT] == [bc.CARRY] and tick[P_NEW] == [bc.CARRY]
    assert tick[P_INFO] == [bc.INFO] and tick[P_SECTOR] == [bc.INFO] and tick[P_FIN] == [bc.INFO]
    assert tick[P_T6] == [bc.T6]
    assert tick[P_CUT] == [bc.CUTOFF]
    assert tick[P_CREDIT] == [bc.CREDIT_T]
    # 종가 정의는 열별 건수로도 남는다(종가 몇 건인지 따로 보인다). 이월은 기록(행) 수와 종목 수가
    # 다르다 — 신규 상장 한 종목이 표마다 수백 행
    assert rep["categories"][bc.CLOSE_DEF]["by_column"]["fi_prices.close"] == 1
    assert rep["categories"][bc.CARRY]["n_tickers"] == 2 < rep["categories"][bc.CARRY]["count"]
    # 표별 — 정보 표 중 차이 없는 것은 해시로 끝난다
    assert rep["fi"]["fi_consensus_annual"]["hash_equal"] is True
    assert rep["fi"]["fi_consensus"]["hash_equal"] is False


def _entries(spec: dict[str, Any]) -> list[dict[str, Any]]:
    cand = spec["candidates"]
    return [*spec["evening_only"], *spec["research_only"], *cand["entered"], *cand["dropped"],
            *spec["composite"]["top"], *(e for f in spec["factors"].values() for e in f["top"])]


def test_model_differences_trace_back_to_fi_categories(planted_pair, tmp_path) -> None:
    """한 판에만 점수가 있는 종목·후보 변동·점수 열 |Δ| 상위 종목에 그 종목의 fi 범주를 붙인다. 붙이는
    범주는 그 spec 엔진이 읽는 fi 표의 것만이다(v3 엔진은 fi_credit 을 읽지 않는다)."""
    run_cli(planted_pair, tmp_path)
    rep = report(tmp_path)
    scope = rep["model"]["scope@1.0"]
    only = {e["ticker"]: e["categories"] for e in scope["research_only"]}
    assert only == {P_T6: [bc.T6], P_CUT: [bc.CUTOFF], P_NEW: [bc.CARRY]}
    assert scope["evening_only"] == []
    assert scope["spearman"] < 1.0 and scope["n_common"] == N_STOCKS - 2   # 41 − T-6·컷오프·신규
    comp = scope["composite"]
    assert comp["n_diff"] >= 1 and comp["top"], comp
    for entry in _entries(scope):
        assert set(entry) >= {"ticker", "categories"}
        assert entry["categories"] or entry["trace"] == bc.CROSS_SECTION
        assert bc.CREDIT_T not in entry["categories"], entry
    # 원값 열의 |Δ| 1위는 그 입력을 심은 종목이고 자기 범주가 붙는다
    for col, (tk, cat) in {"r1m": (P_CLOSE, bc.CLOSE_DEF), "op_change_1m": (P_INFO, bc.INFO),
                           "flow_for_5d": (P_FLOW, bc.FLOW_DEF)}.items():
        top = scope["factors"][col]["top"][0]
        assert (top["ticker"], top["categories"]) == (tk, [cat]), (col, top)


def test_one_unexplained_difference_gives_rc_1(unexplained_pair, tmp_path, capsys) -> None:
    """범주 밖 차이 1건(T 전 행 종가) → rc 1, 미설명 목록에 표·키·열이 남는다."""
    assert run_cli(unexplained_pair, tmp_path) == 1
    out = capsys.readouterr().out
    assert "판정 fail" in out and "미설명" in out and P_PRE_T in out
    rep = report(tmp_path)
    assert (rep["verdict"], rep["rc"], rep["n_unexplained"]) == ("fail", 1, 1)
    (u,) = rep["unexplained"]
    assert (u["table"], u["ticker"], u["key"]["date"], u["columns"]) == (
        "fi_prices", P_PRE_T, "2026-09-20", ["close"])
    assert any("미설명 1" in r for r in rep["reasons"])


def test_spearman_floor_is_a_gate(planted_pair, tmp_path) -> None:
    """미설명이 0 이어도 Spearman 이 하한(기본 0.975) 밑이면 rc 1 — 하한은 인자로 바꾼다. 심은 판의
    scope·v3 Spearman 은 약 0.906(40종목 보드에 컨센서스 2배 등 큰 충격)."""
    assert run_cli(planted_pair, tmp_path) == 1
    rep = report(tmp_path)
    assert rep["n_unexplained"] == 0 and rep["thresholds"]["spearman_min"] == bc.SPEARMAN_MIN
    low = {sid for sid, m in rep["model"].items() if not m["spearman_ok"]}
    assert "scope@1.0" in low
    assert all(any(sid in r and "Spearman" in r for r in rep["reasons"]) for sid in low)
    assert run_cli(planted_pair, tmp_path, "--spearman-min", "0.9") == 0


def test_default_spearman_floor_is_the_registered_p5_lower_end() -> None:
    assert bc.SPEARMAN_MIN == 0.975


# ── 입력 오류(rc 2) ──────────────────────────────────────────────────────────
def _copy_pair(pair: tuple[Path, Path], dest: Path) -> tuple[Path, Path]:
    ev, rs = pair
    return (Path(shutil.copytree(ev, dest / "model_db")), Path(shutil.copytree(rs, dest / "data")))


def _edit_json(path: Path, **vals: object) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj.update(vals)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.mark.parametrize(("case", "words"), [
    ("research_model_missing", ["model", "판이 없다"]),
    ("evening_fi_missing", ["factor_inputs", "_runs", "없다"]),
    ("evening_fi_failed", ["status", "gate_failed"]),
    ("evening_basis_morning", ["basis", "evening"]),
    ("evening_asof_not_before_t", ["asof"]),
    ("model_points_other_fi", ["fi_build_id"]),
    ("rules_differ", ["rules_version"]),
])
def test_input_errors_give_rc_2(same_pair, tmp_path, capsys, case: str,
                                words: list[str]) -> None:
    ev, rs = _copy_pair(same_pair, tmp_path)
    ev_fi_run = ev / "factor_inputs" / "_runs" / f"{T_S}_evening.json"
    if case == "research_model_missing":
        (rs / "model" / "_runs" / f"{T_S}_morning.json").unlink()
        (rs / "model" / "latest_morning.json").unlink()
    elif case == "evening_fi_missing":
        ev_fi_run.unlink()
    elif case == "evening_fi_failed":
        _edit_json(ev_fi_run, status="gate_failed")
    elif case == "evening_basis_morning":
        _edit_json(ev_fi_run, basis="morning")
    elif case == "evening_asof_not_before_t":
        _edit_json(ev_fi_run, asof=T)
    elif case == "model_points_other_fi":
        _edit_json(rs / "model" / "_runs" / f"{T_S}_morning.json",
                   fi_build_id="m_20260101T000000_000000Z")
    elif case == "rules_differ":
        _edit_json(ev_fi_run, rules_version="fi0.0.0")
    rc = bc.main(["--date", T_S, "--evening-root", str(ev), "--research-root", str(rs)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert all(w in err for w in words), err
    rep = report(ev)                         # rc 2 도 판정 파일을 남긴다(X-2 가 '대조 실패'로 센다)
    assert (rep["verdict"], rep["rc"]) == ("error", 2) and rep["error"]


def test_reads_the_run_records_the_real_builders_write(tmp_path) -> None:
    """판 기록 키(status·basis·date·build_id·asof·rules_version)는 실제 `factor_inputs.build`·
    `model.build` 가 쓰는 그대로 읽는다 — 합성 원천(`test_factor_inputs.make_roots`) 위 아침판.
    장 마감 판은 T 행 얹기(PR-5) 전이라 eligible 0 이어서 모델 판을 지을 수 없다."""
    from factor_inputs import build as fi_build
    from test_factor_inputs import D_S, D, make_roots

    eq, st = make_roots(tmp_path / "src")
    fi = fi_build(D_S, "morning", tmp_path / "data" / "factor_inputs", st, eq, min_eligible=5,
                  golden_path=None)
    res = model_build(D_S, "morning", tmp_path / "data" / "model", tmp_path / "data" /
                      "factor_inputs", specs=["v2_percentrank@1.0"],
                      primary="v2_percentrank@1.0", min_prices_on_d=1)
    assert fi.ok and res.ok
    side = bc.load_side(tmp_path / "data", "morning", D)
    assert (side.fi_build_id, side.model_run.build_id) == (fi.build_id, res.build_id)
    assert (side.fi_run["asof"], side.fi_run["rules_version"]) == (D.isoformat(), FI_RULES)
    with pytest.raises(bc.CompareInputError, match="판 기록이 없다"):
        bc.load_side(tmp_path / "data", "evening", D)


def test_bad_date_is_rc_2_without_report(same_pair, tmp_path, capsys) -> None:
    ev, rs = same_pair
    assert bc.main(["--date", "2026-09-28", "--evening-root", str(ev), "--research-root",
                    str(rs), "--out-root", str(tmp_path)]) == 2
    assert "YYYYMMDD" in capsys.readouterr().err
    assert not (tmp_path / "compare").exists()


# ── fi 분류 경계(작은 판 둘 — 모델 없이 `compare_fi`) ─────────────────────────
A, B = "200010", "200020"


def _mini(t: dt.date, *, uni: dict[str, dict[str, object]] | None = None,
          annual: list[dict[str, object]] | None = None,
          adj: dict[str, list[tuple[dt.date, float, float, bool]]] | None = None,
          prices: dict[str, list[tuple[dt.date, int]]] | None = None,
          ) -> dict[str, list[dict[str, object]]]:
    tables: dict[str, list[dict[str, object]]] = {n: [] for n in FI_TABLES}
    for tk in (A, B):
        row = dict(ticker=tk, date=t, name=tk, market="KOSPI", sec_type="common",
                   market_cap=5000.0, mktcap_basis="krx", has_estimates=True,
                   coverage_state="fresh", coverage_age_days=0, filing_late=False,
                   eligible=True, exclude_reason=None)
        row.update((uni or {}).get(tk, {}))
        tables["fi_universe"].append(_r("fi_universe", **row))
        for day, close in (prices or {}).get(tk, [(t, 1000)]):
            tables["fi_prices"].append(_r("fi_prices", ticker=tk, date=day, close=close,
                                          price_source="krx"))
        for day, a_close, factor, ok in (adj or {}).get(tk, []):
            tables["fi_adj_prices"].append(_r("fi_adj_prices", ticker=tk, date=day,
                                              adj_close=a_close, adj_factor=factor, adj_ok=ok))
    for r in annual or []:
        tables["fi_consensus_annual"].append(_r("fi_consensus_annual", **r))
    return tables


def _fi_pair(tmp_path: Path, t: dt.date, ev: dict[str, list[dict[str, object]]],
             rs: dict[str, list[dict[str, object]]]) -> list[bc.Finding]:
    iso = t.isoformat()
    write_fi_tree(tmp_path / "ev", FactorInputs(iso, "evening", EV_FI, ev), EV_FI,
                  basis="evening", date=iso)
    write_fi_tree(tmp_path / "rs", FactorInputs(iso, "morning", RS_FI, rs), RS_FI,
                  basis="morning", date=iso)
    return bc.compare_fi(tmp_path / "ev", EV_FI, tmp_path / "rs", RS_FI,
                         t, t - dt.timedelta(days=5 if t.month == 1 else 1)).findings


def _annual(t: str, fetched: dt.date, periods: tuple[str, ...]) -> list[dict[str, object]]:
    return [dict(ticker=t, period=p, data_type="E", op=1.0, ni=1.0, fetched_date=fetched)
            for p in periods]


@pytest.mark.parametrize(("t", "want"), [(dt.date(2027, 1, 4), bc.FY),
                                         (dt.date(2026, 9, 28), bc.INFO)])
def test_wise_window_on_the_first_session_of_a_year_is_fy_window(tmp_path, t: dt.date,
                                                                 want: str) -> None:
    """연초 첫 거래일(D' 2026-12-30 · T 2027-01-04) — 연간 컨센서스 창·신선도 차이는 연도 창 범주.
    해가 같은 날의 같은 차이는 정보 시점."""
    y = t.year
    dp = t - dt.timedelta(days=5 if t.month == 1 else 1)
    ev = _mini(t, uni={A: dict(coverage_state="none", has_estimates=False, eligible=False,
                               exclude_reason="estimates_none")},
               annual=_annual(A, dp, (f"{y - 1}/12", f"{y}/12")))
    rs = _mini(t, annual=_annual(A, t, (f"{y - 1}/12", f"{y}/12", f"{y + 1}/12")))
    found = _fi_pair(tmp_path, t, ev, rs)
    assert {(f.table, f.category) for f in found} == {("fi_consensus_annual", want),
                                                      ("fi_universe", want)}, found


def test_filing_late_difference_is_its_own_category(tmp_path) -> None:
    ev = _mini(T_DATE, uni={B: dict(filing_late=False)})
    rs = _mini(T_DATE, uni={B: dict(filing_late=True)})
    (f,) = _fi_pair(tmp_path, T_DATE, ev, rs)
    assert (f.table, f.ticker, f.category, f.columns) == ("fi_universe", B, bc.FILING,
                                                          ("filing_late",))


def test_evening_info_after_d_prime_is_unexplained(tmp_path) -> None:
    """장 마감 판 정보가 D' 뒤(fetched_date = T)면 D' 자르기 위반 — 범주로 덮지 않는다."""
    ev = _mini(T_DATE, annual=_annual(A, T_DATE, ("2026/12",)))
    rs = _mini(T_DATE, annual=_annual(A, DP_DATE, ("2026/12",)))
    found = _fi_pair(tmp_path, T_DATE, ev, rs)
    assert {f.category for f in found} == {bc.UNEXPLAINED}, found


@pytest.mark.parametrize(("pre_t_factor", "want"), [(1.0, bc.UNEXPLAINED), (2.0, bc.INFO)])
def test_t_row_factor_change(tmp_path, pre_t_factor: float, want: str) -> None:
    """T 행 계수만 다르면(T-6 표식 없음) 장 마감 판이 당일 기업행위를 못 잡은 것 — 미설명. 같은 종목
    T 전 행부터 계수가 다르면 T 에 도착한 기업행위 정보(정보 시점)."""
    pre = T_DATE - dt.timedelta(days=1)
    ev = _mini(T_DATE, adj={A: [(pre, 1000.0, 1.0, True), (T_DATE, 1000.0, 1.0, True)]},
               prices={A: [(pre, 1000), (T_DATE, 1000)]})
    rs = _mini(T_DATE, adj={A: [(pre, 1000.0 * pre_t_factor, pre_t_factor, True),
                                (T_DATE, 2000.0, 2.0, True)]},
               prices={A: [(pre, 1000), (T_DATE, 1000)]})
    found = [f for f in _fi_pair(tmp_path, T_DATE, ev, rs) if f.key.get("date") == T]
    assert {f.category for f in found} == {want}, found


def test_t_row_unresolved_flag_is_carried(tmp_path) -> None:
    """T 행 adj_ok 는 장 마감 판이 D' 값을 잇는다(PR-5) — T 행에서만 다르면 이월. H1-4 가 더하는
    adj_jump_ok 도 같은 규칙이다(계약에 아직 없어 행 쌍으로 직접 본다)."""
    pre = T_DATE - dt.timedelta(days=1)
    ev = _mini(T_DATE, adj={A: [(pre, 1000.0, 1.0, True), (T_DATE, 1000.0, 1.0, True)]})
    rs = _mini(T_DATE, adj={A: [(pre, 1000.0, 1.0, True), (T_DATE, 1000.0, 1.0, False)]})
    (f,) = _fi_pair(tmp_path, T_DATE, ev, rs)
    assert (f.table, f.category, f.columns) == ("fi_adj_prices", bc.CARRY, ("adj_ok",))
    uni = {A: {"exclude_reason": None}}
    ctx = bc._Ctx(T_DATE, DP_DATE, uni, uni, {A: 1000}, {A: 1000})
    pair = bc._Pair({"ticker": A, "date": T_DATE}, True, True, {"adj_jump_ok": True},
                    {"adj_jump_ok": False}, ("adj_jump_ok",))
    assert bc.classify("fi_adj_prices", pair, ctx) == [(bc.CARRY, ("adj_jump_ok",),
                                                         "T 행 표식은 D' 값")]


def test_reason_vocabulary_matches_factor_inputs() -> None:
    """제외 사유 어휘는 fi 정본(`queries.EXCLUDE_REASONS`)과 같다(T-6 사유는 PR-5 가 더한다)."""
    assert {bc.NO_PRICE, *bc.CARRY_REASONS, *bc.ESTIMATE_REASONS} <= set(fiq.EXCLUDE_REASONS)


def test_category_table_cites_the_canon() -> None:
    """범주 정의는 한 곳(`CATEGORIES`)이고 각 범주가 정본 근거를 단다."""
    assert set(bc.CATEGORIES) == {bc.CLOSE_DEF, bc.FLOW_DEF, bc.CARRY, bc.INFO, bc.T6, bc.CUTOFF,
                                  bc.CREDIT_T, bc.FY, bc.FILING}
    assert all(c.basis and c.label and c.definition for c in bc.CATEGORIES.values())
