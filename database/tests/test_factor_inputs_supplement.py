"""factor_inputs 주말 보강판(`--basis supplement` · `--wise-asof`, N-45 · DECISIONS §7 #8 · 엑셀 확정분 E5).

휴장 앞 마지막 거래일 D 의 판을 휴일 아침에 받은 WISE 컨센서스(WISE 기준일 = D)로 다시 짓는다. 여기서
보는 것:
  ① 휴일 수집분(fetched_date = W > D)이 보강판에 들어간다 — 신선도·매트릭스·연간 컨센서스·추정기관 수·
     WISE 재무. 가격·수급·DART·universe_daily·WICS·감사·공시·기업행위는 D 그대로
  ② 같은 원천으로 지은 D 아침판(정규 판)에는 휴일 수집분이 들어가지 않는다 — 휴일 행이 없는 원천으로 지은
     아침판과 8표 content_hash 가 같다(08:10 아침판이 휴일 아침 WISE 를 담은 stage 위에 서도 무변경)
  ③ 신선도 나이 — W 수집은 D 다음 세션의 저녁 수집과 같은 내용이라 W 까지 신선하지 않은 종목은 그 세션
     하나를 더 센다(나이 0 인 grace 가 생기지 않는다)
  ④ 게이트 — 휴일 수집분이 없으면(D* ≠ W) FG-fresh FAIL · 정규 판은 --wise-asof 를 넓히지 못한다(rc 2)

원천은 `test_factor_inputs` 합성 트리(D = 09-28 월)이고, 휴일 수집일 W 는 합성상 D 다음 날 09-29 다(fi 는
W 가 휴장인지 보지 않는다 — 대상일 판정은 `daily.calendar.supplement_target` 과 체인 몫). 휴일 수집은
실물처럼 D 에 신선했던 종목 전부가 W 판을 받는다 — 단 A 는 값이 바뀌고(901·701), B 는 D 에 사라졌던
추정치가 돌아오며, E 는 W 판에서 추정치가 빠진다. 비 WISE 표의 W 날짜 행은 `_leak_rows` 를 그대로 쓴다.
"""
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_factor_inputs as tf
from conftest import allow_skips
from factor_inputs import FactorInputsError, build
from factor_inputs.__main__ import main as cli_main
from model.contracts import FI_TABLES
from stage import manifest


@pytest.fixture(scope="module", autouse=True)
def _k17a_fixture_skips() -> Iterator[None]:
    with allow_skips(("factor_inputs", "FG4", "no_fixtures",
                      "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")):
        yield


W = dt.date(2026, 9, 29)
W_S = "20260929"
# W 판을 받는 종목 — D 에 신선했던 종목(E 는 W 판에서 추정치가 빠진다) + 추정치가 돌아온 B
W_FRESH = (tf.A, tf.B, tf.G, tf.H, tf.K, *tf.EXTRA)
# 비 WISE 원천 표 — 보강판도 asof = D 로 자른다
NON_WISE = ("universe_daily", "sector_snapshot", "audit_opinion", "disclosure_version", "fin_std",
            "dividend_event", "adj_factor")


def _holiday_rows(w: dt.date, *, wise: bool = True) -> dict[str, list[dict]]:
    """휴일 W 날짜 행. WISE 표는 실물처럼 전 종목 판(06:00 체인의 휴일 수집), 나머지는 `_leak_rows(w)`.
    `wise=False` 면 비 WISE 표만(휴일 수집이 없던 원천 — 그 밖은 같다)."""
    leak = tf._leak_rows(w)
    if not wise:
        return {t: leak[t] for t in NON_WISE}
    annual = [tf._annual_row(t, w, "202512", "A", 800.0, 600.0) for t in W_FRESH]
    annual += [tf._annual_row(t, w, "202612", "E", 901.0 if t == tf.A else 950.0 if t == tf.B
                              else 900.0, 701.0 if t == tf.A else 750.0 if t == tf.B else 700.0)
               for t in W_FRESH]
    annual.append(tf._annual_row(tf.E, w, "202512", "A", 800.0, 600.0))      # E 추정 빠짐
    matrix = [{"ticker": t, "fetched_date": w, "target_period": period, "acc_cd": acc,
               "lookback_idx": str(li + 1), "lookback": lb,
               "target_label": f"{period[:4]}/{period[4:]}",
               "base_date": tf.D,                              # WISE 기준일 DT = D
               "value": tf.mval(t, w, period, acc, lb)}
              for t in W_FRESH for period in ("202612", "202712") for acc in tf.ACC
              for lb, li in tf.LB.items()]
    return {**{t: leak[t] for t in (*NON_WISE, "coverage_daily", "stg_fin_wise",
                                    "stg_fin_wise_q")},
            "stg_consensus_annual": annual, "stg_consensus_matrix": matrix}


