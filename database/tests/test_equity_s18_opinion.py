"""S18 `opinion_daily` · `opinion_broker_daily` — stage 절단본 위 실제 `build_table` 왕복
(DESIGN v1.2 §4-6 · GATES v1.0 §3 ⑳㉑ · FX-5-007·008 · EG6·EG8·EG9-P06).

손계산 기대값은 `stg_analyst_summary`·`stg_analyst_broker`·`stg_v3_analyst_opinions`·
`stg_wise_coverage` 원자료를 직접 열어 확인한 값이다(산출 SQL 로 얻은 값이 아니다). 절단본 실측:
  `stg_analyst_summary` 10 = 5종목 × 2일(09-01·09-02) · `stg_v3_analyst_opinions` 809 =
  8종목 × 100~102일(04-04~09-01, distinct (ticker, date) 809) · `stg_analyst_broker` 249 =
  5종목 × 26제공처 × 3일(09-01~03) · `stg_wise_coverage` 7(covered 5 · none 2)
  · 겹친 (ticker, obs_date) 5(= 2026-09-01 × 5종목)이고 값 5축이 전부 같다
  · (ticker, broker) 마다 `opinion_date` 가 하나뿐이라 `prev_opinion_date` 는 전 행 NULL
  · 목표가 ≤ 0 · `opinion_date > fetched_date` · `base_date > fetched_date` 전부 0.
두 테이블은 equity `security`·`security_span` 을 EG9 커버리지 축으로 읽으므로 같은 equity_root 에
`trading_calendar` → `security` → `security_span` 순으로 먼저 짓는다.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s18
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED = Baseline({**load(Path(rules_s01.__file__).parent / "baseline_seed_s01.json").data,
                 **load(Path(rules_s02.__file__).parent / "baseline_seed_s02.json").data,
                 **load(rules_s18.BASELINE_SEED).data})

N_WISE = 10                     # stg_analyst_summary 전건 (5종목 × 2일)
N_V3 = 809                      # stg_v3_analyst_opinions distinct (ticker, date)
N_OPINION = N_WISE + N_V3       # 819 — GATES §3 ⑳ 우변
N_BROKER = 249                  # stg_analyst_broker 전건 (1:1)
N_OVERLAP = 5                   # 두 원천이 같은 (ticker, obs_date) 를 가진 키
N_V3_NULL = 301                 # v3 값 전 축이 NULL 인 행 (커버 안 되는 종목·날)

OPINION_GATES = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_opinion_daily", "EG6", "EG8", "EG9",
                 "EG4", "EG5a"]
BROKER_GATES = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_opinion_broker_daily", "EG9",
                "EG4", "EG5a"]

# S18 두 테이블이 읽는 equity 입력을 짓기 위한 선행 체인 (WORKFLOW §3-2 S00─S01─S02).
CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN)


def _build_chain(equity_root: Path, stage_root: Path = STAGE_SLICE) -> None:
    for rule in CHAIN:
        r = build.build_table(rule, stage_root, equity_root, SEED, build_id=f"b_{rule.name}")
        assert r.ok, (rule.name, [(g.name, g.status.value, g.detail) for g in r.gates])


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, build.BuildResult]:
    """정상 왕복 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s18") / "equity"
    _build_chain(eq)
    out: dict[str, build.BuildResult] = {}
    for rule in rules_s18.TABLES:
        out[rule.name] = build.build_table(rule, STAGE_SLICE, eq, SEED,
                                           build_id=f"b_{rule.name}")
    return out


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _query(out_dir: Path, sql: str, stage: Path = STAGE_SLICE) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW od AS SELECT * FROM read_parquet("
                    f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true)")
        for t in ("stg_analyst_summary", "stg_analyst_broker", "stg_v3_analyst_opinions",
                  "stg_wise_coverage"):
            con.execute(f"CREATE OR REPLACE TEMP VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{stage / t}/**/*.parquet', hive_partitioning=true, "
                        "union_by_name=true)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 합성 stage 하네스 ────────────────────────────────────────────────────────
# 절단본에 사례가 없는 축(판본 2개·어휘 밖·목표가 0·관측일 역전·직전 의견일)은 손 트리로 만든다.
# 절단본 실물 테이블을 symlink 로 같은 stage 루트에 모아 두므로 선행 체인은 실물 위에서 지어진다.

def _hybrid_stage(tmp_path: Path, make_stage_tree, synthetic: dict[str, list[dict]]) -> Path:
    stage_root = tmp_path / "stage"
    stage_root.mkdir(parents=True, exist_ok=True)
    for d in sorted(STAGE_SLICE.iterdir()):
        if d.is_dir() and d.name not in synthetic:
            (stage_root / d.name).symlink_to(d)
    for table, rows in synthetic.items():
        make_stage_tree(tmp_path, table, rows, partition_class="whole")
    return stage_root


def _fixture_file(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    """합성 하네스용 골든 픽스처 — 정본 픽스처는 절단본 값이라 여기서는 못 쓴다(EG4 는 부재가 실패)."""
    p = tmp_path / f"fx_{len(list(tmp_path.glob('fx_*.json')))}.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return p


def _variant(tmp_path: Path, rule: EquityTable, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 부정 픽스처용 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    p = tmp_path / f"{name}.sql"
    p.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rule.__dict__, "name": name, "sql_path": p})


def _body(rule: EquityTable) -> str:
    return rule.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


_D = dt.date


def _summary_row(ticker: str, fetched: _D, base: _D, target: int = 500000,
                 observed: _D | None = None) -> dict[str, object]:
    return {"ticker": ticker, "fetched_date": fetched, "base_date": base,
            "opinion_score": 4.0, "target_price_krw": target, "eps_krw": 48339, "per": 5.38,
            "analyst_count": 24, "no_opinion_note": None, "observed_date": observed or fetched}


def _v3_row(ticker: str, date: _D, observed: _D, target: int = 500000) -> dict[str, object]:
    return {"ticker": ticker, "date": date, "opinion_score": 4.0, "target_price_krw": target,
            "estimated_eps": 48339, "estimated_per": 5.38, "analyst_count": 24,
            "observed_date": observed}


def _broker_row(ticker: str, fetched: _D, broker: str, opinion_date: _D, target: int = 600000,
                opinion: str = "BUY", opinion_class: str = "buy") -> dict[str, object]:
    return {"ticker": ticker, "fetched_date": fetched, "broker": broker,
            "opinion_date": opinion_date, "target_price_krw": target,
            "prev_target_price_krw": target, "change_pct": 0.0, "opinion": opinion,
            "prev_opinion": opinion, "opinion_class": opinion_class,
            "prev_opinion_class": opinion_class, "observed_date": fetched}


_COVERAGE = [{"ticker": "005930", "status_current": "covered",
              "checked_date_current": _D(2026, 9, 2)}]


def _build_synthetic(tmp_path: Path, make_stage_tree, rule: EquityTable,
                     synthetic: dict[str, list[dict]], fixtures: list[dict[str, object]],
                     *, eg7_off: bool = False) -> build.BuildResult:
    stage_root = _hybrid_stage(tmp_path, make_stage_tree, synthetic)
    eq = tmp_path / "equity"
    _build_chain(eq, stage_root)
    return build.build_table(rule, stage_root, eq, SEED, build_id=f"b_{rule.name}_syn",
                             fixtures_path=_fixture_file(tmp_path, fixtures),
                             gate_thresholds={"EG7": 1.0} if eg7_off else None)


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_체인_빌드가_전_게이트를_통과한다(built) -> None:
    od, ob = built["opinion_daily"], built["opinion_broker_daily"]
    assert od.ok, [(g.name, g.status.value, g.detail) for g in od.gates]
    assert ob.ok, [(g.name, g.status.value, g.detail) for g in ob.gates]
    assert [g.name for g in od.gates] == OPINION_GATES
    assert [g.name for g in ob.gates] == BROKER_GATES
    assert not [g for g in od.gates + ob.gates if g.status is GateStatus.FAIL]
    assert (od.n_rows, od.n_reject) == (N_OPINION, 0)
    assert (ob.n_rows, ob.n_reject) == (N_BROKER, 0)
    assert _gate(od, "EG1").metrics["lhs"] == _gate(od, "EG1").metrics["rhs"] == N_OPINION
    assert _gate(ob, "EG1").metrics["lhs"] == _gate(ob, "EG1").metrics["rhs"] == N_BROKER
    # equity 입력도 고정된다 — EG9 커버리지 축이 읽는 두 테이블
    assert od.inputs["security"] == "b_security"
    assert od.inputs["security_span"] == "b_security_span"
    assert set(ob.inputs) == {"stg_analyst_broker", "security", "security_span"}


def test_date_axis는_연도_디렉토리로_갈린다(built) -> None:
    for name, key in (("opinion_daily", "obs_date"), ("opinion_broker_daily", "fetched_date")):
        r = built[name]
        assert r.out_dir is not None
        assert [str(p["path"]).rsplit("/", 1)[-1] for p in r.partitions] == ["year=2026"]
        assert _query(r.out_dir, f"SELECT count(*) FROM od WHERE year <> year({key})") == [(0,)]


def test_값_축은_stage와_전건_동일하다(built) -> None:
    """원천 값은 옮기기만 한다. 산출 ↔ stage 를 양방향 anti-join 해 짝 없는 행 0."""
    r = built["opinion_daily"]
    assert r.out_dir is not None
    wise_same = ("s.ticker = o.ticker AND s.fetched_date = o.obs_date "
                 "AND s.opinion_score IS NOT DISTINCT FROM o.opinion_score "
                 "AND s.target_price_krw IS NOT DISTINCT FROM o.target_price_krw "
                 "AND s.eps_krw IS NOT DISTINCT FROM o.eps_krw "
                 "AND s.per IS NOT DISTINCT FROM o.per "
                 "AND s.analyst_count IS NOT DISTINCT FROM o.analyst_count "
                 "AND s.base_date IS NOT DISTINCT FROM o.base_date")
    v3_same = ("v.ticker = o.ticker AND v.date = o.obs_date "
               "AND v.opinion_score IS NOT DISTINCT FROM o.opinion_score "
               "AND v.target_price_krw IS NOT DISTINCT FROM o.target_price_krw "
               "AND v.estimated_eps IS NOT DISTINCT FROM o.eps_krw "
               "AND v.estimated_per IS NOT DISTINCT FROM o.per "
               "AND v.analyst_count IS NOT DISTINCT FROM o.analyst_count")
    assert _query(r.out_dir, f"SELECT count(*) FROM od o WHERE o.src = 'wise' AND NOT EXISTS "
                             f"(SELECT 1 FROM stg_analyst_summary s WHERE {wise_same})") == [(0,)]
    assert _query(r.out_dir, f"SELECT count(*) FROM stg_analyst_summary s WHERE NOT EXISTS "
                             f"(SELECT 1 FROM od o WHERE o.src = 'wise' AND {wise_same})") == [(0,)]
    assert _query(r.out_dir, f"SELECT count(*) FROM od o WHERE o.src = 'v3' AND NOT EXISTS "
                             f"(SELECT 1 FROM stg_v3_analyst_opinions v "
                             f"WHERE {v3_same})") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM stg_v3_analyst_opinions v WHERE NOT EXISTS "
                             f"(SELECT 1 FROM od o WHERE o.src = 'v3' AND {v3_same})") == [(0,)]
    # 결측은 결측 — v3 빈 행 301 이 0 으로 채워지지 않는다
    assert _query(r.out_dir, "SELECT count(*) FROM od WHERE src = 'v3' "
                             "AND opinion_score IS NULL") == [(N_V3_NULL,)]


