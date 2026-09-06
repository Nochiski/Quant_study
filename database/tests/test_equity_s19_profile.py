"""S19 `dataset_profile` — stage 절단본 위 전 테이블 체인 → 실제 `build_table` 왕복
(DESIGN v1.2 §4-7 · GATES v1.0 §2 27행 · §3 ㉒ · §4 FX-6-001~008 · EG2-P04/P06/P07 · EG9-P06).

이 슬라이스는 **전 equity 테이블(25)을 입력으로 고정**하므로 체인 전체를 먼저 지어야 한다. 느려서
모듈 픽스처로 한 번만 짓는다(절단본 25테이블 약 20초).

절단본 실측(2026-09-06 S19-2): 프로파일 **72행**(FIELD_MAP §2 어휘 30 · equity 내부 스코프 42),
격리 0, `price.*` 커버 100%(`open`·`high`·`low` 99.16%) · 컨센서스·의견 종목 커버 33.33%(15 중 5) ·
GAP-02 3계정(`borrowings`·`depreciation`·`interest_expense`) 커버 17.5/15/15%, 첫 관측 2023-11-14.
격자 3테이블(S19-2)의 6필드 커버: `flow.*` 54.72% · `short.short_sale_value` 41.94% ·
`short.borrowed_quantity` 15.24%(창 2014-01-02~2019-02-12) · `credit.margin_balance` 60.01%.

**절단본이 못 보는 축**: stage 절단본 트리에 `_meta.json` 이 없어 `lag_known` 이 전부 미상이고
EG2-P04 모집단이 빈다. 그래서 여기서는 `lag_known=false` 를 실은 `_meta` 를 손으로 만들어
게이트 함수에 직접 물린다(아래 「EG2-P04 하네스」) — 서버 실측 전에 술어가 항진인 채로 남지
않게 하는 유일한 방법이다.

**픽스처와 손계산의 경계(§9 S19 2차)**: 골든 픽스처(`fixtures/dataset_profile.json`)는 **모집단
비의존 선언값만** 담는다 — 창·커버율·분위처럼 모집단이 바뀌면 달라지는 값을 굳히면 서버에서 EG4 가
폐기한다(실제로 `financial.borrowings.coverage_from` 이 절단본 2023-11-14 / 서버 2016-03-30 이라
1차가 막혔다). 그 실측 단언은 이 파일의 손계산 테스트가 맡는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from equity import build, gates, inputs, rules_s19
from equity.baseline import Baseline, load
from equity.gates import EquityGateContext
from equity.model import CELL_KINDS, RULES, VALUE_TYPES

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SRC = Path(rules_s19.__file__).parent

# 6단계 선행 — WORKFLOW §3-2 의 순서 그래프를 그대로 편 것(입력이 먼저 커밋돼야 pin 이 된다).
CHAIN: tuple[str, ...] = (
    "trading_calendar", "corp", "security", "corp_ticker", "security_span", "index_daily",
    "price_daily", "corp_event", "adj_factor", "universe_daily", "universe_policy",
    # 격자 3(S08~S10) — `universe_daily` 격자와 `price_daily`(credit 게이트 축) 뒤
    "flow_daily", "short_daily", "credit_daily",
    "disclosure_version", "fin_std", "holder_daily", "ownership_snapshot", "audit_opinion",
    "shares_outstanding", "treasury_stock", "dividend_event", "consensus_daily",
    "opinion_daily", "opinion_broker_daily")

N_FIELDS = 72                    # 선언 행수 — 코드가 정본이라 서버에서도 같다
N_FIELD_MAP_SCOPE = 30           # FIELD_MAP §2 42 어휘 중 프로파일 행을 갖는 것
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
    """체인 25 + `dataset_profile` 1회. 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""
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
    """원천이 없거나(미지원 10) 컬럼이 이 슬라이스에 없는 필드는 프로파일에 실리지 않는다.

    S19-2 가 격자 3테이블의 6필드를 선언해 「슬라이스 미구현」 8 중 6 이 빠졌다. 남은 둘은
    **컬럼 자체가 없다** — `flow.foreign_ownership` 은 원천이 `stg_foreign_daily`(ka10008)라
    S08 이 싣지 않았고(S08-2), `short.short_balance_ratio` 는 공매도량 ÷ 상장주식수라 비율
    계산이 팩터층 몫이다. 선언만 하고 NULL 로 두면 준비도가 거짓으로 ready 가 된다.
    """
    _, r = built
    got = {f for (f,) in _rows(r.out_dir, "SELECT field_id FROM dp")}
    absent = sorted(_field_map_vocab() - got)
    assert absent == sorted([
        # 원천 부재 10 (FIELD_MAP §3 미지원 + GAP-09 benchmark)
        "benchmark.close", "flow.block_buy", "flow.block_sell", "credit.net_buy",
        "credit.collateral_value", "credit.loan_value", "credit.forced_liquidation",
        "event.earnings_surprise", "event.index_membership_change",
        "event.disclosure_sentiment",
        # 격자 테이블에 컬럼이 없다 — S08-2 대기 1 · 팩터층 계산 1
        "flow.foreign_ownership", "short.short_balance_ratio"])
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


