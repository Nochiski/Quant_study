"""S19 `dataset_profile` — stage 절단본 위 전 테이블 체인 → 실제 `build_table` 왕복
(DESIGN v1.2 §4-7 · GATES v1.0 §2 27행 · §3 ㉒ · §4 FX-6-001~008 · EG2-P04/P06/P07 · EG9-P06).

이 슬라이스는 **전 equity 테이블(22)을 입력으로 고정**하므로 체인 전체를 먼저 지어야 한다. 느려서
모듈 픽스처로 한 번만 짓는다(절단본 22테이블 약 6초).

절단본 실측(2026-09-06): 프로파일 **66행**(FIELD_MAP §2 어휘 24 · equity 내부 스코프 42), 격리 0,
`price.*` 커버 100%(`open`·`high`·`low` 99.16%) · 컨센서스·의견 종목 커버 33.33%(15 중 5) ·
GAP-02 3계정(`borrowings`·`depreciation`·`interest_expense`) 커버 17.5/15/15%, 첫 관측 2023-11-14.

**절단본이 못 보는 축**: stage 절단본 트리에 `_meta.json` 이 없어 `lag_known` 이 전부 미상이고
EG2-P04 모집단이 빈다. 그래서 여기서는 `lag_known=false` 를 실은 `_meta` 를 손으로 만들어
게이트 함수에 직접 물린다(아래 「EG2-P04 하네스」) — 서버 실측 전에 술어가 항진인 채로 남지
않게 하는 유일한 방법이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from equity import build, gates, inputs, rules_s19
from equity.baseline import Baseline, load
from equity.gates import EquityGateContext
from equity.model import RULES, VALUE_TYPES

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SRC = Path(rules_s19.__file__).parent

# 6단계 선행 — WORKFLOW §3-2 의 순서 그래프를 그대로 편 것(입력이 먼저 커밋돼야 pin 이 된다).
CHAIN: tuple[str, ...] = (
    "trading_calendar", "corp", "security", "corp_ticker", "security_span", "index_daily",
    "price_daily", "corp_event", "adj_factor", "universe_daily", "universe_policy",
    "disclosure_version", "fin_std", "holder_daily", "ownership_snapshot", "audit_opinion",
    "shares_outstanding", "treasury_stock", "dividend_event", "consensus_daily",
    "opinion_daily", "opinion_broker_daily")

N_FIELDS = 66                    # 선언 행수 — 코드가 정본이라 서버에서도 같다
N_FIELD_MAP_SCOPE = 24           # FIELD_MAP §2 42 어휘 중 프로파일 행을 갖는 것
N_INTERNAL_SCOPE = 42            # equity 내부 스코프(price.adj_close·fin_std 계정·4B·유니버스 …)
N_FIELD_MAP_VOCAB = 42           # FIELD_MAP §2 표의 field_id 수 (check_field_map.py 와 같은 축)
PROFILE_GATES = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG2_dataset_profile", "EG9", "EG4", "EG5a"]


def _seed() -> Baseline:
    """전 슬라이스 seed 병합 — 체인이 S01~S18 의 `_const` 를 전부 요구한다."""
    merged: dict[str, dict[str, object]] = {}
    for path in sorted(SRC.glob("baseline_seed_s*.json")):
        for k, v in load(path).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


SEED = _seed()


def build_chain(equity_root: Path, tables: tuple[str, ...] = CHAIN) -> None:
    for name in tables:
        r = build.build_table(RULES[name], STAGE_SLICE, equity_root, SEED, build_id=f"b_{name}")
        assert r.ok, (name, [(g.name, g.status.value, g.detail) for g in r.gates])


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    """체인 22 + `dataset_profile` 1회. 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s19") / "equity"
    build_chain(eq)
    r = build.build_table(rules_s19.DATASET_PROFILE, STAGE_SLICE, eq, SEED,
                          build_id="b_dataset_profile")
    return eq, r


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