def test_브로커_값_축은_stage와_전건_동일하다(built) -> None:
    r = built["opinion_broker_daily"]
    assert r.out_dir is not None
    same = ("b.ticker = o.ticker AND b.fetched_date = o.fetched_date AND b.broker = o.broker "
            "AND b.opinion_date = o.opinion_date "
            "AND b.target_price_krw IS NOT DISTINCT FROM o.target_price_krw "
            "AND b.prev_target_price_krw IS NOT DISTINCT FROM o.prev_target_price_krw "
            "AND b.change_pct IS NOT DISTINCT FROM o.change_pct "
            "AND b.opinion IS NOT DISTINCT FROM o.opinion "
            "AND b.opinion_class IS NOT DISTINCT FROM o.opinion_class "
            "AND b.prev_opinion IS NOT DISTINCT FROM o.prev_opinion "
            "AND b.prev_opinion_class IS NOT DISTINCT FROM o.prev_opinion_class")
    assert _query(r.out_dir, f"SELECT count(*) FROM od o WHERE NOT EXISTS "
                             f"(SELECT 1 FROM stg_analyst_broker b WHERE {same})") == [(0,)]
    assert _query(r.out_dir, f"SELECT count(*) FROM stg_analyst_broker b WHERE NOT EXISTS "
                             f"(SELECT 1 FROM od o WHERE {same})") == [(0,)]
    # 원문은 접지 않는다 — 같은 buy 등급 안에 표기 8종이 그대로 산다
    assert _query(r.out_dir, "SELECT count(DISTINCT opinion) FROM od") == [(8,)]