GRID_FIELDS = {
    # field_id: (소유 테이블, 컬럼, 단위, value_type)
    "flow.foreign_net_buy": ("flow_daily", "frgnr_invsr_krw", "KRW", "amount"),
    "flow.institution_net_buy": ("flow_daily", "orgn_krw", "KRW", "amount"),
    "flow.retail_net_buy": ("flow_daily", "ind_invsr_krw", "KRW", "amount"),
    "short.short_sale_value": ("short_daily", "short_value_kiwoom_krw", "KRW", "amount"),
    "short.borrowed_quantity": ("short_daily", "lending_balance_kis_shr", "주", "count"),
    "credit.margin_balance": ("credit_daily", "whol_loan_rmnd_stcn_shr", "주", "count"),
}


def test_격자_3테이블은_어댑터가_내는_6필드만_선언한다(built) -> None:
    """S19-2 — 선언의 정본은 `rules_s08/s09/s10` 의 `field_profiles` 다.

    **어댑터가 못 내는 필드는 선언하지 않는다**: 선언만 하고 값이 없으면 `dataset_profile` 에
    '있는데 늘 빈' 행이 생겨 S20 준비도가 거짓으로 ready 가 된다. 대응은
    `backend/.../equity_duckdb/_specs.py` 의 `FIELD_SPECS` 격자 6행이다.
    """
    _, r = built
    rows = _rows(r.out_dir, "SELECT field_id, table_name, column_scope, unit, value_type, "
                            "frequency, available_date_basis, recommended_lag_sessions, "
                            "recommended_lag_days, point_in_time, coverage_basis "
                            "FROM dp WHERE table_name IN ('flow_daily', 'short_daily', "
                            "'credit_daily') ORDER BY 1")
    assert {r0[0] for r0 in rows} == set(GRID_FIELDS)
    for field_id, table, col, unit, value_type, freq, basis, lag_s, lag_d, pit, cov in rows:
        assert (table, col, unit, value_type) == GRID_FIELDS[field_id], field_id
        assert freq == "session" and cov == "grid_session", field_id
        # available_date = date(basis default) 인 팩트 축이고, 공표 랙은 여기서만 낸다
        assert basis == "default", field_id
        # 세 원장 전부 stage lag_known=false — STAGE_HANDOFF §2 · FIELD_MAP §1 「나머지 1 세션」
        assert (lag_s, lag_d) == (1, 1), field_id
        assert pit is True, field_id


def test_격자_필드_중_미결_조건이_남은_것은_기관_순매수_하나다(built) -> None:
    """GAP-03 — `orgn` 은 원장의 합계 컬럼인데 기관 7주체 합과 다르고 값의 기준이 공표되지
    않았다. 합계 컬럼을 쓸지 7주체를 다시 합할지가 소비 측에 남아 S20 이 partial_support 로
    옮긴다. 나머지 5필드는 단위·산출 규칙·원천 선택이 전부 닫혀 있다."""
    _, r = built
    got = dict(_rows(r.out_dir, "SELECT field_id, requires_confirmation FROM dp "
                                "WHERE table_name IN ('flow_daily', 'short_daily', "
                                "'credit_daily') ORDER BY 1"))
    assert {k for k, v in got.items() if v} == {"flow.institution_net_buy"}