def _rows(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW dp AS SELECT * FROM read_parquet("
                    f"'{out_dir / '*.parquet'}')")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 빌드 왕복 ────────────────────────────────────────────────────────────────

def test_전_테이블_체인_위에서_프로파일이_지어지고_게이트가_전부_통과한다(built) -> None:
    _, r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == PROFILE_GATES
    assert r.n_rows == N_FIELDS
    assert r.n_reject == 0
    assert _gate(r, "EG1").status is gates.GateStatus.SKIP      # 선언표
    assert _gate(r, "EG1").detail == "declaration_table"
    assert _gate(r, "EG2").detail == "dimension_table"          # 프레임 EG2 는 차원표 skip
    assert _gate(r, "EG2_dataset_profile").status is gates.GateStatus.PASS


def test_선언_행수가_rules_모듈이_선언한_필드_수와_같다(built) -> None:
    """행수의 정본은 문서가 아니라 `EquityTable.field_profiles` 다."""
    _, r = built
    assert len(rules_s19.owned_fields()) == N_FIELDS == r.n_rows


def test_스코프별_행수(built) -> None:
    eq, r = built
    got = dict(_rows(r.out_dir, "SELECT field_scope, count(*) FROM dp GROUP BY 1 ORDER BY 1"))
    assert got == {"field_map": N_FIELD_MAP_SCOPE, "internal": N_INTERNAL_SCOPE}


def _field_map_vocab() -> set[str]:
    """`EQUITY_FIELD_MAP.md` §2 표의 첫 열 field_id — `check_field_map.py` 와 같은 파싱 축."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    from check_field_map import map_fields
    return map_fields(Path(__file__).parents[1] / "docs" / "EQUITY_FIELD_MAP.md")


def test_field_map_스코프_행은_전부_대응표_어휘_안이다(built) -> None:
    """`field_scope='field_map'` 인데 문서 §2 에 없으면 어휘가 갈린 것이다."""
    _, r = built
    vocab = _field_map_vocab()
    assert len(vocab) == N_FIELD_MAP_VOCAB
    got = {f for (f,) in _rows(r.out_dir, "SELECT field_id FROM dp WHERE field_scope = "
                                          "'field_map'")}
    assert got <= vocab
    assert len(got) == N_FIELD_MAP_SCOPE


def test_대응표_어휘_중_행이_없는_필드는_어댑터의_unavailable_이다(built) -> None:
    """원천이 없거나(미지원 9) 슬라이스가 없는(S08~S10) 필드는 프로파일에 실리지 않는다."""
    _, r = built
    got = {f for (f,) in _rows(r.out_dir, "SELECT field_id FROM dp")}
    absent = sorted(_field_map_vocab() - got)
    assert absent == sorted([
        # 원천 부재 9 (FIELD_MAP §3 미지원) + 미확인 1
        "benchmark.close", "flow.block_buy", "flow.block_sell", "credit.net_buy",
        "credit.collateral_value", "credit.loan_value", "credit.forced_liquidation",
        "event.earnings_surprise", "event.index_membership_change",
        "event.disclosure_sentiment",
        # 원천은 있고 슬라이스가 없다 — S08 수급 4 · S09 공매도 3 · S10 신용 1
        "flow.foreign_net_buy", "flow.institution_net_buy", "flow.retail_net_buy",
        "flow.foreign_ownership", "short.short_balance_ratio", "short.short_sale_value",
        "short.borrowed_quantity", "credit.margin_balance"])
    assert len(absent) == N_FIELD_MAP_VOCAB - N_FIELD_MAP_SCOPE


def test_같은_입력_재빌드는_파티션_해시가_같다(built) -> None:
    eq, first = built
    again = build.build_table(rules_s19.DATASET_PROFILE, STAGE_SLICE, eq, SEED,
                              build_id="b_dataset_profile_2")
    assert again.ok
    assert again.content_hash == first.content_hash
    assert _gate(again, "EG5a").status is gates.GateStatus.PASS


# ── 선언면이 코드에서 온다 ───────────────────────────────────────────────────

def test_랙은_문서가_아니라_rules_선언에서_온다(built) -> None:
    """산출 랙을 바꾸려면 `rules_s04.FIELDS` 를 고쳐야 한다 — 프로파일 SQL 엔 숫자가 없다."""
    _, r = built
    got = dict(_rows(r.out_dir, "SELECT field_id, recommended_lag_sessions FROM dp "
                                "WHERE field_id LIKE 'price.%' ORDER BY 1"))
    assert got["price.close"] == 0 and got["price.adj_close"] == 0
    # shares_out 이 stg_listing_daily(stage lag_known=false)에서 오므로 1 세션 (FIELD_MAP §2)
    assert got["price.market_cap"] == 1 and got["price.shares_outstanding"] == 1
    from equity import rules_s04, rules_s06
    decl = {f.field_id: f.recommended_lag_sessions
            for f in (*rules_s04.FIELDS, *rules_s06.FIELDS_ADJ)}
    assert {k: decl[k] for k in got} == got


def test_sql_본문에_랙_리터럴이_없다() -> None:
    body = (SRC / "sql" / "dataset_profile.sql").read_text(encoding="utf-8")
    statement = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("--"))
    assert "recommended_lag" in statement            # 컬럼은 나른다
    assert not any(ch.isdigit() for ch in statement)  # 숫자는 하나도 없다


def test_stage_원천은_equity_입력을_타고_전개된다() -> None:
    """`price.adj_close` 는 뷰 산출이라 소유 테이블(adj_factor)의 전이 폐포가 원천이다."""
    closure = rules_s19.stage_closure("adj_factor")
    assert "stg_price_daily" in closure            # adj_factor → price_daily → stage
    assert "stg_event_cr" in closure               # 직접 stage 입력
    assert "stg_index_daily" in closure            # adj_factor → trading_calendar → stage
    assert all(t.startswith("stg_") for t in closure)


def test_뷰_필드는_산출처를_뷰_이름으로_밝힌다(built) -> None:
    _, r = built
    (table, basis), = _rows(r.out_dir, "SELECT table_name, available_date_basis FROM dp "
                                       "WHERE field_id = 'price.adj_close'")
    assert table == "v_adj_price_fwd"
    # 원주가(default) × 계수(derived) 를 함께 보는 뷰라 두 어휘가 합쳐진다 (DESIGN §5)
    assert basis == "default|derived"


def test_cell_kind_는_엔진_어휘를_쓴다(built) -> None:
    _, r = built
    kinds = {k for (row,) in _rows(r.out_dir, "SELECT supported_cell_kinds FROM dp")
             for k in row}
    assert kinds == {"observed", "missing", "coverage_gap"}     # 격자 테이블이 아직 없다


def test_value_type_은_엔진_FieldValueType_어휘_안이다(built) -> None:
    _, r = built
    got = {v for (v,) in _rows(r.out_dir, "SELECT DISTINCT value_type FROM dp")}
    assert got <= set(VALUE_TYPES)


# ── 커버 실측 ────────────────────────────────────────────────────────────────

def test_커버율은_0에서_100_사이이고_격자_축은_분위_다섯을_낸다(built) -> None:
    _, r = built
    bad = _rows(r.out_dir, "SELECT field_id FROM dp WHERE estimated_coverage_pct IS NULL "
                           "OR estimated_coverage_pct < 0 OR estimated_coverage_pct > 100")
    assert bad == []
    widths = _rows(r.out_dir, "SELECT field_id, len(coverage_by_mktcap_quintile) FROM dp "
                              "WHERE coverage_basis LIKE 'grid%'")
    assert widths and {w for _, w in widths} == {5}
    assert _rows(r.out_dir, "SELECT field_id FROM dp WHERE coverage_basis NOT LIKE 'grid%' "
                            "AND coverage_by_mktcap_quintile IS NOT NULL") == []


def test_가격_필드는_캘린더_전_구간을_덮는다(built) -> None:
    _, r = built
    (lo, hi, pct), = _rows(r.out_dir, "SELECT coverage_from, coverage_to, "
                                      "estimated_coverage_pct FROM dp "
                                      "WHERE field_id = 'price.close'")
    assert (str(lo), str(hi), pct) == ("2010-01-04", "2026-08-20", 100.0)


def test_선언은_있는데_값이_없는_필드는_커버율_0_으로_남는다(built) -> None:
    """`corp_event` 는 MVP 4유형만 적재해 자사주·유상증자 행이 0 이다 — 격리가 아니라 사실이다."""
    _, r = built
    got = dict(_rows(r.out_dir, "SELECT field_id, estimated_coverage_pct FROM dp "
                                "WHERE table_name = 'corp_event' ORDER BY 1"))
    assert got == {"event.buyback_amount": 0.0, "event.capital_raise_amount": 0.0}
    assert r.n_reject == 0


def test_GAP02_세_계정은_실재하고_첫_관측일이_기록된다(built) -> None:
    """'depreciation·borrowings·interest_expense 가 있는가' 가 GAP-02 의 질문이었다."""
    _, r = built
    got = {f: (str(lo), pct) for f, lo, pct in _rows(
        r.out_dir, "SELECT field_id, coverage_from, estimated_coverage_pct FROM dp "
                   "WHERE field_id IN ('financial.borrowings', 'financial.depreciation', "
                   "'financial.interest_expense') ORDER BY 1")}
    assert set(got) == {"financial.borrowings", "financial.depreciation",
                        "financial.interest_expense"}
    assert all(pct > 0 for _, pct in got.values())
    assert got["financial.borrowings"][0] == "2023-11-14"


def test_컨센서스_커버는_종목_축이라_일별_격자로_나누지_않는다(built) -> None:
    """월 1회 관측을 일별 셀로 나누면 구조적으로 낮게 나온다 — FIELD_MAP §3 '커버 종목' 축."""
    _, r = built
    rows = _rows(r.out_dir, "SELECT field_id, coverage_basis, estimated_coverage_pct FROM dp "
                            "WHERE table_name IN ('consensus_daily', 'opinion_daily') ORDER BY 1")
    assert {b for _, b, _ in rows} == {"grid_security"}
    # 절단본 유니버스 15종목 중 컨센서스·의견 커버 5 = 33.33%
    assert {round(p, 2) for _, _, p in rows} == {33.33, 26.67}


# ── EG2-P04 하네스 (절단본 stage 에 `_meta` 가 없어 모집단이 비는 축) ────────

def _profile_ctx(equity_root: Path, out_dir: Path,
                 lag_known: dict[str, bool]) -> EquityGateContext:
    """산출 parquet 위에 게이트 문맥을 세운다. `pinned` 메타만 손으로 채운다."""
    con = duckdb.connect()
    con.execute("CREATE OR REPLACE TEMP VIEW out_pq AS SELECT * FROM read_parquet("
                f"'{out_dir / '*.parquet'}')")
    pinned = {"universe_daily": inputs.PinnedBuild(
        "universe_daily", "b_universe_daily", equity_root / "universe_daily",
        (equity_root / "universe_daily",), {"lag_known_inputs": lag_known})}
    n = con.execute("SELECT count(*) FROM out_pq").fetchone()
    return EquityGateContext(
        con=con, rule=rules_s19.DATASET_PROFILE, out_view="out_pq", reject_view=None,
        pinned=pinned, n_out=int(str(n[0])), n_reject=0, reject_by_reason={},
        inputs={}, partition_hashes={}, baseline=SEED)


def test_lag_known_false_인_stage_원천은_세션_랙_1_이상인_행을_요구한다(built) -> None:
    eq, r = built
    # `stg_fin` 은 fin_std 계열 필드(랙 1)가 원천으로 싣는다 → 통과
    ok = rules_s19.eg2_dataset_profile(_profile_ctx(eq, r.out_dir, {"stg_fin": False}))
    assert ok.status is gates.GateStatus.PASS
    assert ok.metrics["lag_known_false_inputs"] == ["stg_fin"]
    assert ok.metrics["uncovered_lag_known_false"] == []


def test_어떤_필드도_싣지_않은_lag_known_false_원천은_EG2를_폐기한다(built) -> None:
    """S08 수급 격자가 붙었는데 프로파일 행을 안 만들면 여기서 잡힌다."""
    eq, r = built
    bad = rules_s19.eg2_dataset_profile(
        _profile_ctx(eq, r.out_dir, {"stg_flow_daily_kiwoom": False}))
    assert bad.status is gates.GateStatus.FAIL
    assert bad.metrics["uncovered_lag_known_false"] == ["stg_flow_daily_kiwoom"]
    assert "n_lag_known_false_without_profile=1" in bad.detail


def test_절단본은_lag_known_을_못_재고_그_사실을_기록한다(built) -> None:
    """stage 절단본에 `_meta.json` 이 없다 — 술어가 항진인 것을 metrics 가 드러낸다."""
    _, r = built
    m = _gate(r, "EG2_dataset_profile").metrics
    assert m["lag_known_false_inputs"] == []
    assert len(m["lag_known_unmeasured_inputs"]) > 20


# ── 기록 ─────────────────────────────────────────────────────────────────────

def test_EG9_는_시총_분위별_커버율을_기록으로_남긴다(built) -> None:
    _, r = built
    g = _gate(r, "EG9")
    assert g.status is gates.GateStatus.PASS
    q = g.metrics["coverage_by_mktcap_quintile"]
    assert len(q["price.close"]) == 5 and set(q["price.close"]) == {100.0}
    # 컨센서스는 대형주로 기운다 — 하위 두 분위 커버 0
    assert q["consensus.forward_eps"][0] == 0.0 and q["consensus.forward_eps"][4] > 0.0


def test_meta_에_게이트_판정이_남는다(built) -> None:
    _, r = built
    meta = json.loads((r.out_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["table"] == "dataset_profile"
    assert [g["name"] for g in meta["gates"]] == PROFILE_GATES
    assert meta["n_rows"] == N_FIELDS