def test_두_원천이_같은_키를_가져도_src로_갈라져_공존한다(built) -> None:
    """상보 결합 — (ticker, obs_date) 는 겹쳐도 dedup 하지 않는다(PIT 축이 서로 다르다)."""
    r = built["opinion_daily"]
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM (SELECT ticker, obs_date FROM od "
                             "WHERE src = 'wise' INTERSECT SELECT ticker, obs_date FROM od "
                             "WHERE src = 'v3')") == [(N_OVERLAP,)]
    # 겹친 5키의 값 5축이 전부 같다(§10 P37) — EG8 이 재계산해 같은 수를 낸다
    assert _query(r.out_dir, """
        SELECT count(*) FROM od w JOIN od x ON x.ticker = w.ticker AND x.obs_date = w.obs_date
        WHERE w.src = 'wise' AND x.src = 'v3'
          AND w.opinion_score IS NOT DISTINCT FROM x.opinion_score
          AND w.target_price_krw IS NOT DISTINCT FROM x.target_price_krw
          AND w.eps_krw IS NOT DISTINCT FROM x.eps_krw
          AND w.per IS NOT DISTINCT FROM x.per
          AND w.analyst_count IS NOT DISTINCT FROM x.analyst_count""") == [(N_OVERLAP,)]
    eg8 = _gate(r, "EG8")
    assert eg8.status is GateStatus.SKIP and eg8.detail == "no_baseline"
    assert eg8.metrics["n_overlap_keys"] == N_OVERLAP
    assert eg8.metrics["agree_rate"] == 1.0
    assert eg8.metrics["missing_metric"] == "opinion_daily.src_overlap_agree_min"