def test_격자_필드의_stage_원천은_그_테이블의_원장을_포함한다(built) -> None:
    _, r = built
    got = {f: set(s) for f, s in _rows(
        r.out_dir, "SELECT field_id, source_stage_tables FROM dp "
                   "WHERE table_name IN ('flow_daily', 'short_daily', 'credit_daily')")}
    assert {"stg_flow_daily_kiwoom", "stg_flow_split_daily"} <= got["flow.foreign_net_buy"]
    assert "stg_short_daily_kiwoom" in got["short.short_sale_value"]
    assert "stg_loan_daily_kis" in got["short.borrowed_quantity"]
    assert "stg_credit_daily" in got["credit.margin_balance"]


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
    """격자 테이블(`fill_kind*`)만 5종 전부를 받고 나머지는 3종이다 — 유도는 소유 테이블의
    컬럼에서 기계적으로 나온다(선언 중복 금지, `rules_s19.supported_cell_kinds`)."""
    _, r = built
    kinds = {k for (row,) in _rows(r.out_dir, "SELECT supported_cell_kinds FROM dp")
             for k in row}
    assert kinds == set(CELL_KINDS)
    by_table = {t: sorted(k) for t, k in _rows(
        r.out_dir, "SELECT table_name, any_value(supported_cell_kinds) FROM dp GROUP BY 1")}
    # `short_daily` 는 원천마다 fill_kind 를 두어 컬럼 이름이 `fill_kind_short_kiwoom` 부류다
    for grid in ("flow_daily", "short_daily", "credit_daily"):
        assert by_table[grid] == sorted(CELL_KINDS), grid
    assert by_table["price_daily"] == sorted(["observed", "missing", "coverage_gap"])


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


def test_내부자_지분_필드는_롤링_2년_창을_coverage_from_으로_남긴다(built) -> None:
    """FIELD_MAP §2 의 '롤링 2년' 조건은 판정이 아니라 창이다 — 그래서 골든 픽스처가 아니라
    절단본 손계산으로 잰다(창은 모집단·수집 시점에 따라 움직인다)."""
    _, r = built
    got = {f: (str(lo), str(hi)) for f, lo, hi in _rows(
        r.out_dir, "SELECT field_id, coverage_from, coverage_to FROM dp WHERE field_id IN "
                   "('event.insider_net_buy', 'event.insider_stake_change') ORDER BY 1")}
    assert got["event.insider_net_buy"] == ("2024-08-26", "2026-08-26")
    assert got["event.insider_stake_change"] == got["event.insider_net_buy"]


def test_커버율은_정수_분자_분모의_순수_함수다(built) -> None:
    """산출에 실리는 유일한 부동소수 — 표만 보고 재계산이 되어야 content_hash 가 안정하다."""
    _, r = built
    rows = _rows(r.out_dir, "SELECT field_id, n_observed, n_denominator, estimated_coverage_pct "
                            "FROM dp ORDER BY 1")
    assert len(rows) == N_FIELDS
    for field_id, n_obs, n_den, pct in rows:
        assert 0 <= n_obs <= n_den, field_id
        decimals = rules_s19.COVERAGE_PCT_DECIMALS
        expect = 0.0 if n_den == 0 else round(100.0 * n_obs / n_den, decimals)
        assert pct == expect, field_id
    # 게이트도 같은 항등을 SQL 로 본다
    assert _gate(r, "EG2_dataset_profile").metrics["n_pct_not_derivable"] == 0


# ── 결정성 (서버 해시 불일치의 회귀, §9 S19 2차) ──────────────────────────────

N_DETERMINISM_BUILDS = 5


def test_같은_입력으로_다섯_번_지어도_해시가_같다(built) -> None:
    """재빌드 1회 검사로는 못 잡은 축 — 커버 실측은 격자를 병렬로 훑으므로 스캔 순서가 값에
    새면 여기서 드러난다. 서버 실측에서 같은 입력의 두 빌드가 다른 해시를 냈다(§9 S19 2차)."""
    eq, first = built
    hashes = {first.content_hash}
    # keep 을 넉넉히 준다 — 기본 3 이면 manifest GC 가 모듈 픽스처의 원본 `v=` 를 지워
    # 뒤 테스트가 읽을 산출이 사라진다(빌드 프레임의 정상 동작).
    keep = N_DETERMINISM_BUILDS + 2
    for i in range(N_DETERMINISM_BUILDS - 1):
        r = build.build_table(rules_s19.DATASET_PROFILE, STAGE_SLICE, eq, SEED,
                              build_id=f"b_det_{i}", threads=3, keep=keep)
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
        hashes.add(r.content_hash)
    assert len(hashes) == 1, hashes