def _hist(path: Path) -> Path:
    # 휴일 행이 WISE 분기(stg_fin_wise_q) 판을 만들므로 인계 이력에도 그 판을 싣는다
    return tf._history(path, stage_builds={**{t: tf.ST_BUILD for t in tf.ST_TABLES},
                                           "stg_fin_wise_q": tf.ST_BUILD})


@pytest.fixture(scope="module")
def supp(tmp_path_factory) -> SimpleNamespace:
    """휴일 수집분이 든 원천(hol) 위에 보강판(s)·아침판(m)을, 휴일 WISE 수집만 없는 원천(plain) 위에
    아침판(p)을 짓는다. 셋 다 같은 판 id(EQ_BUILD·ST_BUILD)라 차이는 원천 행뿐이다."""
    base = tmp_path_factory.mktemp("fi_supp")
    eq, st = tf.make_roots(base / "hol", extra=_holiday_rows(W))
    eq_p, st_p = tf.make_roots(base / "plain", extra=_holiday_rows(W, wise=False))
    hist = _hist(base / f"{tf.D_S}_morning.json")
    s = build(tf.D_S, "supplement", base / "fi", st, eq, grace_days=5, min_eligible=5,
              golden_path=None, builds_from=hist, wise_asof=W_S)
    m = build(tf.D_S, "morning", base / "fi", st, eq, grace_days=5, min_eligible=5,
              golden_path=None)
    p = build(tf.D_S, "morning", base / "fi_plain", st_p, eq_p, grace_days=5, min_eligible=5,
              golden_path=None)
    return SimpleNamespace(base=base, eq=eq, st=st, hist=hist, s=s, m=m, p=p,
                           out=base / "fi", out_p=base / "fi_plain")


def _q_build(out: Path, table: str, build_id: str, sql: str) -> list[tuple]:
    """판 id 로 연다 — 보강판·아침판이 한 루트에 있어 current 포인터는 마지막 판을 가리킨다."""
    import duckdb
    path = out / table / f"v={build_id}" / "part0.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{path}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _fail(res) -> list[tuple[str, str]]:
    return [(g.name, g.detail) for g in res.gates if g.status.value == "fail"]


# ── ① 판 · 기록 ──────────────────────────────────────────────────────────────
def test_supplement_commits_its_own_board_and_records_wise_asof(supp) -> None:
    """판 id s_ · `_runs/<D>_supplement.json` · `latest_supplement.json` — 정규 판 기록(`_morning`)과 갈린다.
    판 manifest 의 asof = D, wise_asof = W(정규 판은 asof 와 같다). 원천은 D 아침 확정판 인계 이력 고정."""
    s = supp.s
    assert s.ok, _fail(s)
    assert s.build_id.startswith("s_") and (s.basis, s.date) == ("supplement", "2026-09-28")
    assert s.run_manifest == supp.out / "_runs" / f"{tf.D_S}_supplement.json"
    run = json.loads(s.run_manifest.read_text(encoding="utf-8"))
    assert (run["basis"], run["date"], run["asof"], run["wise_asof"]) == (
        "supplement", "2026-09-28", "2026-09-28", "2026-09-29")
    assert run["builds_from"] == str(supp.hist.resolve()) and run["builds_from_date"] == tf.D_S
    latest = json.loads((supp.out / "latest_supplement.json").read_text(encoding="utf-8"))
    assert latest["build_id"] == s.build_id
    rec = next(b for b in manifest.load(supp.out / "fi_universe" / "MANIFEST.json").builds
               if b.build_id == s.build_id)
    assert rec.basis == "supplement"
    m_run = json.loads(supp.m.run_manifest.read_text(encoding="utf-8"))
    assert supp.m.run_manifest.name == f"{tf.D_S}_morning.json"
    assert (m_run["asof"], m_run["wise_asof"]) == ("2026-09-28", "2026-09-28")
    fresh = next(g for g in s.gates if g.name == "FG-fresh")
    assert (fresh.metrics["last_collection_date"], fresh.metrics["collection_expected_date"],
            fresh.metrics["collection_lag_sessions"],
            fresh.metrics["supplement_wise_not_on_wise_asof"]) == (
        "2026-09-29", "2026-09-29", 0, 0)
    assert {g.name: g.status.value for g in s.gates} == {
        "FG0": "pass", "FG1": "pass", "FG2": "pass", "FG3": "pass", "FG4": "skip",
        "FG-fresh": "pass"}


