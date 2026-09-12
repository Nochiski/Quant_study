"""S03·S03B·S03B-2·S03C `universe_policy` — 선언표 스키마 + all · common-stock · investable ·
liquid 13행 (DESIGN §4-1 · GATES §3 ㉒ · §4 FX-1-017).

행수 등식이 없는 선언표라 EG1 은 `skip(declaration_table)` 이고, 대신 EG3_policy 가 어휘·문법·
'all' 존재·임계 정합·predicate 바인딩(입력 `universe_daily` 스키마 위)을 본다. 입력이 equity
산출이므로 절단본 위에 S01·S02·S04·S05·S06·S03 상류를 먼저 빌드한다(S03C 로 `universe_daily` 가
`adj_factor` 를 읽는다). `liquid` 는 investable 4행 + S03C flag 행 `no_trade_reason <> 'illiquid'`
+ `adv20_rank_pct >= 1 − liquid_top_pct`(quantile, baseline `universe_policy.liquid_top_pct` 0.5)
— 절단본 손계산 13,656행(rank ≥ 0.5 인 13,660행 중 이유가 illiquid 인 4행이 빠진다).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from equity import (
    build,
    rules_s01,
    rules_s02,
    rules_s03,
    rules_s04,
    rules_s05,
    rules_s06,
)
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"


def _seed() -> Baseline:
    """S01·S02·S03·S05·S06 seed 병합 — S03C 로 universe_daily 가 adj_factor 를 읽어 계수 체인
    상수까지 필요하다(test_equity_s21_workbench.seed 와 같은 규약)."""
    merged: dict[str, dict[str, object]] = {}
    for path in (rules_s01.BASELINE_SEED,
                 Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
                 rules_s03.BASELINE_SEED, rules_s04.BASELINE_SEED,
                 rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED):
        for k, v in load(path).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()
UPSTREAM = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
            rules_s01.CORP, rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT,
            rules_s06.ADJ_FACTOR, rules_s03.UNIVERSE_DAILY)
POLICY = rules_s03.UNIVERSE_POLICY
VERSION = "s03c-v4"
LIQUID_TOP_PCT = 0.5                 # baseline_seed_s03 universe_policy.liquid_top_pct
N_RANK_TOP_HALF = 13660              # rank ≥ 0.5 (test_equity_s03_universe.N_RANK_TOP_HALF)
N_LIQUID = 13656                     # 그중 no_trade_reason='illiquid' 4행을 뺀 절단본 손계산

_ROWS = [
    ("krx.all", "all", 1, "TRUE", "flag", None, "convention", None, VERSION),
    ("krx.common-stock", "common-stock", 1, "sec_type = 'common'", "flag", None, "convention",
     None, VERSION),
    ("krx.common-stock", "common-stock", 2, "status = 'listed'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 1, "sec_type = 'common'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 2, "status = 'listed'", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 3, "NOT admin_state", "flag", None, "convention",
     None, VERSION),
    ("krx.investable", "investable", 4, "NOT liquidation_window", "flag", None, "convention",
     None, VERSION),
    ("krx.liquid", "liquid", 1, "sec_type = 'common'", "flag", None, "convention", None, VERSION),
    ("krx.liquid", "liquid", 2, "status = 'listed'", "flag", None, "convention", None, VERSION),
    ("krx.liquid", "liquid", 3, "NOT admin_state", "flag", None, "convention", None, VERSION),
    ("krx.liquid", "liquid", 4, "NOT liquidation_window", "flag", None, "convention", None,
     VERSION),
    ("krx.liquid", "liquid", 5, "no_trade_reason <> 'illiquid'", "flag", None, "convention", None,
     VERSION),
    ("krx.liquid", "liquid", 6, "adv20_rank_pct >= 0.5", "quantile", LIQUID_TOP_PCT, "convention",
     None, VERSION),
]
LIQUID_QUANTILE_SEQ = 6              # flag 5행 다음 (len(predicates) + 1)


def _query(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{out_dir / '*.parquet'}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _fails(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is GateStatus.FAIL]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s03p") / "equity"
    for rule in UPSTREAM:
        r = build.build_table(rule, STAGE_SLICE, root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, _fails(r)
    return build.build_table(POLICY, STAGE_SLICE, root, SEED, build_id="b_s03_up")


def _variant(built: build.BuildResult, tmp_path: Path, name: str, values: str,
             universe_id: str = "'krx.' || p.policy") -> build.BuildResult:
    """VALUES 목록만 바꾼 변종을 같은 equity_root 에 빌드한다(상수는 변종 이름으로 재등재)."""
    assert built.out_dir is not None
    p = tmp_path / f"{name}.sql"
    p.write_text(f"""
        SELECT {universe_id} AS universe_id, p.policy, CAST(p.rule_seq AS BIGINT) AS rule_seq,
               p.predicate, p.threshold_kind, p.threshold_value, p.basis, p.measured_at,
               k.version, NULL::VARCHAR AS reject_reason
        FROM (VALUES {values}) AS p(policy, rule_seq, predicate, threshold_kind,
                                    threshold_value, basis, measured_at)
        CROSS JOIN _const k""", encoding="utf-8")
    rule = EquityTable(**{**POLICY.__dict__, "name": name, "sql_path": p})
    bl = Baseline({**SEED.data, name: SEED.table(POLICY.name)})
    return build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], bl,
                             build_id=f"b_{name}")


ALL = "('all', 1, 'TRUE', 'flag', NULL::DOUBLE, 'convention', NULL::DATE)"


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_빌드가_통과하고_EG1은_declaration_table_skip(built: build.BuildResult) -> None:
    assert built.ok, _fails(built)
    assert {g.name: (g.status.value, g.detail) for g in built.gates
            if g.status is not GateStatus.PASS} == {
        "EG1": ("skip", "declaration_table"), "EG2": ("skip", "dimension_table"),
        "EG5a": ("skip", "no_previous_build")}
    assert _gate(built, "EG1").metrics == {"n_out": len(_ROWS), "n_reject": 0}
    assert built.inputs == {"universe_daily": "b_universe_daily"}


def test_표는_all_common_stock_investable_liquid_13행이고_quantile은_liquid_마지막_행뿐(
        built: build.BuildResult) -> None:
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT * FROM t ORDER BY policy, rule_seq") == _ROWS
    assert [str(r[0]) for r in _query(built.out_dir, "DESCRIBE t")] == list(POLICY.columns)
    assert _query(built.out_dir, "SELECT policy, rule_seq, threshold_value FROM t WHERE "
                                 "threshold_kind <> 'flag' OR threshold_value IS NOT NULL") == [
        ("liquid", LIQUID_QUANTILE_SEQ, LIQUID_TOP_PCT)]
    assert _query(built.out_dir, "SELECT count(*) FROM t WHERE measured_at IS NOT NULL") == [(0,)]
    # liquid = investable 4행 그대로 + S03C flag 1행 + 임계 1행 — 앞 4행은 글자까지 같다
    assert _query(built.out_dir, "SELECT a.predicate FROM t a JOIN t b ON a.rule_seq = b.rule_seq "
                                 "AND b.policy = 'investable' WHERE a.policy = 'liquid' "
                                 "AND a.predicate <> b.predicate") == []
    assert _query(built.out_dir, "SELECT predicate FROM t WHERE policy = 'liquid' AND rule_seq = 5"
                  ) == [("no_trade_reason <> 'illiquid'",)]
    assert _query(built.out_dir, "SELECT predicate FROM t WHERE policy = 'liquid' "
                                 f"AND rule_seq = {LIQUID_QUANTILE_SEQ}"
                  ) == [(f"adv20_rank_pct >= {1 - LIQUID_TOP_PCT}",)]
    assert "liquid" in rules_s03.POLICY_VOCAB


def test_universe_id는_소비자_계약_어휘와_같다(built: build.BuildResult) -> None:
    """FIELD_MAP §1 — `krx.common-stock` 이 정책표로 풀린다(하이픈 포함 policy 값 =
    'krx.' || policy). `krx.liquid` 는 S03B-2 부터."""
    assert built.out_dir is not None
    assert _query(built.out_dir, "SELECT DISTINCT universe_id FROM t ORDER BY 1") == [
        ("krx.all",), ("krx.common-stock",), ("krx.investable",), ("krx.liquid",)]


def test_predicate가_universe_daily_스키마에서_평가된다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_policy").metrics
    assert m["n_predicate_unbound"] == 0 and m["unbound_predicates"] == []
    assert m["policy_counts"] == {"all": 1, "common-stock": 2, "investable": 4, "liquid": 6}
    assert m["quantile_rows"] == {f"liquid#{LIQUID_QUANTILE_SEQ}": LIQUID_TOP_PCT}
    assert m["n_quantile_threshold_out_of_range"] == 0


def test_정책을_universe_daily에_적용하면_포함_관계가_선다(built: build.BuildResult) -> None:
    """all ⊇ common-stock ⊇ investable ⊇ liquid. 절단본: common 주식 행 중 suspended 333·ETF 4,094·
    우선주·관리·정리매매 행이 차례로 빠지고, liquid 는 그중 무거래 이유가 illiquid 인 행을 뺀 뒤
    같은 날 보통주 모집단 안 adv20 백분위 상위 50%(13,656행 = 13,660 − 4, 손계산)만 남는다
    (적용은 팩터층 몫이지만 술어가 뜻대로 도는지는 여기서 본다)."""
    assert built.out_dir is not None
    root = built.out_dir.parents[1]
    con = duckdb.connect()
    try:
        con.execute("CREATE VIEW u AS SELECT * FROM read_parquet("
                    f"'{root / 'universe_daily' / 'v=b_universe_daily' / 'year=*' / '*.parquet'}', "
                    "hive_partitioning=false)")
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{built.out_dir / '*.parquet'}')")
        preds = {p: " AND ".join(f"({x})" for x in [r[0] for r in con.execute(
            f"SELECT predicate FROM t WHERE policy = '{p}' ORDER BY rule_seq").fetchall()])
            for p in ("all", "common-stock", "investable", "liquid")}
        n = {p: con.execute(f"SELECT count(*) FROM u WHERE {w}").fetchone()[0]  # type: ignore[index]
             for p, w in preds.items()}
        n_common_listed = con.execute("SELECT count(*) FROM u WHERE sec_type = 'common' "
                                      "AND status = 'listed'").fetchone()[0]  # type: ignore[index]
        n_inv = con.execute("SELECT count(*) FROM u WHERE sec_type = 'common' "
                            "AND status = 'listed' AND NOT admin_state "
                            "AND NOT liquidation_window").fetchone()[0]  # type: ignore[index]
        # liquid 는 investable 안에서 rank ≥ 1 − top_pct — 2018-05-03 모집단 4 중 상위 3.
        # 005930 은 분할 창 무거래(corp_action_window·suspended)라 모집단부터 빠졌다
        liq_20180503 = con.execute(f"SELECT ticker FROM u WHERE {preds['liquid']} "
                                   "AND date = DATE '2018-05-03' ORDER BY 1").fetchall()
        n_top_half = con.execute("SELECT count(*) FROM u WHERE sec_type = 'common' "
                                 "AND status = 'listed' AND NOT admin_state "
                                 "AND NOT liquidation_window "
                                 f"AND adv20_rank_pct >= {1 - LIQUID_TOP_PCT}"
                                 ).fetchone()[0]  # type: ignore[index]
    finally:
        con.close()
    assert n["all"] == 41066
    assert n["all"] > n["common-stock"] == n_common_listed > n["investable"] == n_inv
    assert n_inv > n["liquid"] == N_LIQUID
    # S03C 술어가 빼는 몫 = 순위 상위 절반인데 이유가 illiquid 인 4행
    assert n_top_half == N_RANK_TOP_HALF and N_RANK_TOP_HALF - N_LIQUID == 4
    assert liq_20180503 == [("000030",), ("000660",), ("161890",)]


def _with_top_pct(built: build.BuildResult, name: str, top_pct: object,
                  fixtures_path: Path | None = None) -> build.BuildResult:
    """같은 SQL, baseline `liquid_top_pct` 만 바꾼 변종(변종 이름으로 재등재 — 정본은 그대로).
    변종 이름엔 픽스처 파일이 없으므로 통과시키려면 `fixtures_path` 를 준다."""
    assert built.out_dir is not None
    rule = EquityTable(**{**POLICY.__dict__, "name": name})
    bl = Baseline({**SEED.data, name: {**SEED.table(POLICY.name), "liquid_top_pct": top_pct}})
    return build.build_table(rule, STAGE_SLICE, built.out_dir.parents[1], bl, build_id=f"b_{name}",
                             fixtures_path=fixtures_path)


def test_liquid_임계는_baseline_liquid_top_pct에서_온다(built: build.BuildResult,
                                                   tmp_path: Path) -> None:
    """상위 20% 로 바꾸면 술어가 `adv20_rank_pct >= 0.8`, threshold_value 0.2 — SQL 리터럴 없이
    `_const` 로만 들어온다(픽스처도 그 값으로 준다)."""
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps([
        {"case": "top20_pred", "key": {"policy": "liquid", "rule_seq": str(LIQUID_QUANTILE_SEQ)},
         "column": "predicate", "expect": "adv20_rank_pct >= 0.8", "source": "hand"},
        {"case": "top20_value", "key": {"policy": "liquid", "rule_seq": str(LIQUID_QUANTILE_SEQ)},
         "column": "threshold_value", "expect": "0.2", "source": "hand"}]), encoding="utf-8")
    r = _with_top_pct(built, "policy_top20", 0.2, fixtures_path=fx)
    assert r.ok, _fails(r)
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT predicate, threshold_kind, threshold_value FROM t "
                             f"WHERE policy = 'liquid' AND rule_seq = {LIQUID_QUANTILE_SEQ}") == [
        ("adv20_rank_pct >= 0.8", "quantile", 0.2)]
    assert _query(r.out_dir, "SELECT count(*) FROM t") == [(len(_ROWS),)]
    assert _gate(r, "EG3_policy").metrics["quantile_rows"] == {
        f"liquid#{LIQUID_QUANTILE_SEQ}": 0.2}


def test_liquid_top_pct가_비율_밖이면_EG3_policy가_폐기한다(built: build.BuildResult) -> None:
    """1.5 → quantile 임계가 (0, 1] 밖 — 술어 `adv20_rank_pct >= -0.5` 는 모집단 전부를 뽑는다."""
    r = _with_top_pct(built, "policy_top150", 1.5)
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_quantile_threshold_out_of_range"] == 1
    assert eg3.metrics["n_predicate_unbound"] == 0


def test_liquid_top_pct가_baseline에_없으면_빌드가_거절된다(built: build.BuildResult) -> None:
    """산출 규칙 상수는 `_const` 로만 — 미등재는 KeyError(사람 승인 지점, S03 `no_trade_run_k` 와
    같은 규약)."""
    assert built.out_dir is not None
    bl = Baseline({**SEED.data, POLICY.name: {"version": VERSION}})
    with pytest.raises(KeyError, match="liquid_top_pct"):
        build.build_table(POLICY, STAGE_SLICE, built.out_dir.parents[1], bl, build_id="b_no_pct")


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_없는_컬럼을_쓰는_predicate는_EG3_policy가_폐기한다(built: build.BuildResult,
                                                     tmp_path: Path) -> None:
    """아직 없는 컬럼(turnover_pct)을 predicate 에 쓰면 바인딩 실패 = 착수 순서 위반."""
    r = _variant(built, tmp_path, "policy_unbound",
                 f"{ALL}, ('liquid', 1, 'turnover_pct >= 0.2', 'quantile', 0.2, 'measured', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_predicate_unbound"] == 1
    assert eg3.metrics["unbound_predicates"] == ["liquid#1"]
    assert _gate(r, "EG3").status is GateStatus.PASS


def test_S03B_컬럼은_이제_바인딩된다(built: build.BuildResult, tmp_path: Path) -> None:
    """`adv20_krw` 위 절대 금액 quantile 행도 문법상 통과한다 — 정본은 S03B-2 의 `adv20_rank_pct`
    비율 행이지만, 표는 다른 임계 종류도 실을 수 있다(여기선 변종만)."""
    r = _variant(built, tmp_path, "policy_liquid_shape",
                 f"{ALL}, ('liquid', 1, 'adv20_krw >= 1000000', 'quantile', 0.2, 'measured', "
                 "DATE '2026-09-05')")
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.PASS and eg3.metrics["n_predicate_unbound"] == 0
    assert _gate(r, "EG4").status is GateStatus.FAIL          # 변종 이름엔 픽스처가 없다(정상)


def test_threshold_kind_어휘_밖이면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_bad_kind",
                 f"{ALL}, ('liquid', 1, 'halt_state = false', 'percentile', 0.2, 'measured', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_threshold_kind_outside_vocab"] == 1
    assert eg3.metrics["n_predicate_unbound"] == 0


def test_policy_어휘_밖이면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    """'common_stock'(밑줄) 은 어휘 밖 — universe_id 가 계약 값 'krx.common-stock' 과 달라진다."""
    r = _variant(built, tmp_path, "policy_bad_policy",
                 f"{ALL}, ('common_stock', 1, 'sec_type = ''common''', 'flag', NULL::DOUBLE, "
                 "'convention', NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_policy_outside_vocab"] == 1


def test_flag인데_임계값이_있으면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_flag_value",
                 "('all', 1, 'TRUE', 'flag', 1.0, 'convention', NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_threshold_value_mismatch"] == 1


def test_all_행이_없으면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    """`krx.all` 이 비면 정책 미적용 유니버스 자체가 사라진다."""
    r = _variant(built, tmp_path, "policy_no_all",
                 "('investable', 1, 'NOT halt_state', 'flag', NULL::DOUBLE, 'convention', "
                 "NULL::DATE)")
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_policy")
    assert eg3.metrics["n_all_missing"] == 1 and eg3.metrics["n_predicate_unbound"] == 0


def test_universe_id_문법이_어긋나면_폐기한다(built: build.BuildResult, tmp_path: Path) -> None:
    r = _variant(built, tmp_path, "policy_bad_id", ALL, universe_id="'krx_' || p.policy")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "EG3_policy").metrics["n_universe_id_bad_grammar"] == 1
