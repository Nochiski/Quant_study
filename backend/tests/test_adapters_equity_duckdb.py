"""워크벤치 equity 어댑터(`adapters/outbound/equity_duckdb`, S21 축소) — 손 픽스처 위 동작 검증.

픽스처는 `tests/equity_fixture.build_workbench_root`(캘린더 13세션, 000660 2:1 분할·정지일, 재상장
036220, 우선주·ETF, 정책표 2개). 산출물 parquet 에 의존하지 않는다(`.claude/rules/testing.md`).
어댑터 모듈은 duckdb 를 지연 import 하므로 여기 import 는 안전하고, 실제 사용은 importorskip 뒤다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_SPECS,
    UNSUPPORTED_FIELDS,
)
from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
    EquityDuckdbAdapter,
    EquityDuckdbSetupError,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import BacktestDataQuery
from strategy_workbench.application.factor_research.facade.ports import FactorObservationQuery
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    FieldLag,
    ResearchPanelQuery,
    UniverseHistoryQuery,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.strategy.facade.specification import (
    DataStep,
    FactorDirection,
    FactorGraph,
    FactorSignal,
    FactorStep,
    FieldNode,
    Market,
    RebalanceFrequency,
    StrategySpec,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from tests.equity_fixture import (
    WB_HALT_DATE,
    WB_SESSIONS,
    WB_SPLIT_DATE,
    build_workbench_root,
    snapshot_id,
    table_builds,
    wb_close,
    write_catalog,
)

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

START, END = date(2024, 1, 8), date(2024, 1, 12)
PRICE_FIELDS = ("price.close", "price.adj_close", "price.market_cap")
# 손 픽스처가 원천을 다 갖췄을 때 어댑터가 내는 field_id — FIELD_MAP §2 의 42 중 23 +
# equity 내부 스코프 `price.adj_close`. 나머지 19 의 사유는 `_specs.UNSUPPORTED_FIELDS` 다.
ALL_FIELDS = (
    "price.close", "price.open", "price.volume", "price.market_cap",
    "price.shares_outstanding", "price.trading_value", "price.adj_close",
    "financial.revenue", "financial.gross_profit", "financial.operating_income",
    "financial.net_income", "financial.operating_cash_flow", "financial.total_assets",
    "financial.total_liabilities", "financial.book_equity",
    "consensus.forward_eps", "consensus.forward_sales", "consensus.eps_dispersion",
    "consensus.target_price", "consensus.recommendation", "consensus.analyst_count",
    "event.dividend_per_share", "event.buyback_amount", "event.insider_net_buy",
)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_workbench_root(tmp_path_factory.mktemp("wb") / "equity")


@pytest.fixture(scope="module")
def adapter(root: Path) -> EquityDuckdbAdapter:
    return EquityDuckdbAdapter(root)


def _raw(
    adapter: EquityDuckdbAdapter,
    *,
    start: date = START,
    end: date = END,
    fields: tuple[str, ...] = PRICE_FIELDS,
    history: int = 0,
    universe: str = "krx.common-stock",
):
    return adapter.load_raw_observations(
        RawObservationQuery("KRX", universe, start, end, fields, history)
    )


def _field(result, as_of: date, security_id: str, field_id: str) -> object:
    observation = next(
        o for o in result.observations if (o.as_of, o.security_id) == (as_of, security_id)
    )
    return next(f.value for f in observation.fields if f.field_id == field_id)


# ── 스냅샷·필드 카탈로그 ──────────────────────────────────────────────────────


def test_snapshot_is_the_manifest_hash_and_names_every_table(
    adapter: EquityDuckdbAdapter, root: Path
) -> None:
    snapshot = adapter.snapshot()
    assert snapshot.snapshot_id == snapshot_id(table_builds(root))
    assert snapshot.schema_version == "equity-v1.2" and snapshot.point_in_time
    assert snapshot.source == f"equity_duckdb:{root.resolve()}"
    assert {r.dataset_id for r in snapshot.dataset_revisions} == set(table_builds(root))
    assert all(r.as_of == WB_SESSIONS[-1] for r in snapshot.dataset_revisions)


def test_list_fields_serves_every_declared_field_whose_source_is_built(
    adapter: EquityDuckdbAdapter,
) -> None:
    profiles = {p.field_id: p for p in adapter.list_fields()}
    assert set(profiles) == set(ALL_FIELDS)
    assert all(p.recommended_lag_sessions == 0 for p in profiles.values())
    assert all(p.coverage.venues == ("XKRX",) for p in profiles.values())
    assert all(p.coverage.point_in_time for p in profiles.values())  # adj_close 도 전방 조정
    assert profiles["price.close"].coverage.estimated_coverage_pct == 100.0
    assert profiles["price.market_cap"].coverage.estimated_coverage_pct < 100.0  # 035420 NULL
    # dataset_id 는 FIELD_MAP §2 의 equity 산출 자리다
    assert profiles["financial.book_equity"].dataset_id == "fin_std"
    assert profiles["consensus.target_price"].dataset_id == "opinion_daily"
    assert profiles["event.insider_net_buy"].dataset_id == "holder_daily"
    # LATEST 원천의 커버 시작은 첫 공개일이고, 그 전 세션에는 셀이 없다
    assert profiles["financial.book_equity"].coverage.starts_on == WB_SESSIONS[0]
    assert profiles["event.buyback_amount"].coverage.starts_on == date(2023, 12, 27)
    # 판정(지원/부분)은 프로필 설명 앞에 붙어 소비자에게 그대로 보인다
    assert profiles["financial.revenue"].description.startswith("[부분]")
    assert profiles["financial.net_income"].description.startswith("[지원]")


def test_field_specs_cover_every_field_map_id_exactly_once() -> None:
    """FIELD_MAP §2 의 42 = 어댑터가 내는 23 + 사유가 적힌 19. 겹치거나 빠지면 안 된다."""
    declared = {spec.field_id for spec in FIELD_SPECS} - {"price.adj_close"}
    assert declared & set(UNSUPPORTED_FIELDS) == set()
    assert len(declared) == 23 and len(UNSUPPORTED_FIELDS) == 19
    assert len(declared | set(UNSUPPORTED_FIELDS)) == 42
    assert all(reason.strip() for reason in UNSUPPORTED_FIELDS.values())


# ── RawObservationPort ────────────────────────────────────────────────────────


def test_universe_is_policy_driven_and_membership_is_a_daily_fact(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter)
    assert result.ok and result.warnings == ()
    assert {o.security_id for o in result.observations} == {
        "005930:1",
        "000660:1",
        "035420:1",
        "036220:2",  # 재상장 둘째 구간만 창 안에 있다 — 첫 구간 id 는 나오지 않는다
    }
    halted = next(
        o for o in result.observations if (o.as_of, o.security_id) == (WB_HALT_DATE, "000660:1")
    )
    assert halted.universe_member is False  # status='suspended' → 정책 술어 거짓
    assert _field(result, WB_HALT_DATE, "000660:1", "price.close") == wb_close(
        "000660", WB_HALT_DATE
    )  # 기준가 행도 종가는 실린다
    assert all(o.sector_id is None and o.previous_weight == 0.0 for o in result.observations)


def test_krx_all_keeps_preferred_and_etf_as_members(adapter: EquityDuckdbAdapter) -> None:
    result = _raw(adapter, universe="krx.all")
    ids = {o.security_id for o in result.observations}
    assert {"005935:1", "069500:1"} <= ids
    assert all(o.universe_member for o in result.observations)


def test_adj_close_is_raw_close_scaled_by_factors_applied_on_or_before_the_row(
    adapter: EquityDuckdbAdapter,
) -> None:
    """전방 조정: 분할 전 행은 원주가 그대로, 분할일부터 × share_factor(2). 값은 창에 무관하다."""
    before = date(2024, 1, 5)
    result = _raw(adapter, history=1)
    assert _field(result, before, "000660:1", "price.close") == 103_500.0
    assert _field(result, before, "000660:1", "price.adj_close") == 103_500.0  # 첫 관측 수준 고정
    assert _field(result, WB_SPLIT_DATE, "000660:1", "price.close") == 52_000.0
    assert _field(result, WB_SPLIT_DATE, "000660:1", "price.adj_close") == 104_000.0  # × 2
    assert _field(result, END, "000660:1", "price.adj_close") == 2 * wb_close("000660", END)
    assert _field(result, before, "005930:1", "price.adj_close") == 73_500.0  # not-ok 행 무시
    # 창 독립성(부정 검사 c): 분할 전에 끝나는 창과 분할을 지나는 창에서 같은 셀은 같은 값이다 —
    # base = 창 end 였을 때는 103,500 / 51,750 으로 갈렸다
    early = _raw(adapter, start=date(2024, 1, 2), end=before)
    assert _field(early, before, "000660:1", "price.adj_close") == 103_500.0
    late = _raw(adapter, start=date(2024, 1, 2), end=END)
    assert _field(late, before, "000660:1", "price.adj_close") == 103_500.0
    assert _field(late, WB_SPLIT_DATE, "000660:1", "price.adj_close") == 104_000.0
    # 공개일 = greatest(원주가 공개일, 접힌 계수 공개일) = 세션 (계수 available = apply = 01-08)
    split = next(
        o for o in result.observations if (o.as_of, o.security_id) == (WB_SPLIT_DATE, "000660:1")
    )
    assert {f.available_date for f in split.fields} == {WB_SPLIT_DATE}


def test_missing_market_cap_is_a_none_value_not_an_omission(adapter: EquityDuckdbAdapter) -> None:
    result = _raw(adapter)
    assert _field(result, START, "035420:1", "price.market_cap") is None
    assert _field(result, START, "005930:1", "price.market_cap") == wb_close(
        "005930", START
    ) * 5_969_782_550


def test_unavailable_field_is_a_failure_value_naming_the_supported_set(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, fields=("price.close", "classification.sector"))
    assert result.status is DataLoadStatus.INVALID_QUERY and result.observations == ()
    assert result.detail is not None
    assert "unavailable" in result.detail and "classification.sector" in result.detail
    assert "현재값 라벨" in result.detail  # 사유를 그대로 붙인다
    assert "price.adj_close" in result.detail  # supported 목록
    # S08~S10 격자는 "원천 없음" 이 아니라 "아직 병합되지 않은 테이블" 이라고 말해야 한다
    flow = _raw(adapter, fields=("flow.foreign_net_buy",))
    assert flow.detail is not None and "equity/s08-s10" in flow.detail


def test_queries_outside_calendar_coverage_are_no_data(adapter: EquityDuckdbAdapter) -> None:
    beyond = _raw(adapter, start=START, end=date(2024, 1, 15))
    assert beyond.status is DataLoadStatus.NO_DATA
    assert beyond.detail is not None and "outside coverage" in beyond.detail
    before = _raw(adapter, start=date(2023, 12, 1), end=START)
    assert before.status is DataLoadStatus.NO_DATA


def test_history_is_truncated_at_calendar_start_with_a_warning(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2023, 12, 27), end=date(2023, 12, 28), history=5)
    assert result.ok
    assert result.history_sessions == (WB_SESSIONS[0],)
    assert any("insufficient calendar for warm-up history" in w for w in result.warnings)


def _cell(result, as_of: date, security_id: str, field_id: str):
    observation = next(
        o for o in result.observations if (o.as_of, o.security_id) == (as_of, security_id)
    )
    return next(f for f in observation.fields if f.field_id == field_id)


def _has(result, as_of: date, security_id: str, field_id: str) -> bool:
    observation = next(
        o for o in result.observations if (o.as_of, o.security_id) == (as_of, security_id)
    )
    return any(f.field_id == field_id for f in observation.fields)


def test_price_row_fields_come_from_the_krx_ledger_row(adapter: EquityDuckdbAdapter) -> None:
    """`price.*` 6 은 같은 (ticker, session) 원장 행이고 공개일은 그 세션이다."""
    result = _raw(adapter, fields=ALL_FIELDS)
    close = wb_close("005930", START)
    assert _field(result, START, "005930:1", "price.close") == close
    assert _field(result, START, "005930:1", "price.open") == close - 100
    assert _field(result, START, "005930:1", "price.volume") == 1_000
    assert _field(result, START, "005930:1", "price.trading_value") == close * 1_000
    assert _field(result, START, "005930:1", "price.shares_outstanding") == 5_969_782_550
    assert _cell(result, START, "005930:1", "price.open").available_date == START
    # 정지일(기준가 행)은 OHLC 가 NULL 이라 값이 아니라 MISSING 이다 — 0 으로 접지 않는다
    halted = _cell(result, WB_HALT_DATE, "000660:1", "price.open")
    assert (halted.value, halted.kind) == (None, CellKind.MISSING)
    assert _field(result, WB_HALT_DATE, "000660:1", "price.volume") == 0  # 실제 0 은 관측이다
    # 035420 은 상장주식수 원장이 없어 시총·주식수가 결측이다(합성하지 않는다)
    assert _field(result, START, "035420:1", "price.shares_outstanding") is None


def test_financials_are_the_latest_filing_and_every_share_class_shares_them(
    adapter: EquityDuckdbAdapter,
) -> None:
    """법인 축 재무는 `corp_ticker` 로 전개된다 — 005930 과 우선주 005935 가 같은 값이다."""
    result = _raw(adapter, start=date(2024, 1, 3), end=END, fields=ALL_FIELDS, universe="krx.all")
    # 01-03 에는 3분기 보고서(2023-11-14 공개)가 최신, 01-04 부터 사업보고서(01-04 공개)
    early = _cell(result, date(2024, 1, 3), "005930:1", "financial.revenue")
    assert (early.value, early.available_date) == (120.0, date(2023, 11, 14))
    late = _cell(result, date(2024, 1, 4), "005930:1", "financial.revenue")
    assert (late.value, late.available_date) == (460.0, date(2024, 1, 4))
    assert _field(result, START, "005930:1", "financial.book_equity") == 615.0
    assert _field(result, START, "005935:1", "financial.book_equity") == 615.0  # 같은 법인
    assert _field(result, START, "005930:1", "financial.operating_cash_flow") == 150.0
    # 같은 grain 의 CFS·OFS 중 v_fin_latest 가 CFS 를 고른다(OFS 는 9,999 로 깔아 뒀다)
    assert _field(result, START, "000660:1", "financial.book_equity") == 1_200.0
    # 값이 없는 계정은 셀이 나가되 MISSING 이고, 있는 계정은 OBSERVED 다(같은 행에서 갈린다)
    missing = _cell(result, date(2024, 1, 9), "036220:2", "financial.revenue")
    assert (missing.value, missing.kind) == (None, CellKind.MISSING)
    assert _field(result, date(2024, 1, 9), "036220:2", "financial.net_income") == 7.0
    # 재무 원천이 없는 종목(ETF)은 셀 자체가 없다 — mock 값으로 채우지 않는다
    assert not _has(result, START, "069500:1", "financial.revenue")
    # 창 독립: 같은 셀은 창을 좁혀도 같다(as-of 값은 (security, 컷오프) 의 함수다)
    narrow = _raw(adapter, start=START, end=START, fields=("financial.revenue",))
    assert _field(narrow, START, "005930:1", "financial.revenue") == _field(
        result, START, "005930:1", "financial.revenue"
    )


def test_consensus_picks_the_nearest_target_period_and_the_measured_source(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2024, 1, 4), end=END, fields=ALL_FIELDS)
    # 2023-12 관측점의 FY1 = 202312(5,000원) · 범위 = 6,000 − 4,000
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.forward_eps") == 5_000.0
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.eps_dispersion") == 2_000.0
    # 2024-01 관측점(01-05 공개)이 오면 FY1 이 202412 로 넘어간다
    later = _cell(result, date(2024, 1, 5), "005930:1", "consensus.forward_eps")
    assert (later.value, later.available_date) == (6_500.0, date(2024, 1, 5))
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.eps_dispersion") == 1_200.0
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.forward_sales") == 3_000_000.0
    # 같은 (ticker, obs_date) 의 v3·wise 중 잰 판본(wise, 95,000)이 이긴다
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.target_price") == 95_000.0
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.recommendation") == 4.1
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.analyst_count") == 28.0
    # wise 가 없는 날은 v3 를 쓴다(coverage_degraded — 프로필이 그렇게 말한다)
    assert _field(result, date(2024, 1, 9), "005930:1", "consensus.target_price") == 97_000.0


def test_event_fields_are_the_latest_filing_with_its_publication_date(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2024, 1, 4), end=END, fields=ALL_FIELDS, universe="krx.all")
    # 자사주 취득 결정 2건이 같은 공시일에 있으면 합한다(다른 event_type 은 섞이지 않는다)
    buyback = _cell(result, date(2024, 1, 5), "005930:1", "event.buyback_amount")
    assert (buyback.value, buyback.available_date) == (1_500_000.0, date(2024, 1, 5))
    assert not _has(result, date(2024, 1, 4), "005930:1", "event.buyback_amount")
    # 임원 지분 증감은 같은 접수일의 보고자를 합하고(1,000 − 400) majorstock 은 빼놓는다
    insider = _cell(result, date(2024, 1, 9), "005930:1", "event.insider_net_buy")
    assert (insider.value, insider.available_date) == (600.0, date(2024, 1, 9))
    assert not _has(result, START, "005930:1", "event.insider_net_buy")
    # 보고가 값 없이 하나뿐이면 합도 결측이다(0 으로 접지 않는다)
    empty = _cell(result, date(2024, 1, 9), "000660:1", "event.insider_net_buy")
    assert (empty.value, empty.kind) == (None, CellKind.MISSING)
    # 배당은 종류 축을 접어 보통주 값이 우선주 티커에도 간다(FIELD_MAP 부분 판정 ②)
    old = _cell(result, START, "005930:1", "event.dividend_per_share")
    assert (old.value, old.available_date) == (361.0, date(2023, 3, 7))
    assert _field(result, START, "005935:1", "event.dividend_per_share") == 361.0
    assert _field(result, date(2024, 1, 9), "005930:1", "event.dividend_per_share") == 400.0


def test_latest_fields_never_show_a_filing_before_its_available_date(
    adapter: EquityDuckdbAdapter,
) -> None:
    """PIT — 공개일이 as_of 보다 늦은 판본은 보이지 않고, 랙은 컷오프를 세션 단위로 물린다."""
    result = _raw(adapter, start=WB_SESSIONS[0], end=END, fields=ALL_FIELDS, universe="krx.all")
    for observation in result.observations:
        for cell in observation.fields:
            assert cell.available_date <= observation.as_of, (observation.as_of, cell)
    panel = adapter.load_panel(
        ResearchPanelQuery(
            start=date(2024, 1, 9),
            end=date(2024, 1, 9),
            security_ids=("005930:1",),
            field_ids=("financial.revenue",),
            lag_overrides=(FieldLag("financial.revenue", 4),),
        )
    )
    assert panel.ok
    # 01-09 에서 4세션 전 = 01-03 → 사업보고서(01-04 공개)는 아직 보이지 않는다
    (cell,) = panel.cells
    assert (cell.value, cell.available_date) == (120.0, date(2023, 11, 14))
    assert cell.source_effective_date == date(2023, 9, 30)  # 내용일 = 기간 말일


# ── EquityDataPort ────────────────────────────────────────────────────────────


def test_panel_lag_override_shifts_the_row_and_its_available_date(
    adapter: EquityDuckdbAdapter,
) -> None:
    panel = adapter.load_panel(
        ResearchPanelQuery(
            start=START,
            end=END,
            security_ids=("000660:1", "036220:2"),
            field_ids=("price.close", "price.adj_close"),
            lag_overrides=(FieldLag("price.close", 1),),
        )
    )
    assert panel.ok
    cells = {(c.as_of, c.security_id, c.field_id): c for c in panel.cells}
    lagged = cells[(date(2024, 1, 9), "000660:1", "price.close")]
    assert lagged.value == wb_close("000660", START) and lagged.available_date == START
    assert lagged.kind is CellKind.OBSERVED
    # 036220 둘째 구간 첫날은 전 세션 행이 없어 랙 1 값이 나오지 않는다(합성 금지)
    assert (START, "036220:2", "price.close") not in cells
    assert (START, "036220:2", "price.adj_close") in cells


def test_panel_rejects_unknown_or_malformed_security_ids(adapter: EquityDuckdbAdapter) -> None:
    unknown = adapter.load_panel(
        ResearchPanelQuery(START, END, ("000660:9",), ("price.close",))
    )
    assert unknown.status is DataLoadStatus.INVALID_QUERY
    assert unknown.detail is not None and "000660:9" in unknown.detail
    malformed = adapter.load_panel(ResearchPanelQuery(START, END, ("000660",), ("price.close",)))
    assert malformed.status is DataLoadStatus.INVALID_QUERY


def test_load_universe_is_policy_free_and_names_securities(adapter: EquityDuckdbAdapter) -> None:
    result = adapter.load_universe(UniverseHistoryQuery("XKRX", START, date(2024, 1, 9)))
    assert result.ok and [p.session for p in result.points] == [START, date(2024, 1, 9)]
    members = {m.security_id: m for m in result.points[0].members}
    assert {"005935:1", "069500:1", "036220:2"} <= set(members)
    assert members["005930:1"].name == "삼성전자" and members["005930:1"].venue == "XKRX"
    other = adapter.load_universe(UniverseHistoryQuery("XNYS", START, END))
    assert other.status is DataLoadStatus.INVALID_QUERY


# ── FactorMetadataPort · FactorObservationPort ────────────────────────────────


def test_factor_metadata_and_observations_come_from_the_same_panel(
    adapter: EquityDuckdbAdapter,
) -> None:
    metadata = adapter.resolve_factor_fields(("price.adj_close", "flow.foreign_net_buy"))
    assert [f.field_id for f in metadata.fields] == ["price.adj_close"]
    assert metadata.data_snapshot_id == adapter.snapshot().snapshot_id
    observations = adapter.load_factor_observations(
        FactorObservationQuery(("price.adj_close",), START, END, minimum_history_sessions=2)
    )
    assert observations.data_snapshot_id == metadata.data_snapshot_id
    dates = {o.as_of for o in observations.observations}
    assert min(dates) == date(2024, 1, 5) and max(dates) == END
    assert all(o.forward_return is None for o in observations.observations)
    assert any(not o.universe_member for o in observations.observations)  # 000660 정지일


# ── BacktestDataPort ──────────────────────────────────────────────────────────


def test_backtest_dataset_drops_reference_rows_and_carries_ok_actions_only(
    adapter: EquityDuckdbAdapter,
) -> None:
    dataset = adapter.load_backtest_dataset(
        BacktestDataQuery(WB_SESSIONS[0], END, ("000660:1", "005930:1", "036220:2"), None)
    )
    assert dataset.data_snapshot_id == adapter.snapshot().snapshot_id
    hynix = [b for b in dataset.bars if b.security_id == "000660:1"]
    assert len(hynix) == len(WB_SESSIONS) - 1 and WB_HALT_DATE not in {b.session for b in hynix}
    assert {b.session for b in dataset.bars if b.security_id == "036220:2"} == set(
        WB_SESSIONS[8:]
    )
    assert dataset.corporate_actions == (
        replace(
            dataset.corporate_actions[0],
            session=WB_SPLIT_DATE,
            security_id="000660:1",
            action_type="split",
            ratio="2.0",
            detail="000660:split:2024-01-08",
        ),
    )
    assert {m.security_id: (m.first_session, m.last_session) for m in dataset.memberships}[
        "036220:2"
    ] == (WB_SPLIT_DATE, END)
    assert [w.code for w in dataset.warnings] == ["equity.reference_rows_dropped"]


def test_backtest_dataset_refuses_unknown_and_index_ids(adapter: EquityDuckdbAdapter) -> None:
    with pytest.raises(ValueError, match="unknown security_id"):
        adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("000660:9",), None))
    with pytest.raises(ValueError, match="malformed security_id"):
        adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("000660:1",), "idx:코스피"))


# ── 카탈로그·환경 실패 ────────────────────────────────────────────────────────


def test_missing_or_stale_catalog_makes_adj_close_unavailable(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity", catalog=False)
    without = EquityDuckdbAdapter(root)
    # 매크로가 없으면 그 매크로를 읽는 원천의 필드가 전부 빠진다 — 테이블 원천은 남는다
    served = {p.field_id for p in without.list_fields()}
    assert "price.close" in served and "consensus.target_price" in served
    assert not served & {"price.adj_close", "financial.book_equity", "consensus.forward_eps"}
    denied = _raw(without, fields=("price.adj_close",))
    assert denied.status is DataLoadStatus.INVALID_QUERY
    assert denied.detail is not None and "catalog file missing" in denied.detail
    assert _raw(without, fields=("price.close",)).ok  # 나머지 필드는 카탈로그 없이도 답한다

    write_catalog(root, snapshot="deadbeefdeadbeef")
    stale = EquityDuckdbAdapter(root)
    result = _raw(stale, fields=("price.adj_close",))
    assert result.status is DataLoadStatus.INVALID_QUERY
    assert result.detail is not None and "catalog is stale" in result.detail
    # snapshot_id 는 meta 가 아니라 MANIFEST 에서 온다
    assert stale.snapshot().snapshot_id == snapshot_id(table_builds(root))

    write_catalog(root, with_macros=False)
    skipped = EquityDuckdbAdapter(root)
    result = _raw(skipped, fields=("price.adj_close",))
    assert result.detail is not None and "macros_skipped" in result.detail


def test_missing_required_table_fails_at_construction(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity")
    (root / "universe_policy" / "MANIFEST.json").unlink()
    with pytest.raises(EquityDuckdbSetupError, match="universe_policy"):
        EquityDuckdbAdapter(root)
    with pytest.raises(EquityDuckdbSetupError, match="not a directory"):
        EquityDuckdbAdapter(tmp_path / "nowhere")


# ── 부팅 · 파이프라인 ─────────────────────────────────────────────────────────


def test_container_boots_with_the_duckdb_adapter(root: Path, tmp_path: Path) -> None:
    container = build_container(
        equity_adapter="duckdb", equity_root=root, artifact_root=tmp_path / "runs"
    )
    assert isinstance(container.equity_data, EquityDuckdbAdapter)
    catalog = container.equity_workspace.catalog()
    assert catalog.snapshot.schema_version == "equity-v1.2"
    # 카탈로그는 페이지 단위라 한 쪽에 다 담기지 않는다 — 총 개수와 첫 쪽의 소속만 본다
    assert catalog.total == len(ALL_FIELDS)
    assert {p.field_id for p in catalog.fields} <= set(ALL_FIELDS)
    assert "fin_std" in catalog.facets.dataset_ids


def _momentum_spec(field_id: str) -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: END
    ).template()
    return replace(
        template,
        data=DataStep(market=Market.KRX, start=START, end=END, universe_id="krx.common-stock"),
        factors=FactorStep(
            factors=(
                FactorSignal(
                    factor_id="mom_3",
                    label="3세션 모멘텀",
                    direction=FactorDirection.HIGH,
                    weight=1.0,
                    graph=FactorGraph(
                        nodes=(
                            FieldNode("px", field_id, "field"),
                            TimeSeriesNode(
                                "mom", TimeSeriesOperator.MOMENTUM, "px", 3, "time_series"
                            ),
                        ),
                        output_node_id="mom",
                    ),
                ),
            )
        ),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def test_truthful_pipeline_momentum_across_a_split_is_continuous_on_adj_close(
    adapter: EquityDuckdbAdapter,
) -> None:
    """결정 6 의 근거: 원주가 모멘텀은 분할일에 −50% 를 찍고, 조정가 모멘텀은 연속이다."""
    registry = build_default_factor_registry()
    service = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version=registry.version,
    )

    def momentum(field_id: str) -> float:
        result = service.run_pipeline(PortfolioPreviewRequest(_momentum_spec(field_id)))
        assert result.data_snapshot_id == adapter.snapshot().snapshot_id
        assert result.preview.tape.frames
        values = result.factor_evaluations[0].values
        value = next(
            v.value for v in values if (v.as_of, v.security_id) == (WB_SPLIT_DATE, "000660:1")
        )
        assert value is not None
        return value

    assert momentum("price.adj_close") == pytest.approx(104_000 / 103_000 - 1)  # 전방 조정
    assert momentum("price.close") == pytest.approx(52_000 / 103_000 - 1)