# ── ① 휴일 WISE 가 들어간다 · 나머지는 D ───────────────────────────────────────
def test_supplement_reads_the_holiday_consensus(supp) -> None:
    """매트릭스·연간 컨센서스는 W 판(A 의 바뀐 값 · B 의 돌아온 추정치), 아침판은 D 이하 판."""
    sb, mb = supp.s.build_id, supp.m.build_id
    sql = ("SELECT ticker, max(fetched_date) FROM t WHERE ticker IN "
           f"('{tf.A}', '{tf.B}', '{tf.E}') GROUP BY 1 ORDER BY 1")
    assert _q_build(supp.out, "fi_consensus", sb, sql) == [
        (tf.A, W), (tf.B, W), (tf.E, tf.D)]
    assert _q_build(supp.out, "fi_consensus", mb, sql) == [
        (tf.A, tf.D), (tf.B, dt.date(2026, 9, 23)), (tf.E, tf.D)]
    cur = (f"SELECT op, obs_date FROM t WHERE ticker = '{tf.A}' AND target_period = '2026/12' "
           "AND horizon = 'cur'")
    assert _q_build(supp.out, "fi_consensus", sb, cur) == [
        (tf.mval(tf.A, W, "202612", "121500", "current"), tf.D)]      # 관측일 = WISE 기준일 D
    ann = (f"SELECT op, ni, fetched_date FROM t WHERE ticker = '{tf.A}' AND period = '2026/12' "
           "AND data_type = 'E'")
    assert _q_build(supp.out, "fi_consensus_annual", sb, ann) == [(901.0, 701.0, W)]
    assert _q_build(supp.out, "fi_consensus_annual", mb, ann) == [(900.0, 700.0, tf.D)]


def test_supplement_coverage_ages_count_the_holiday_as_the_next_session(supp) -> None:
    """W 판을 받은 종목은 fresh 0. W 까지 신선하지 않은 종목은 W 를 D 다음 세션으로 센다 — E(D 신선,
    W 판에서 빠짐)는 grace 1(나이 0 grace 가 아니다), C(09-17 신선)는 5 + 1 = 6 → lapsed. 둘 다 평일
    다음 세션 판에서 같은 종목이 받을 나이다. 아침판은 그대로(C grace 5 · E fresh 0)."""
    sql = "SELECT ticker, coverage_state, coverage_age_days, eligible FROM t ORDER BY ticker"
    s = {r[0]: r[1:] for r in _q_build(supp.out, "fi_universe", supp.s.build_id, sql)}
    m = {r[0]: r[1:] for r in _q_build(supp.out, "fi_universe", supp.m.build_id, sql)}
    assert s[tf.A] == ("fresh", 0, True) and s[tf.B] == ("fresh", 0, True)
    assert s[tf.E] == ("grace", 1, True) and m[tf.E] == ("fresh", 0, True)
    assert s[tf.C] == ("lapsed", 6, False) and m[tf.C] == ("grace", 5, True)
    assert s[tf.DD] == ("lapsed", 7, False)
    assert all(s[t] == ("fresh", 0, True) for t in (tf.H, *tf.EXTRA))
    assert m[tf.B] == ("grace", 1, True)