def test_wise는_measured_v3는_default로_PIT축이_갈린다(built) -> None:
    """v3 에는 수집 시각 컬럼이 없다 — date 대용 + coverage_degraded (DESIGN §4-6)."""
    r = built["opinion_daily"]
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT src, available_basis, any_value(coverage_degraded), "
                             "count(*) FROM od GROUP BY 1, 2 ORDER BY 1") == [
        ("v3", "default", True, N_V3), ("wise", "measured", False, N_WISE)]
    assert _query(r.out_dir, "SELECT count(*) FROM od "
                             "WHERE available_date IS DISTINCT FROM obs_date") == [(0,)]
    # 미러 관측일(09-01·09-02)은 obs_date 와 별개 축으로 남는다 — v3 809행 **전부** 다르다.
    # 04~08월 라벨을 09-01·09-02 에 한꺼번에 관측한 사본이라는 뜻이고, basis 를 measured 가 아니라
    # default 로 두는 근거다(수집일을 잰 적이 없다).
    assert _query(r.out_dir, "SELECT count(*) FILTER (WHERE observed_date <> obs_date), "
                             "count(*) FILTER (WHERE observed_date < obs_date) FROM od "
                             "WHERE src = 'v3'") == [(N_V3, 0)]
    assert _gate(r, "EG2").metrics["n_available_before_content"] == 0


def test_절단본에는_직전_의견일_사례가_없다(built) -> None:
    """FX-5-008 — 3일치 안에서는 (ticker, broker) 마다 opinion_date 가 하나뿐이다.

    이 NULL 이 곧 "간격을 모르니 change_pct 를 비율로 읽지 말라" 는 근거다. 서버 9,717행에서
    실사례가 나오면 `fixtures/opinion_broker_daily.json` 에 non-NULL 케이스를 등재한다.
    """
    r = built["opinion_broker_daily"]
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT count(*) FROM od WHERE prev_opinion_date IS NULL") == [
        (N_BROKER,)]
    assert _query(r.out_dir, "SELECT count(*) FROM od "
                             "WHERE prev_opinion_date_available_date IS NOT NULL") == [(0,)]
    assert _query(r.out_dir, "SELECT count(*) FROM (SELECT ticker, broker, "
                             "count(DISTINCT opinion_date) c FROM od GROUP BY 1, 2 "
                             "HAVING c > 1)") == [(0,)]
    assert _gate(r, "EG3_opinion_broker_daily").metrics["n_prev_opinion_date_null"] == N_BROKER


def test_커버리지는_기록형이고_시총_분위는_S19_몫이다(built) -> None:
    """EG9-P06 — `universe_daily` 가 S18 입력이 아니라 분위 축을 낼 수 없다(GATES §9)."""
    for name, n_ticker in (("opinion_daily", 8), ("opinion_broker_daily", 5)):
        eg9 = _gate(built[name], "EG9")
        assert eg9.status is GateStatus.PASS
        assert eg9.metrics["n_ticker_out"] == n_ticker
        assert eg9.metrics["n_ticker_not_in_security"] == 0
        assert eg9.metrics["coverage_by_mktcap_quintile"] is None
        assert "S19" in str(eg9.metrics["coverage_by_mktcap_quintile_note"])
        # 커버는 보통주에만 있다 — 우선주·ETF·외국주는 0 (선택편향의 축, 기록형)
        assert {k: v[1] for k, v in eg9.metrics["covered_by_sec_type"].items()} == {
            "common": n_ticker, "etf": 0, "foreign": 0, "preferred": 0}
    # WISE 커버 대장(현재값 라벨)은 팩트 컬럼으로 내리지 않고 기록에만 쓴다
    eg9 = _gate(built["opinion_daily"], "EG9")
    assert eg9.metrics["wise_coverage_status"] == {"covered": 5, "none": 2}
    assert eg9.metrics["n_wise_covered_without_row"] == 0
    # 관측일이 커버리지 종료(2026-08-20) 뒤인 행 — WISE 수집은 09-01 시작(P16)
    assert eg9.metrics["n_row_after_span_end"] == 74