def test_분위_경계의_동률은_총순서로만_결정된다(tmp_path: Path) -> None:
    """**서버 비결정성의 원인**: `ntile` 의 ORDER BY 가 유일하지 않으면 동률 행이 어느 분위로
    가는지 SQL 이 정하지 않는다 — 답이 엔진의 행 순서(병렬 스캔·정렬·스필)에 맡겨진다.

    아래는 동률이 분위 경계를 가로지르는 최소 사례다. 총순서가 없으면 입력 행 순서를 바꾸는
    것만으로 커버율 배열이 달라지고, `mktcap_krw, ticker` 로 못박으면 불변이다.
    """
    rows = [("A", 100), ("B", 200), ("C", 200), ("D", 200), ("E", 200),
            ("F", 200), ("G", 200), ("H", 300), ("I", 400), ("J", 500)]
    covered = ("B", "D", "F")

    def quintiles(order: list[tuple[str, int]], order_by: str) -> tuple:
        con = duckdb.connect()
        try:
            values = ", ".join(f"('{t}', {m})" for t, m in order)
            con.execute(f"CREATE TEMP TABLE g AS SELECT * FROM (VALUES {values}) "
                        "v(ticker, mktcap_krw)")
            obs = ", ".join(f"('{t}')" for t in covered)
            con.execute(f"CREATE TEMP TABLE o AS SELECT * FROM (VALUES {obs}) v(ticker)")
            got = con.execute(
                f"WITH s AS (SELECT ticker, ntile(5) OVER (ORDER BY {order_by}) AS q FROM g) "
                "SELECT s.q, count(o.ticker), count(*) FROM s LEFT JOIN o ON o.ticker = s.ticker "
                "GROUP BY s.q ORDER BY s.q").fetchall()
            return tuple((int(a), int(b), int(c)) for a, b, c in got)
        finally:
            con.close()

    orders = [rows, list(reversed(rows)), rows[3:] + rows[:3]]
    assert len({quintiles(o, "mktcap_krw") for o in orders}) > 1          # 총순서 없음 = 미결정
    assert len({quintiles(o, "mktcap_krw, ticker") for o in orders}) == 1  # 총순서 = 순수 함수


def test_분위_지도는_총순서로_한_번만_굽는다(built) -> None:
    """구현이 실제로 그 총순서를 쓰는지 — SQL 본문과 산출 양쪽으로 본다."""
    import inspect
    src = inspect.getsource(rules_s19.install_grid_quintiles)
    assert "ORDER BY mktcap_krw, ticker" in src        # 종목 축
    assert "PARTITION BY date ORDER BY mktcap_krw, ticker" in src  # 세션 축
    eq, r = built
    # 같은 격자에서 두 번 구워도 지도가 같다
    con = duckdb.connect()
    try:
        con.execute("SET threads = 3")
        glob = str(eq / "universe_daily" / "v=b_universe_daily" / "year=*" / "*.parquet")
        con.execute("CREATE OR REPLACE TEMP VIEW universe_daily AS SELECT * FROM "
                    f"read_parquet('{glob}', hive_partitioning=true)")
        seen = set()
        for _ in range(3):
            rules_s19.install_grid_quintiles(con)
            seen.add(con.execute(
                "SELECT bit_xor(hash(ticker || '|' || date || '|' || q)) FROM _grid_quintile"
            ).fetchone()[0])
        assert len(seen) == 1
    finally:
        con.close()


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
    """원천을 실었는데 프로파일 행을 안 만들면 여기서 잡힌다.

    S19-2 전에는 `stg_flow_daily_kiwoom` 이 이 예였다 — S08 격자가 커밋됐는데 선언이 비어 있어
    수급 원천을 아무 필드도 덮지 않았다. 지금은 `flow.*` 3필드가 랙 1 로 덮으므로 예를
    `stg_foreign_daily`(ka10008 외국인 보유, S08-2 대기)로 옮긴다.
    """
    eq, r = built
    covered = rules_s19.eg2_dataset_profile(
        _profile_ctx(eq, r.out_dir, {"stg_flow_daily_kiwoom": False}))
    assert covered.status is gates.GateStatus.PASS      # S19-2 가 덮었다
    bad = rules_s19.eg2_dataset_profile(
        _profile_ctx(eq, r.out_dir, {"stg_foreign_daily": False}))
    assert bad.status is gates.GateStatus.FAIL
    assert bad.metrics["uncovered_lag_known_false"] == ["stg_foreign_daily"]
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