def test_supplement_analyst_counts_and_wise_financials_follow_wise_asof(supp) -> None:
    """추정기관 수 — A(신선, 마지막 신선일 W) 99 · F(none, wise_asof 이하 최신) 77. 아침판 10 · 2.
    WISE 연간 재무는 A 의 W 판(+50), WISE 분기는 A 가 W 판으로 넘어간다. available_date 상한은 W."""
    sb, mb = supp.s.build_id, supp.m.build_id
    sql = f"SELECT ticker, n_analysts FROM t WHERE ticker IN ('{tf.A}', '{tf.F}') ORDER BY 1"
    assert _q_build(supp.out, "fi_universe", sb, sql) == [(tf.A, 99), (tf.F, 77)]
    assert _q_build(supp.out, "fi_universe", mb, sql) == [(tf.A, 10), (tf.F, 2)]
    eps = (f"SELECT eps FROM t WHERE ticker = '{tf.A}' AND period = '2025/12' "
           "AND period_type = 'annual'")
    s_eps, m_eps = (_q_build(supp.out, "fi_fin_summary", b, eps) for b in (sb, mb))
    assert s_eps != m_eps
    qtr = (f"SELECT DISTINCT substr(fs_basis, 1, 5) FROM t WHERE ticker = '{tf.A}' "
           "AND period_type = 'quarter'")
    assert _q_build(supp.out, "fi_fin_summary", sb, qtr) == [("WISE:",)]
    assert _q_build(supp.out, "fi_fin_summary", mb, qtr) == [("DART:",)]
    assert _q_build(supp.out, "fi_fin_summary", sb, "SELECT max(available_date) FROM t") == [(W,)]


def test_supplement_keeps_prices_flows_dart_and_attributes_at_d(supp) -> None:
    """가격·수정주가·수급·신용 표는 아침판과 바이트까지 같다. 원천의 W 날짜 비 WISE 행(A 상장폐지·관리·
    정지 · WICS G30 · 감사 의견거절 · 지연 원본 · 반기 재제출 · 배당 2,000원 · E 4Q 파생 · A 미해결 사건)은
    읽지 않는다 — 속성·DART 값이 아침판과 같다."""
    for t in ("fi_prices", "fi_adj_prices", "fi_flows", "fi_credit"):
        assert supp.s.tables[t]["content_hash"] == supp.m.tables[t]["content_hash"], t
    cols = ("ticker, date, name, market, sec_type, listed_date, shares, market_cap, mktcap_basis, "
            "sector_l1, sector_l2, adv20, is_admin, is_halted, audit_adverse, filing_late")
    sql = f"SELECT {cols} FROM t ORDER BY ticker"
    assert (_q_build(supp.out, "fi_universe", supp.s.build_id, sql)
            == _q_build(supp.out, "fi_universe", supp.m.build_id, sql))
    # 연간 행의 DART·배당 열(WISE 열은 앞 테스트) · DART 분기 행 — A 는 WISE 분기로 넘어가 DART 분기가 없다
    ann = ("SELECT ticker, period, total_assets, debt_ratio, fcf, capex, dps, period_months FROM t "
           "WHERE period_type = 'annual' ORDER BY 1, 2")
    qtr = ("SELECT * FROM t WHERE period_type = 'quarter' AND fs_basis LIKE 'DART:%' "
           f"AND ticker <> '{tf.A}' ORDER BY ticker, period")
    for sql in (ann, qtr):
        assert (_q_build(supp.out, "fi_fin_summary", supp.s.build_id, sql)
                == _q_build(supp.out, "fi_fin_summary", supp.m.build_id, sql)), sql
    dps = (f"SELECT dps FROM t WHERE ticker = '{tf.A}' AND period = '2025/12' "
           "AND period_type = 'annual'")
    assert _q_build(supp.out, "fi_fin_summary", supp.s.build_id, dps) == [(1_446.0,)]
    e4q = (f"SELECT revenue, op, ni FROM t WHERE ticker = '{tf.E}' AND period = '2025/12' "
           "AND period_type = 'quarter'")
    assert _q_build(supp.out, "fi_fin_summary", supp.s.build_id, e4q) == [(None, None, None)]