def test_재빌드가_같은_해시를_낸다(built) -> None:
    """EG5a — 같은 inputs 면 파티션 content_hash 전량 동일."""
    first = built["opinion_daily"].out_dir
    assert first is not None
    eq = first.parent.parent            # <equity_root>/<table>/v=<build>
    for rule in rules_s18.TABLES:
        r = build.build_table(rule, STAGE_SLICE, eq, SEED, build_id=f"b_{rule.name}_2")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
        eg5 = _gate(r, "EG5a")
        assert eg5.status is GateStatus.PASS, eg5.detail
        assert r.content_hash == built[rule.name].content_hash


# ── 판본 선택 (EG6) ──────────────────────────────────────────────────────────

_TWO_VERSIONS = {
    "stg_v3_analyst_opinions": [
        _v3_row("005930", _D(2026, 5, 4), _D(2026, 9, 1), target=100000),
        _v3_row("005930", _D(2026, 5, 4), _D(2026, 9, 2), target=200000),
        _v3_row("005930", _D(2026, 5, 6), _D(2026, 9, 1), target=300000)],
    "stg_analyst_summary": [_summary_row("005930", _D(2026, 9, 1), _D(2026, 8, 31))],
    "stg_wise_coverage": _COVERAGE,
}


def test_덮어쓰기_원천은_최초_관측만_남는다(tmp_path: Path, make_stage_tree) -> None:
    """v3 미러의 같은 (ticker, date) 판본 2개 중 `observed_date` 최소만 채택한다."""
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_DAILY, _TWO_VERSIONS,
                         [{"id": "syn", "key": {"ticker": "005930", "obs_date": "2026-05-04",
                                                "src": "v3"},
                           "column": "target_price_krw", "expect": "100000",
                           "source": "hand", "note": "observed 09-01 판본"}])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.n_rows == 3            # wise 1 + v3 distinct (ticker, date) 2
    assert _gate(r, "EG1").metrics["rhs"] == 3
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT obs_date, target_price_krw, observed_date, n_src_rows "
                             "FROM od WHERE src = 'v3' ORDER BY obs_date") == [
        (_D(2026, 5, 4), 100000, _D(2026, 9, 1), 2),
        (_D(2026, 5, 6), 300000, _D(2026, 9, 1), 1)]
    eg6 = _gate(r, "EG6")
    assert eg6.status is GateStatus.PASS
    assert eg6.metrics["n_v3_multi_version_keys"] == 1


def test_최신_관측을_고르면_EG6가_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """부정 픽스처(FX-N-005 대응) — 판본 선택을 max 로 뒤집으면 EG6 가 잡는다."""
    body = _body(rules_s18.OPINION_DAILY).replace(
        "ORDER BY o.observed_date NULLS LAST,", "ORDER BY o.observed_date DESC NULLS LAST,")
    assert "DESC" in body
    rule = _variant(tmp_path, rules_s18.OPINION_DAILY, "opinion_daily_latest", body)
    r = _build_synthetic(tmp_path, make_stage_tree, rule, _TWO_VERSIONS, [])
    assert r.status is build.BuildStatus.GATE_FAILED
    eg6 = _gate(r, "EG6")
    assert eg6.status is GateStatus.FAIL
    assert eg6.metrics["n_v3_not_first_observation"] == 1
    assert eg6.metrics["n_v3_payload_not_in_source"] == 0    # 값 자체는 원천에 있다


# ── 부정 픽스처: 등급 어휘 밖 ────────────────────────────────────────────────

