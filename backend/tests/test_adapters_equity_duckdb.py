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
ALL_FIELDS = ("price.close", "price.adj_close", "price.market_cap")


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
    fields: tuple[str, ...] = ALL_FIELDS,
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


def test_list_fields_serves_the_three_price_fields_only(adapter: EquityDuckdbAdapter) -> None:
    profiles = {p.field_id: p for p in adapter.list_fields()}
    assert set(profiles) == set(ALL_FIELDS)
    assert all(p.recommended_lag_sessions == 0 for p in profiles.values())
    assert all(p.coverage.venues == ("XKRX",) for p in profiles.values())
    assert profiles["price.close"].coverage.point_in_time
    assert not profiles["price.adj_close"].coverage.point_in_time  # 수준값은 스냅샷 vintage
    assert profiles["price.close"].coverage.estimated_coverage_pct == 100.0
    assert profiles["price.market_cap"].coverage.estimated_coverage_pct < 100.0  # 035420 NULL


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


def test_adj_close_is_raw_close_scaled_by_factors_applied_after_the_row(
    adapter: EquityDuckdbAdapter,
) -> None:
    before = date(2024, 1, 5)
    result = _raw(adapter, history=1)
    assert _field(result, before, "000660:1", "price.close") == 103_500.0
    assert _field(result, before, "000660:1", "price.adj_close") == 51_750.0  # × 1/2
    assert _field(result, WB_SPLIT_DATE, "000660:1", "price.adj_close") == 52_000.0  # base
    assert _field(result, before, "005930:1", "price.adj_close") == 73_500.0  # not-ok 행 무시
    # base = 질의 end: 분할 전에 끝나는 창에서는 계수가 아직 없다(모듈 docstring 의 절충)
    early = _raw(adapter, start=date(2024, 1, 2), end=before)
    assert _field(early, before, "000660:1", "price.adj_close") == 103_500.0


def test_missing_market_cap_is_a_none_value_not_an_omission(adapter: EquityDuckdbAdapter) -> None:
    result = _raw(adapter)
    assert _field(result, START, "035420:1", "price.market_cap") is None
    assert _field(result, START, "005930:1", "price.market_cap") == wb_close(
        "005930", START
    ) * 5_969_782_550


def test_unavailable_field_is_a_failure_value_naming_the_supported_set(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, fields=("price.close", "financial.book_equity"))
    assert result.status is DataLoadStatus.INVALID_QUERY and result.observations == ()
    assert result.detail is not None
    assert "unavailable" in result.detail and "financial.book_equity" in result.detail
    assert "price.adj_close" in result.detail  # supported 목록


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
    metadata = adapter.resolve_factor_fields(("price.adj_close", "financial.book_equity"))
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
    assert {p.field_id for p in without.list_fields()} == {"price.close", "price.market_cap"}
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
    assert {p.field_id for p in catalog.fields} == set(ALL_FIELDS)


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

    assert momentum("price.adj_close") == pytest.approx(52_000 / 51_500 - 1)
    assert momentum("price.close") == pytest.approx(52_000 / 103_000 - 1)