# ── ② 정규 판 무변경 ──────────────────────────────────────────────────────────
def test_morning_board_on_a_holiday_stage_equals_the_board_without_it(supp) -> None:
    """08:10 체인은 휴일 아침에 D 아침판을 짓는다 — 그 stage·equity 에는 06:00 체인이 받은 휴일 WISE
    (fetched_date = W > D — 컨센서스·매트릭스·추정기관 수·WISE 연간·분기 재무)가 이미 들어 있다. 아침판은
    WISE 를 fetched_date ≤ asof = D 로 자르므로 휴일 WISE 수집이 없던 원천으로 지은 아침판과 8표
    content_hash 가 같다."""
    assert supp.m.ok and supp.p.ok, (_fail(supp.m), _fail(supp.p))
    for t in FI_TABLES:
        assert supp.m.tables[t]["content_hash"] == supp.p.tables[t]["content_hash"], t
    m_fresh = next(g for g in supp.m.gates if g.name == "FG-fresh")
    assert m_fresh.metrics["last_collection_date"] == "2026-09-28"
    assert "supplement_wise_not_on_wise_asof" not in m_fresh.metrics


# ── ④ 게이트 · 인자 ────────────────────────────────────────────────────────────
def test_supplement_without_the_holiday_collection_fails_fresh_gate(supp, tmp_path: Path) -> None:
    """휴일 수집분이 판에 없으면(원천의 D* = D) 수집 지연은 0 거래일이라 못 잡는다 — FG-fresh 가 D* ≠ W 로
    FAIL 해 정규 판과 같은 판을 '보강판' 으로 올리지 않는다. 포인터·latest 는 그대로."""
    eq, st = tf.make_roots(tmp_path / "plain")
    out = tmp_path / "fi"
    res = build(tf.D_S, "supplement", out, st, eq, grace_days=5, min_eligible=5,
                golden_path=None, builds_from=tf._history(tmp_path / f"{tf.D_S}_morning.json"),
                wise_asof=W_S)
    fresh = next(g for g in res.gates if g.name == "FG-fresh")
    assert not res.ok and fresh.status.value == "fail", fresh.detail
    assert fresh.metrics["supplement_wise_not_on_wise_asof"] == 1
    assert fresh.metrics["collection_lag_sessions"] == 0
    assert "supplement_wise_not_on_wise_asof=1" in fresh.detail
    assert not any((out / t / "MANIFEST.json").exists() for t in FI_TABLES)
    assert not (out / "latest_supplement.json").exists()
    assert res.run_manifest == out / "_runs" / f"{tf.D_S}_supplement.json"


@pytest.mark.parametrize(("args", "want"), [
    (["--basis", "supplement", "--wise-asof", W_S], ["--builds-from", "supplement"]),
    (["--basis", "supplement", "--builds-from", "<hist>"], ["--wise-asof", "D 보다 뒤"]),
    (["--basis", "supplement", "--builds-from", "<hist>", "--wise-asof", tf.D_S],
     ["--wise-asof", "D 보다 뒤"]),
    (["--basis", "supplement", "--builds-from", "<hist>", "--wise-asof", "2026-09-29"],
     ["YYYYMMDD"]),
    (["--basis", "morning", "--wise-asof", W_S], ["보강판", "asof=2026-09-28"]),
])
def test_supplement_argument_errors_stop_with_rc2(supp, tmp_path: Path, capsys,
                                                  args: list[str], want: list[str]) -> None:
    """보강판은 고정 판(--builds-from)·휴일 수집일(--wise-asof > D)이 필수. 정규 판은 --wise-asof 로 추정치를
    D 뒤 수집분까지 넓히지 못한다 — 모두 판을 만들지 않고 rc 2."""
    out = tmp_path / "fi"
    args = [str(supp.hist) if a == "<hist>" else a for a in args]
    rc = cli_main(["build", "--date", tf.D_S, "--root", str(out), "--stage-root", str(supp.st),
                   "--equity-root", str(supp.eq), "--min-eligible", "5", *args])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert all(w in err for w in want), err
    assert not out.exists() or not any((out / t / "MANIFEST.json").exists() for t in FI_TABLES)


def test_morning_wise_asof_equal_to_d_is_the_same_board(supp, tmp_path: Path) -> None:
    """정규 판에 --wise-asof = asof 를 주면 생략과 같은 판이다(SQL 글자 그대로)."""
    res = build(tf.D_S, "morning", tmp_path / "fi", supp.st, supp.eq, grace_days=5,
                min_eligible=5, golden_path=None, wise_asof=tf.D_S)
    assert res.ok
    assert {t: v["content_hash"] for t, v in res.tables.items()} == \
        {t: v["content_hash"] for t, v in supp.m.tables.items()}