def test_등급_어휘_밖이면_EG3가_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """stage 가 어휘 밖 분류를 내보내면 폐기한다 — equity 는 어휘를 계승만 하고 재계산하지 않는다."""
    rows = [_broker_row("005930", _D(2026, 9, 1), "KB", _D(2026, 8, 10)),
            _broker_row("005930", _D(2026, 9, 1), "NH투자", _D(2026, 8, 10),
                        opinion="STRONG BUY", opinion_class="STRONGBUY")]
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_BROKER_DAILY,
                         {"stg_analyst_broker": rows}, [])
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_opinion_broker_daily")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_opinion_class_outside_vocab"] == 1
    assert eg3.metrics["opinion_class_vocab"] == list(rules_s18.OPINION_CLASS_VOCAB)
    assert "n_opinion_class_outside_vocab=1" in eg3.detail


# ── 부정 픽스처: 목표가 ≤ 0 ──────────────────────────────────────────────────

def test_목표가가_0_이하면_격리된다(tmp_path: Path, make_stage_tree) -> None:
    """EG7 격리형 — 행을 버리지 않고 `_reject/nonpositive_target_price/` 로 보낸다."""
    rows = [_broker_row("005930", _D(2026, 9, 1), "KB", _D(2026, 8, 10), target=600000),
            _broker_row("005930", _D(2026, 9, 1), "NH투자", _D(2026, 8, 10), target=0)]
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_BROKER_DAILY,
                         {"stg_analyst_broker": rows},
                         [{"id": "syn", "key": {"ticker": "005930", "fetched_date": "2026-09-01",
                                                "broker": "KB", "opinion_date": "2026-08-10"},
                           "column": "target_price_krw", "expect": "600000", "source": "hand",
                           "note": "격리는 다른 행을 건드리지 않는다"}], eg7_off=True)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {"nonpositive_target_price": 1}
    assert _gate(r, "EG1").metrics["delta"] == 0      # 등식은 격리를 빼고 선다
    assert (r.out_dir / "_reject" / "reject_reason=nonpositive_target_price").is_dir()


def test_의견_요약도_목표가_0_이하를_격리한다(tmp_path: Path, make_stage_tree) -> None:
    synthetic = {"stg_analyst_summary": [
                     _summary_row("005930", _D(2026, 9, 1), _D(2026, 8, 31), target=500000),
                     _summary_row("000660", _D(2026, 9, 1), _D(2026, 8, 31), target=0)],
                 "stg_v3_analyst_opinions": [_v3_row("005930", _D(2026, 5, 4), _D(2026, 9, 1))],
                 "stg_wise_coverage": _COVERAGE}
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_DAILY, synthetic,
                         [{"id": "syn", "key": {"ticker": "005930", "obs_date": "2026-09-01",
                                                "src": "wise"},
                           "column": "target_price_krw", "expect": "500000", "source": "hand",
                           "note": "격리는 다른 행을 건드리지 않는다"}], eg7_off=True)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 1)
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {"nonpositive_target_price": 1}


# ── 부정 픽스처: 관측일 역전 ─────────────────────────────────────────────────

_INVERTED_BROKER = {"stg_analyst_broker": [
    _broker_row("005930", _D(2026, 9, 1), "KB", _D(2026, 8, 10)),
    _broker_row("005930", _D(2026, 9, 1), "NH투자", _D(2026, 9, 3))]}   # 의견일 > 수집일

_INVERTED_SUMMARY = {
    "stg_analyst_summary": [_summary_row("005930", _D(2026, 9, 1), _D(2026, 9, 3))],
    "stg_v3_analyst_opinions": [_v3_row("005930", _D(2026, 5, 4), _D(2026, 9, 1))],
    "stg_wise_coverage": _COVERAGE}


def test_관측일이_역전되면_격리된다(tmp_path: Path, make_stage_tree) -> None:
    """의견일이 수집일보다 뒤인 행은 PIT 위반이다 — 격리해 살리고 나머지 행을 지킨다."""
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_BROKER_DAILY,
                         _INVERTED_BROKER,
                         [{"id": "syn", "key": {"ticker": "005930", "fetched_date": "2026-09-01",
                                                "broker": "KB", "opinion_date": "2026-08-10"},
                           "column": "available_date", "expect": "2026-09-01", "source": "hand",
                           "note": "정상 행"}], eg7_off=True)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {"opinion_date_after_fetch": 1}
    assert _gate(r, "EG2").metrics["n_available_before_content"] == 0


def test_기준일이_수집일보다_뒤면_격리된다(tmp_path: Path, make_stage_tree) -> None:
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_DAILY, _INVERTED_SUMMARY,
                         [{"id": "syn", "key": {"ticker": "005930", "obs_date": "2026-05-04",
                                                "src": "v3"},
                           "column": "available_basis", "expect": "default", "source": "hand",
                           "note": "정상 행"}], eg7_off=True)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert _gate(r, "EG7").metrics["reject_by_reason"] == {"base_date_after_obs": 1}


def test_관측일_역전을_격리하지_않으면_EG2가_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """격리 술어를 지운 변종 — 살아남은 역전 행이 EG2-P02(available >= 내용일)를 깬다."""
    branch = "WHEN r.opinion_date > r.fetched_date THEN 'opinion_date_after_fetch'"
    body = _body(rules_s18.OPINION_BROKER_DAILY).replace(branch, "")
    assert branch not in body      # 주석에 남은 같은 낱말은 산출식이 아니다
    rule = _variant(tmp_path, rules_s18.OPINION_BROKER_DAILY, "opinion_broker_daily_keep", body)
    r = _build_synthetic(tmp_path, make_stage_tree, rule, _INVERTED_BROKER, [])
    assert r.status is build.BuildStatus.GATE_FAILED
    eg2 = _gate(r, "EG2")
    assert eg2.status is GateStatus.FAIL
    assert eg2.metrics["n_available_before_content"] == 1
    assert eg2.metrics["content_date_column"] == "opinion_date"


# ── 파생: prev_opinion_date (FX-5-008) ───────────────────────────────────────

def test_직전_의견일은_우리_관측_이력에서만_되찾는다(tmp_path: Path, make_stage_tree) -> None:
    """같은 (ticker, broker) 의 앞선 의견일을 싣되, **이 행 시점까지 관측된 것**만 후보다.

    09-02 에 처음 본 08-20 의견은 09-01 행을 채우지 못한다(look-ahead 금지). 동반
    `prev_opinion_date_available_date` 는 구성 행 available 의 max 다(EG2-P05).
    """
    rows = [_broker_row("005930", _D(2026, 9, 1), "KB", _D(2026, 8, 10), target=600000),
            _broker_row("005930", _D(2026, 9, 2), "KB", _D(2026, 8, 10), target=600000),
            _broker_row("005930", _D(2026, 9, 3), "KB", _D(2026, 8, 20), target=700000)]
    r = _build_synthetic(tmp_path, make_stage_tree, rules_s18.OPINION_BROKER_DAILY,
                         {"stg_analyst_broker": rows},
                         [{"id": "syn", "key": {"ticker": "005930", "fetched_date": "2026-09-03",
                                                "broker": "KB", "opinion_date": "2026-08-20"},
                           "column": "prev_opinion_date", "expect": "2026-08-10",
                           "source": "hand", "note": "09-01·09-02 관측에 실린 08-10 의견"}])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    assert _query(r.out_dir, "SELECT fetched_date, opinion_date, prev_opinion_date, "
                             "prev_opinion_date_available_date FROM od "
                             "ORDER BY fetched_date") == [
        (_D(2026, 9, 1), _D(2026, 8, 10), None, None),
        (_D(2026, 9, 2), _D(2026, 8, 10), None, None),
        (_D(2026, 9, 3), _D(2026, 8, 20), _D(2026, 8, 10), _D(2026, 9, 3))]
    eg3 = _gate(r, "EG3_opinion_broker_daily")
    assert eg3.metrics["n_prev_opinion_date_null"] == 2
    assert eg3.status is GateStatus.PASS       # 동반 available 독립 재계산 일치


def test_나중_관측으로_과거_행을_채우면_EG3가_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """PIT 제약(`p.fetched_date <= r.fetched_date`)을 지운 변종 — look-ahead 를 게이트가 잡는다.

    제약을 두 CTE 에서 다 지우면 09-01 행이 09-03 에야 본 08-10 의견을 직전 의견으로 갖고 동반
    available 도 09-03(미래)이 된다. 게이트는 제약을 건 채 stage 에서 다시 세므로 09-01 을 기대해
    어긋난다 — 산출 컬럼으로 산출을 확인하는 항진명제가 아니다(GATES §5-C5).
    """
    body = (_body(rules_s18.OPINION_BROKER_DAILY)
            .replace("     AND p.fetched_date <= r.fetched_date\n", "")
            .replace("     AND p.fetched_date <= q.fetched_date\n", ""))
    assert "p.fetched_date <= " not in body
    rule = _variant(tmp_path, rules_s18.OPINION_BROKER_DAILY, "opinion_broker_daily_leak", body)
    rows = [_broker_row("005930", _D(2026, 9, 1), "KB", _D(2026, 8, 20), target=700000),
            _broker_row("005930", _D(2026, 9, 3), "KB", _D(2026, 8, 10), target=600000)]
    r = _build_synthetic(tmp_path, make_stage_tree, rule, {"stg_analyst_broker": rows}, [])
    assert r.status is build.BuildStatus.GATE_FAILED
    eg3 = _gate(r, "EG3_opinion_broker_daily")
    assert eg3.status is GateStatus.FAIL
    assert eg3.metrics["n_prev_available_mismatch"] == 1
    assert r.out_dir is None      # 폐기 — 누출된 판이 커밋되지 않는다