def test_supplement_cli_round_trip(supp, tmp_path: Path) -> None:
    out = tmp_path / "fi"
    rc = cli_main(["build", "--date", tf.D_S, "--basis", "supplement", "--root", str(out),
                   "--stage-root", str(supp.st), "--equity-root", str(supp.eq),
                   "--min-eligible", "5", "--builds-from", str(supp.hist), "--wise-asof", W_S])
    assert rc == 0
    run = json.loads((out / "_runs" / f"{tf.D_S}_supplement.json").read_text(encoding="utf-8"))
    assert (run["status"], run["wise_asof"]) == ("ok", "2026-09-29")
    assert {t: v["content_hash"] for t, v in run["tables"].items()} == \
        {t: v["content_hash"] for t, v in supp.s.tables.items()}


def test_supplement_fi_feeds_the_supplement_model(supp, tmp_path: Path) -> None:
    """보강 fi(`latest_supplement.json`)가 모델 basis supplement 로 이어진다. 유예 0 spec(v2)은 grace 종목
    (E — W 판에서 빠진 나이 1)을 다시 뺀다 — 나이 0 grace 였다면 낡은 추정치로 채점됐다."""
    from model.build import build as model_build
    res = model_build(tf.D_S, "supplement", tmp_path / "model", supp.out,
                      specs=["v2_percentrank@1.0"], primary="v2_percentrank@1.0",
                      min_prices_on_d=1)
    assert res.ok, res.specs
    assert res.fi_build_id == supp.s.build_id and res.build_id.startswith("s_")
    assert res.run_manifest == tmp_path / "model" / "_runs" / f"{tf.D_S}_supplement.json"
    n_grace = supp.s.coverage["counts_eligible"]["grace"]
    assert n_grace == 1
    assert res.specs["v2_percentrank@1.0"]["n_scores"] == supp.s.coverage["n_eligible"] - n_grace


def test_supplement_build_id_prefix_is_not_a_stage_or_equity_basis() -> None:
    """`s_` 는 fi·모델 층만 쓴다 — stage·equity 의 --basis 선택지에는 없다(그 층은 보강판을 짓지 않는다)."""
    from equity.model import BUILD_BASES
    from stage.model import BASIS_PREFIX, basis_of_build_id, make_build_id
    bid = make_build_id("supplement")
    assert bid.startswith("s_") and basis_of_build_id(bid) == "supplement"
    assert "supplement" not in BASIS_PREFIX and "supplement" not in BUILD_BASES
    with pytest.raises(FactorInputsError, match="접두어"):
        build(tf.D_S, "supplement", Path("/nonexistent"), Path("/nonexistent"),
              Path("/nonexistent"), builds_from=Path("/nonexistent.json"), wise_asof=W_S,
              build_id="m_20260929T000000_000000Z")


def test_supplement_replay_cuts_analyst_counts_at_wise_asof(supp, tmp_path: Path) -> None:
    """재생(`--replay` — 현판에서 그날 판을 다시 짓는 길, 보강판 재생 대조가 쓴다)도 같은 보강판이다. 세션 축 표는
    asof(D)에서 자르지만 coverage_daily 는 날짜가 WISE 수집일이라 wise_asof(W)에서 자른다 — D 에서 자르면 휴일
    수집분의 추정기관 수(A 99 · F 77)가 빠져 재생 판이 실제 보강판과 갈린다."""
    res = build(tf.D_S, "supplement", tmp_path / "fi", supp.st, supp.eq, grace_days=5,
                min_eligible=5, golden_path=None, builds_from=supp.hist, wise_asof=W_S, replay=True)
    assert res.ok, _fail(res)
    assert {t: v["content_hash"] for t, v in res.tables.items()} == \
        {t: v["content_hash"] for t, v in supp.s.tables.items()}
    mark = json.loads(res.run_manifest.read_text(encoding="utf-8"))["replay"]
    assert (mark["session_cut"], mark["wise_cut"]) == ("2026-09-28", "2026-09-29")