# ── EG8 — 두 원천 겹침 ──────────────────────────────────────────────────────

def test_두_원천_값이_어긋나면_EG8이_폐기한다(tmp_path: Path, make_stage_tree) -> None:
    """상수를 등재하면 EG8 이 산다 — 겹친 키의 값이 갈리면 어느 쪽을 읽느냐로 팩터가 바뀐다."""
    synthetic = {
        "stg_analyst_summary": [_summary_row("005930", _D(2026, 9, 1), _D(2026, 8, 31),
                                             target=500000)],
        "stg_v3_analyst_opinions": [_v3_row("005930", _D(2026, 9, 1), _D(2026, 9, 2),
                                            target=400000)],
        "stg_wise_coverage": _COVERAGE}
    stage_root = _hybrid_stage(tmp_path, make_stage_tree, synthetic)
    eq = tmp_path / "equity"
    _build_chain(eq, stage_root)
    seeded = Baseline({**SEED.data, "opinion_daily": {"src_overlap_agree_min": 1.0}})
    r = build.build_table(rules_s18.OPINION_DAILY, stage_root, eq, seeded,
                          build_id="b_opinion_daily_eg8",
                          fixtures_path=_fixture_file(tmp_path, []))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg8 = _gate(r, "EG8")
    assert eg8.status is GateStatus.FAIL
    assert eg8.metrics["n_overlap_keys"] == 1 and eg8.metrics["n_agree_all_columns"] == 0
    assert eg8.metrics["n_agree_by_column"]["target_price_krw"] == 0
    assert eg8.metrics["n_agree_by_column"]["opinion_score"] == 1
    assert eg8.metrics["threshold"] == 1.0


# ── 선언 ─────────────────────────────────────────────────────────────────────

def test_선언이_DESIGN_4_6과_같다() -> None:
    od, ob = rules_s18.OPINION_DAILY, rules_s18.OPINION_BROKER_DAILY
    assert od.grain == ("ticker", "obs_date", "src")
    assert ob.grain == ("ticker", "fetched_date", "broker", "opinion_date")
    assert od.partition_class == ob.partition_class == "date_axis"
    assert od.available_basis == ("measured", "default") and ob.available_basis == ("measured",)
    assert od.content_date_column == "base_date" and ob.content_date_column == "opinion_date"
    # 현재값 라벨(`_current`)을 팩트 컬럼으로 내리지 않는다 — EG6-P03
    assert not [c for c in {**od.columns, **ob.columns} if c.endswith("_current")]
    # baseline 상수는 등재하지 않는다(EG8 만 사람 승인 대기)
    seed = json.loads(rules_s18.BASELINE_SEED.read_text(encoding="utf-8"))
    assert seed["opinion_daily"] == {} and seed["opinion_broker_daily"] == {}
    assert any(m["metric"] == "src_overlap_agree_min" for m in seed["_measured"])


def test_CLI가_두_테이블을_등록한다() -> None:
    from equity import __main__ as cli  # 등록 부작용(import) 자체가 검사 대상이다

    assert {"opinion_daily", "opinion_broker_daily"} <= set(cli.RULES)
    assert cli.RULES["opinion_daily"] is rules_s18.OPINION_DAILY


def test_합성_트리가_절단본을_건드리지_않는다(tmp_path: Path, make_stage_tree) -> None:
    """symlink 하네스는 읽기 전용이어야 한다 — 합성 테이블만 tmp 에 실물로 생긴다."""
    before = {p.name: p.stat().st_mtime for p in STAGE_SLICE.iterdir()}
    stage_root = _hybrid_stage(tmp_path, make_stage_tree,
                               {"stg_analyst_broker": [_broker_row("005930", _D(2026, 9, 1),
                                                                   "KB", _D(2026, 8, 10))]})
    assert (stage_root / "stg_analyst_summary").is_symlink()          # 실물은 절단본을 가리킨다
    assert (stage_root / "stg_analyst_summary").resolve() == (STAGE_SLICE /
                                                              "stg_analyst_summary").resolve()
    assert not (stage_root / "stg_analyst_broker").is_symlink()       # 합성만 실물 디렉토리
    shutil.rmtree(tmp_path, ignore_errors=True)
    assert before == {p.name: p.stat().st_mtime for p in STAGE_SLICE.iterdir()}
