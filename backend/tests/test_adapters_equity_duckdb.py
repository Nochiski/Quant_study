"""워크벤치 equity 어댑터(`adapters/outbound/equity_duckdb`, S21 축소) — 손 픽스처 위 동작 검증.

픽스처는 `tests/equity_fixture.build_workbench_root`(캘린더 13세션, 000660 2:1 분할·정지일, 재상장
036220, 우선주·ETF, 정책표 2개). 산출물 parquet 에 의존하지 않는다(`.claude/rules/testing.md`).
어댑터 모듈은 duckdb 를 지연 import 하므로 여기 import 는 안전하고, 실제 사용은 importorskip 뒤다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Condition, Event
from typing import cast

import pytest
from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_duckdb._adapter import (
    _FILL_KIND_TO_CELL,
    _LOCK_CONFLICT_MARKERS,
    _PARQUET_LIST,
    EVENT_TYPE_MAP,
    _fetchall,
    _open,
    _parquet_table,
)
from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_SPECS,
    SOURCE_SPECS,
    UNSUPPORTED_FIELDS,
    SourceMode,
    _reject_unread_declarations,
)
from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
    EquityDuckdbAdapter,
    EquityDuckdbSetupError,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataNotReadyError,
    BacktestDataQuery,
    CorporateActionRecord,
)
from strategy_workbench.application.factor_research.facade.ports import FactorObservationQuery
from strategy_workbench.application.factor_research.facade.research import (
    FactorPreviewRequest,
    FactorResearchService,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import WarningSeverity
from strategy_workbench.domain.equity.facade.research_data import (
    SNAPSHOT_CONTRACT_SEPARATOR,
    CellKind,
    DataLoadStatus,
    FieldLag,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorGraph,
    FactorSignal,
    FieldNode,
    NodeValueType,
    RebalanceFrequency,
    StrategySpec,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from tests.equity_fixture import (
    WB_BONUS_EX,
    WB_EVENING_SESSION,
    WB_FIN_ROWS,
    WB_HALT_DATE,
    WB_INCONSISTENT,
    WB_LATE_FACTOR,
    WB_PROFILE_LAG_THREE,
    WB_PROFILE_LAG_ZERO,
    WB_PROFILE_ROWS,
    WB_SESSIONS,
    WB_SPLIT_DATE,
    build_workbench_root,
    fin_std_table,
    snapshot_id,
    table_builds,
    wb_close,
    write_catalog,
    write_equity_table,
)

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

START, END = date(2024, 1, 8), date(2024, 1, 12)
PRICE_FIELDS = ("price.close", "price.adj_close", "price.market_cap")
# 손 픽스처가 원천을 다 갖췄을 때 어댑터가 내는 field_id — FIELD_MAP §2 의 42 중 29 +
# equity 내부 스코프 `price.adj_close`. 나머지 13 의 사유는 `_specs.UNSUPPORTED_FIELDS` 다.
ALL_FIELDS = (
    "price.close",
    "price.open",
    "price.volume",
    "price.market_cap",
    "price.shares_outstanding",
    "price.trading_value",
    "price.adj_close",
    "financial.revenue",
    "financial.gross_profit",
    "financial.operating_income",
    "financial.net_income",
    "financial.operating_cash_flow",
    "financial.total_assets",
    "financial.total_liabilities",
    "financial.book_equity",
    "consensus.forward_eps",
    "consensus.forward_sales",
    "consensus.eps_dispersion",
    "consensus.target_price",
    "consensus.recommendation",
    "consensus.analyst_count",
    "flow.foreign_net_buy",
    "flow.institution_net_buy",
    "flow.retail_net_buy",
    "short.short_sale_value",
    "short.borrowed_quantity",
    "credit.margin_balance",
    "event.dividend_per_share",
    "event.buyback_amount",
    "event.insider_net_buy",
)

# 경고 문장이 한글로 완성됐는지 보는 표지(SoT 경고 문장 행, 이슈 #229).
_HANGUL = re.compile("[가-힣]")


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
    # 앞부분은 원장 스냅샷(테이블 build 해시), 뒷부분은 필드 계약 판이다(#235).
    ledger, separator, contract = snapshot.snapshot_id.partition(SNAPSHOT_CONTRACT_SEPARATOR)
    assert (ledger, separator) == (snapshot_id(table_builds(root)), SNAPSHOT_CONTRACT_SEPARATOR)
    assert re.fullmatch(r"[0-9a-f]{16}", contract)
    assert snapshot.schema_version == "equity-v1.2" and snapshot.point_in_time
    # 계약 패널이 그대로 보여 주는 값이라 루트 절대 경로를 싣지 않는다(#163)
    assert snapshot.source == "equity_duckdb"
    assert {r.dataset_id for r in snapshot.dataset_revisions} == set(table_builds(root))
    assert all(r.as_of == WB_SESSIONS[-1] for r in snapshot.dataset_revisions)


def test_field_contract_splits_the_snapshot_but_not_the_ledger_or_the_root_path(
    tmp_path: Path,
) -> None:
    """이슈 #235: 같은 원장 빌드라도 필드를 읽는 규칙이 바뀌면 데이터 스냅샷 id 가 갈린다.

    카탈로그 매크로 본문(여기서는 `period_frontier` 가 없는 옛 재무 뷰)이 바뀌면 원장 판은
    그대로이고 필드 계약 판만 바뀐다. 매크로가 싣는 parquet 절대경로에는 흔들리지 않는다 —
    같은 원장·코드를 다른 폴더에 두어도 같은 id 다. 팩터 행렬 캐시 키는 이 id 를 받아 옛
    의미의 값을 새 의미로 재사용하지 않는다.
    """
    here = build_workbench_root(tmp_path / "here" / "equity")
    there = build_workbench_root(tmp_path / "there" / "equity")
    ledger = snapshot_id(table_builds(here)) + SNAPSHOT_CONTRACT_SEPARATOR

    current = EquityDuckdbAdapter(here)
    assert current.snapshot().snapshot_id.startswith(ledger)
    assert EquityDuckdbAdapter(there).snapshot().snapshot_id == current.snapshot().snapshot_id

    write_catalog(there, legacy_fin_columns=("period_frontier",))
    older = EquityDuckdbAdapter(there)
    assert older.snapshot().snapshot_id.startswith(ledger)
    assert older.snapshot().snapshot_id != current.snapshot().snapshot_id

    request = FactorPreviewRequest(
        FactorGraph(nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close"),
        START,
        END,
    )
    registry = build_default_factor_registry()
    keys = [
        FactorResearchService(registry, adapter, adapter).preview(request).cache_key
        for adapter in (current, older)
    ]
    assert [key.data_snapshot_id for key in keys] == [
        current.snapshot().snapshot_id,
        older.snapshot().snapshot_id,
    ]
    assert keys[0].fingerprint != keys[1].fingerprint


_ADAPTER = "strategy_workbench.adapters.outbound.equity_duckdb._adapter"


def _edit_close(monkeypatch: pytest.MonkeyPatch, **changes: object) -> None:
    edited = tuple(
        replace(spec, **changes) if spec.field_id == "price.close" else spec for spec in FIELD_SPECS
    )
    monkeypatch.setattr(f"{_ADAPTER}.FIELD_SPECS", edited)


def _edit_fin(monkeypatch: pytest.MonkeyPatch, **changes: object) -> None:
    edited = tuple(
        replace(spec, **changes) if spec.name == "fin" else spec for spec in SOURCE_SPECS
    )
    monkeypatch.setattr(f"{_ADAPTER}.SOURCE_SPECS", edited)


def _edit_signature_default(root: Path) -> None:
    meta_path = root / "_catalog_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["macros"] = [
        signature.replace("vintage := 'restated'", "vintage := 'pit'")
        for signature in meta["macros"]
    ]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _reverse_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_ADAPTER}.FIELD_SPECS", FIELD_SPECS[::-1])
    monkeypatch.setattr(f"{_ADAPTER}.SOURCE_SPECS", SOURCE_SPECS[::-1])


@pytest.mark.parametrize(
    ("edit", "moves"),
    [
        pytest.param(lambda mp, root: _edit_close(mp, expr="(close) * 1"), True, id="field-expr"),
        pytest.param(
            lambda mp, root: _edit_fin(mp, row_filter="period_frontier AND TRUE"),
            True,
            id="source-row-filter",
        ),
        pytest.param(
            lambda mp, root: _edit_signature_default(root), True, id="macro-signature-default"
        ),
        pytest.param(
            lambda mp, root: mp.setattr(
                f"{_ADAPTER}.EVENT_TYPE_MAP", {**EVENT_TYPE_MAP, "bonus": "reverse_split"}
            ),
            True,
            id="code-table",
        ),
        pytest.param(
            lambda mp, root: mp.setattr(
                f"{_ADAPTER}._FILL_KIND_TO_CELL",
                {**_FILL_KIND_TO_CELL, "not_collected": CellKind.MISSING},
            ),
            True,
            id="cell-kinds",
        ),
        pytest.param(
            lambda mp, root: mp.setattr(f"{_ADAPTER}.RATIO_DIRECTED_EVENT_TYPES", frozenset()),
            True,
            id="ratio-directed",
        ),
        pytest.param(
            lambda mp, root: _edit_close(
                mp,
                label="종가 ",
                description="문장만",
                evidence="-",
                verdict="-",
                disclosure_basis="-",
                lag_basis="-",
            ),
            False,
            id="field-prose",
        ),
        pytest.param(lambda mp, root: _edit_fin(mp, lag_basis="문장만"), False, id="source-prose"),
        pytest.param(lambda mp, root: _reverse_declarations(mp), False, id="declaration-order"),
    ],
)
def test_the_field_contract_follows_meaning_not_prose_or_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    edit: Callable[[pytest.MonkeyPatch, Path], object],
    moves: bool,
) -> None:
    """이슈 #235·#291 리뷰: 뜻 칸이 바뀌면 계약 판이 바뀌고, 문장·선언 순서만 바뀌면 그대로다.

    뜻 칸은 선언표의 식·원천의 고르는 규칙(#225 의 `period_frontier` 같은)·매크로 기본값(meta
    시그니처)·코드의 대응표다. 어느 경우든 원장 판(앞부분)은 그대로다.
    """
    root = build_workbench_root(tmp_path / "equity")
    before = EquityDuckdbAdapter(root).snapshot().snapshot_id
    edit(monkeypatch, root)
    after = EquityDuckdbAdapter(root).snapshot().snapshot_id

    ledger = before.partition(SNAPSHOT_CONTRACT_SEPARATOR)[0]
    assert after.partition(SNAPSHOT_CONTRACT_SEPARATOR)[0] == ledger
    assert (after != before) is moves


_HIVE_OFF = "(hive_partitioning = CAST('f' AS BOOLEAN))"


@pytest.mark.parametrize(
    ("paths", "table"),
    [
        pytest.param(
            [
                "C:\\Users\\a\\equity\\price_daily\\v=b1\\year=2020\\part0.parquet",
                "C:\\Users\\a\\equity\\price_daily\\v=b1\\year=2021\\part0.parquet",
            ],
            "price_daily",
            id="windows",
        ),
        pytest.param(
            ["C:/Users/a/equity/price_daily/v=b1/year=2020/part0.parquet"],
            "price_daily",
            id="windows-slash",
        ),
        pytest.param(
            [
                f"/home/ledger/equity/price_daily/v=b1/year={year}/part0.parquet"
                for year in (2019, 2020, 2021)
            ],
            "price_daily",
            id="posix",
        ),
        pytest.param(
            ["/srv/o''brien/equity/fin_std/v=b2/part0.parquet"], "fin_std", id="quoted-root"
        ),
    ],
)
def test_macro_bodies_fold_parquet_paths_to_the_table_name_on_any_os(
    paths: list[str], table: str
) -> None:
    """이슈 #235·#291 리뷰 P3-1: 매크로 본문의 parquet 절대경로는 OS·루트·파티션 수와 무관하게
    테이블 이름으로 접힌다. 파일 하나면 duckdb 가 목록 없이 문자열로 돌려준다. 값 필터의 문자열
    목록은 규칙이라 그대로 둔다.
    """
    listed = ", ".join(f"'{path}'" for path in paths)
    body = f"read_parquet(main.list_value({listed}), {_HIVE_OFF}) WHERE k IN ('a', 'b')"
    folded = f"read_parquet({table}, {_HIVE_OFF}) WHERE k IN ('a', 'b')"
    assert _PARQUET_LIST.sub(_parquet_table, body) == folded
    single = f"read_parquet('{paths[0]}')"
    assert _PARQUET_LIST.sub(_parquet_table, single) == f"read_parquet({table})"


def test_list_fields_serves_every_declared_field_whose_source_is_built(
    adapter: EquityDuckdbAdapter,
) -> None:
    profiles = {p.field_id: p for p in adapter.list_fields()}
    assert set(profiles) == set(ALL_FIELDS)
    # 랙의 정본은 `dataset_profile` 이다 — 어댑터 상수가 아니라 대장 값이 나와야 한다.
    assert {f: profiles[f].recommended_lag_sessions for f in ALL_FIELDS} == {
        f: (0 if f in WB_PROFILE_LAG_ZERO else 3 if f in WB_PROFILE_LAG_THREE else 1)
        for f in ALL_FIELDS
    }
    assert profiles["price.close"].available_date_basis == "session_close"
    assert profiles["credit.margin_balance"].available_date_basis == "next_session_open"
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
    """FIELD_MAP §2 의 42 = 어댑터가 내는 29 + 사유가 적힌 13. 겹치거나 빠지면 안 된다."""
    declared = {spec.field_id for spec in FIELD_SPECS} - {"price.adj_close"}
    assert declared & set(UNSUPPORTED_FIELDS) == set()
    assert len(declared) == 29 and len(UNSUPPORTED_FIELDS) == 13
    assert len(declared | set(UNSUPPORTED_FIELDS)) == 42
    assert all(reason.strip() for reason in UNSUPPORTED_FIELDS.values())


def test_declarations_their_query_does_not_read_are_rejected() -> None:
    """선언한 식을 그 원천의 질의가 읽지 않으면 선언 때 막는다 — 읽히지 않는 선언은 조용히 무시된다.

    필드 공개일 열은 LATEST PICK 질의만 읽고(#300 리뷰 P3-3), 가림 표시는 격자 질의만 읽는다(#311
    리뷰 P3-3). LATEST 원천의 가림 표시는 결측 정책이 가린 셀을 다시 채우게 둔다.
    """
    _reject_unread_declarations(SOURCE_SPECS, FIELD_SPECS)  # 지금 선언은 통과한다
    latest = next(spec for spec in SOURCE_SPECS if spec.mode is SourceMode.LATEST)
    with pytest.raises(ValueError, match=re.escape(f"sources=['{latest.name}']")):
        _reject_unread_declarations(
            (*SOURCE_SPECS, replace(latest, masked_expr="TRUE")), FIELD_SPECS
        )
    grid = {spec.name for spec in SOURCE_SPECS if spec.mode is SourceMode.GRID}
    gridded = next(spec for spec in FIELD_SPECS if spec.source in grid)
    with pytest.raises(ValueError, match=re.escape(f"fields=['{gridded.field_id}']")):
        _reject_unread_declarations(
            SOURCE_SPECS, (*FIELD_SPECS, replace(gridded, available_expr="available_date"))
        )


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
    # 공개일 = greatest(원주가 공개일, 접힌 계수 공개일) = 세션 (계수 available = apply = 01-08).
    # 랙은 필드마다 `dataset_profile` 값을 따른다 — 가격 축 0세션, 시총 1세션(직전 세션 공개).
    split = next(
        o for o in result.observations if (o.as_of, o.security_id) == (WB_SPLIT_DATE, "000660:1")
    )
    assert {f.field_id: f.available_date for f in split.fields} == {
        "price.close": WB_SPLIT_DATE,
        "price.adj_close": WB_SPLIT_DATE,
        "price.market_cap": before,
    }
    # 원장이 그날 사건을 접지 못한 적용일 행은 원장이 가린 셀(MASKED)이다(#220 원장 뷰
    # `v_adj_close`, #298 셀 종류). 035420 은 01-09 계수가 다음 세션에 공개돼 그날부터
    # 접히고(× 0.5), 01-11 은 계수를 못 낸 기준가 재설정이다. 01-05 의 unknown_price_only(유상
    # 권리락 등)는 가리지 않는다.
    gaps = _raw(adapter, start=date(2024, 1, 5), end=END)
    for session, expected in (
        (date(2024, 1, 5), wb_close("035420", date(2024, 1, 5))),
        (WB_LATE_FACTOR, None),
        (WB_HALT_DATE, 0.5 * wb_close("035420", WB_HALT_DATE)),
        (WB_INCONSISTENT, None),
        (END, 0.5 * wb_close("035420", END)),
    ):
        cell = _cell(gaps, session, "035420:1", "price.adj_close")
        kind = CellKind.OBSERVED if expected is not None else CellKind.MASKED
        assert (cell.value, cell.kind, cell.available_date) == (expected, kind, session), session


def test_missing_market_cap_is_a_none_value_not_an_omission(adapter: EquityDuckdbAdapter) -> None:
    result = _raw(adapter, history=1)
    assert _field(result, START, "035420:1", "price.market_cap") is None
    # 시총은 `dataset_profile` 이 1세션으로 확정한 필드다 — START 세션에는 직전 세션 값이 온다.
    assert (
        _field(result, START, "005930:1", "price.market_cap")
        == wb_close("005930", date(2024, 1, 5)) * 5_969_782_550
    )


def test_unavailable_field_is_a_failure_value_naming_the_supported_set(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, fields=("price.close", "classification.sector"))
    assert result.status is DataLoadStatus.INVALID_QUERY and result.observations == ()
    assert result.detail is not None
    assert "unavailable" in result.detail and "classification.sector" in result.detail
    assert "현재값 라벨" in result.detail  # 사유를 그대로 붙인다
    assert "price.adj_close" in result.detail  # supported 목록
    # 격자 3테이블이 서도 남는 미지원은 사유가 셋으로 갈린다 — 컬럼 부재 · 원천 부재 · 안 굽기
    ownership = _raw(adapter, fields=("flow.foreign_ownership",))
    assert ownership.detail is not None and "S08-2" in ownership.detail
    net_buy = _raw(adapter, fields=("credit.net_buy",))
    assert net_buy.detail is not None and "39컬럼에 순매수 축이 없다" in net_buy.detail
    ratio = _raw(adapter, fields=("short.short_balance_ratio",))
    assert ratio.detail is not None and "셀 하나로 굽지 않는다" in ratio.detail


def test_queries_outside_calendar_coverage_are_no_data(
    adapter: EquityDuckdbAdapter, root: Path
) -> None:
    beyond = _raw(adapter, start=START, end=date(2024, 1, 15))
    assert beyond.status is DataLoadStatus.NO_DATA
    assert beyond.detail is not None and "outside coverage" in beyond.detail
    assert str(root.resolve()) not in beyond.detail  # 422·run `error` 로 나가는 문장이다(#163)
    before = _raw(adapter, start=date(2023, 12, 1), end=START)
    assert before.status is DataLoadStatus.NO_DATA


def test_history_is_truncated_at_calendar_start_with_a_warning(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2023, 12, 27), end=date(2023, 12, 28), history=5)
    assert result.ok
    assert result.history_sessions == (WB_SESSIONS[0],)
    # 경고 문장은 한글로 완성하고 재현용 key=value 는 그대로 둔다(SoT, 이슈 #229).
    assert any("워밍업" in w and "requested=5" in w for w in result.warnings)


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
    # 재무는 `dataset_profile` 이 1세션으로 확정한 필드다 — 01-04 에 공개된 사업보고서는 그날이
    # 아니라 **다음 세션(01-05)** 부터 보인다. 공시가 장 마감 뒤에 올라오므로 당일 매매에 쓸 수
    # 없다(TECH_DEBT §4 — 이 랙이 0이던 동안 확정 look-ahead 였다).
    early = _cell(result, date(2024, 1, 4), "005930:1", "financial.revenue")
    assert (early.value, early.available_date) == (425.0, date(2023, 11, 14))
    late = _cell(result, date(2024, 1, 5), "005930:1", "financial.revenue")
    assert (late.value, late.available_date) == (460.0, date(2024, 1, 4))
    assert _field(result, START, "005930:1", "financial.book_equity") == 615.0
    assert _field(result, START, "005935:1", "financial.book_equity") == 615.0  # 같은 법인
    assert _field(result, START, "005930:1", "financial.operating_cash_flow") == 150.0
    # 같은 grain 의 CFS·OFS 중 v_fin_latest 가 CFS 를 고른다(OFS 는 9,999 로 깔아 뒀다)
    assert _field(result, START, "000660:1", "financial.book_equity") == 1_200.0
    # 값이 없는 계정은 셀이 나가되 MISSING 이고, 있는 계정은 OBSERVED 다(같은 행에서 갈린다).
    # 036220 의 보고서 공개일은 01-09 이고 랙 1세션이라 01-10 부터 보인다.
    missing = _cell(result, date(2024, 1, 10), "036220:2", "financial.revenue")
    assert (missing.value, missing.kind) == (None, CellKind.MISSING)
    # 잔고 계정은 사업보고서 한 행으로 선다(재무상태표 시점 값)
    assert _field(result, date(2024, 1, 10), "036220:2", "financial.book_equity") == 40.0
    # 재무 원천이 없는 종목(ETF)은 셀 자체가 없다 — mock 값으로 채우지 않는다
    assert not _has(result, START, "069500:1", "financial.revenue")
    # 창 독립: 같은 셀은 창을 좁혀도 같다(as-of 값은 (security, 컷오프) 의 함수다)
    narrow = _raw(adapter, start=START, end=START, fields=("financial.revenue",))
    assert _field(narrow, START, "005930:1", "financial.revenue") == _field(
        result, START, "005930:1", "financial.revenue"
    )


def test_flow_financials_are_pit_ttm_not_the_latest_report_period(
    adapter: EquityDuckdbAdapter,
) -> None:
    """흐름 계정(매출·이익·영업현금)은 최근 4분기 합(TTM)이다 — 보고서 종류가 기간을 바꾸지 않는다.

    #212: 예전에는 최신 공시가 분기면 3개월, 사업보고서면 12개월 값이 나와 ROE 같은 비율이
    공시 시즌마다 계단식으로 튀었다. 지금은 `v_fin_latest` 의 `ttm_*` 를 그대로 낸다. TTM 은 창
    안 4분기가 이 행의 공개일까지 전부 공개됐을 때만 서고(부분합 금지), 아니면 셀은 MISSING 이다.
    """
    fields = (
        "financial.revenue",
        "financial.gross_profit",
        "financial.operating_income",
        "financial.net_income",
        "financial.operating_cash_flow",
    )
    result = _raw(adapter, start=date(2024, 1, 3), end=END, fields=fields, universe="krx.all")
    # 01-04: 사업보고서(01-04 접수, 랙 1세션)가 아직 안 보여 2023 3분기 행이 최신이다.
    # 3분기 3개월 값(24)이 아니라 2022 4분기 ~ 2023 3분기 합이다.
    before = {f: _cell(result, date(2024, 1, 4), "005930:1", f) for f in fields}
    assert {f: c.value for f, c in before.items()} == {
        "financial.revenue": 425.0,
        "financial.gross_profit": 38.0 + 40.0 + 44.0 + 48.0,
        "financial.operating_income": 28.0 + 30.0 + 33.0 + 36.0,
        "financial.net_income": 84.0,
        "financial.operating_cash_flow": 120.0,
    }
    assert {c.available_date for c in before.values()} == {date(2023, 11, 14)}
    # 01-05: 사업보고서가 보이는 첫 세션 — TTM 이 연간 값과 같고 공개일이 사업보고서 접수일이다.
    after = {f: _cell(result, date(2024, 1, 5), "005930:1", f) for f in fields}
    assert {f: c.value for f, c in after.items()} == {
        "financial.revenue": 460.0,
        "financial.gross_profit": 184.0,
        "financial.operating_income": 138.0,
        "financial.net_income": 92.0,
        "financial.operating_cash_flow": 150.0,
    }
    assert {c.available_date for c in after.values()} == {date(2024, 1, 4)}
    # 앞 분기가 없어 4분기를 채울 수 없으면 3개월·12개월 값으로 대신하지 않고 MISSING 이다
    # (000660 은 2023 반기 1행, 036220 은 2023 사업보고서 1행뿐).
    for security_id, as_of in (("000660:1", START), ("036220:2", date(2024, 1, 10))):
        cell = _cell(result, as_of, security_id, "financial.net_income")
        assert (cell.value, cell.kind) == (None, CellKind.MISSING), security_id


def test_late_old_period_correction_does_not_revert_financials_to_that_period(
    adapter: EquityDuckdbAdapter,
) -> None:
    """옛 기간 정정본이 늦게 접수돼도 셀은 컷오프까지 공개된 가장 최근 기간을 유지한다 (#225).

    000660 은 2023 반기(08-14 접수) 뒤에 2023 1분기 정정본이 2024-01-09 에 접수된다. 랙 1세션이라
    01-10 부터 보인다. 예전에는 컷오프 이하 "가장 늦게 접수된 행" 을 골라 01-10 부터 1분기 값
    (자본 1,120)으로 되돌아갔다. 지금은 `v_fin_latest.period_frontier` 가 참인 행만 본다.
    """
    result = _raw(adapter, start=START, end=END, fields=ALL_FIELDS, universe="krx.all")
    for session in (date(2024, 1, 10), date(2024, 1, 11), END):
        cell = _cell(result, session, "000660:1", "financial.book_equity")
        assert (cell.value, cell.available_date) == (1_200.0, date(2023, 8, 14)), session
    # 창 독립: 정정본 공개 뒤 세션만 좁혀 물어도 같다
    narrow = _raw(adapter, start=END, end=END, fields=("financial.book_equity",))
    assert _field(narrow, END, "000660:1", "financial.book_equity") == 1_200.0


def test_a_late_correction_inside_the_ttm_window_completes_that_period_ttm_on_its_filing(
    tmp_path: Path,
) -> None:
    """#238: 창 안 분기의 정정본이 늦게 접수되면 그 기간 TTM 은 정정 접수일부터 보인다.

    000660 은 2023 반기(08-14 접수)가 공개된 가장 최근 기간이고, 그 TTM 창 [2022 3분기 ~ 2023
    반기] 안의 2023 1분기 정정본이 2024-01-09 에 접수된다. 예전에는 창이 반기 공개일에 완성되지
    않아 다음 정기보고서까지 결측이었다. 지금은 정정 접수일(랙 1세션이라 01-10)부터 반기 TTM 이
    선다. 고르는 기간(반기)과 잔액 필드(자본)는 그대로다.

    창 밖 2022 1분기 정정본(01-10)은 사업보고서 4분기 파생에만 기대므로 손익 TTM 공개일만 늦춘다
    — 손익은 01-11 부터, 영업현금은 01-10 부터 선다. 공개일 열이 둘인 이유다(#300 리뷰 P3-1).
    """
    root = build_workbench_root(tmp_path / "equity")
    fiscal_2022 = [
        {
            "corp_code": "C00660",
            "period_end": period_end,
            "report_code": report,
            "bsns_year": "2022",
            "rcept_no": f"R00660{report}_22",
            "available_date": available,
            "revenue": revenue,
            "net_income": net_income,
            "cf_operating_ytd": cf_ytd,
        }
        for period_end, report, available, revenue, net_income, cf_ytd in (
            (date(2022, 3, 31), "11013", date(2024, 1, 10), 150, 30, 20),
            (date(2022, 6, 30), "11012", date(2022, 8, 16), 160, 32, 45),
            (date(2022, 9, 30), "11014", date(2022, 11, 14), 170, 34, 70),
            (date(2022, 12, 31), "11011", date(2023, 3, 14), 660, 136, 100),
        )
    ]
    write_equity_table(
        root,
        "fin_std",
        fin_std_table([*WB_FIN_ROWS, *fiscal_2022]),
        build_id="b_fin_238",
        year_column="period_end",
    )
    write_catalog(root)
    fields = (
        "financial.revenue",
        "financial.net_income",
        "financial.operating_cash_flow",
        "financial.book_equity",
    )
    result = _raw(
        EquityDuckdbAdapter(root),
        start=date(2024, 1, 9),
        end=END,
        fields=fields,
        universe="krx.all",
    )

    def seen(session: date) -> dict[str, tuple[object, date]]:
        cells = {field: _cell(result, session, "000660:1", field) for field in fields}
        assert all((c.value is None) == (c.kind is CellKind.MISSING) for c in cells.values())
        return {field: (cell.value, cell.available_date) for field, cell in cells.items()}

    # 2022 3분기 · 2022 4분기(연간 − 1~3분기) · 2023 1분기(정정본) · 2023 반기
    revenue, net_income = 170.0 + 180.0 + 190.0 + 200.0, 34.0 + 40.0 + 38.0 + 40.0
    cash_flow, row = 25.0 + 30.0 + 40.0 + 30.0, date(2023, 8, 14)
    assert seen(date(2024, 1, 9)) == {
        "financial.revenue": (None, row),
        "financial.net_income": (None, row),
        "financial.operating_cash_flow": (None, row),
        "financial.book_equity": (1_200.0, row),
    }
    assert seen(date(2024, 1, 10)) == {
        "financial.revenue": (None, row),
        "financial.net_income": (None, row),
        "financial.operating_cash_flow": (cash_flow, date(2024, 1, 9)),
        "financial.book_equity": (1_200.0, row),
    }
    for session in (date(2024, 1, 11), END):
        assert seen(session) == {
            "financial.revenue": (revenue, date(2024, 1, 10)),
            "financial.net_income": (net_income, date(2024, 1, 10)),
            "financial.operating_cash_flow": (cash_flow, date(2024, 1, 9)),
            "financial.book_equity": (1_200.0, row),
        }, session


def test_consensus_picks_the_nearest_target_period_and_the_measured_source(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2024, 1, 4), end=END, fields=ALL_FIELDS)
    # 2023-12 관측점의 FY1 = 202312(5,000원) · 범위 = 6,000 − 4,000
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.forward_eps") == 5_000.0
    assert _field(result, date(2024, 1, 4), "005930:1", "consensus.eps_dispersion") == 2_000.0
    # 2024-01 관측점(01-05 공개)이 오면 FY1 이 202412 로 넘어간다 — 랙 1세션이라 다음 세션(01-08)
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.forward_eps") == 5_000.0
    later = _cell(result, date(2024, 1, 8), "005930:1", "consensus.forward_eps")
    assert (later.value, later.available_date) == (6_500.0, date(2024, 1, 5))
    assert _field(result, date(2024, 1, 8), "005930:1", "consensus.eps_dispersion") == 1_200.0
    assert _field(result, date(2024, 1, 8), "005930:1", "consensus.forward_sales") == 3_000_000.0
    # 같은 (ticker, obs_date) 의 v3·wise 중 잰 판본(wise, 95,000)이 이긴다 — 관측 01-04, 랙 1세션
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.target_price") == 95_000.0
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.recommendation") == 4.1
    assert _field(result, date(2024, 1, 5), "005930:1", "consensus.analyst_count") == 28.0
    # wise 가 없는 날은 v3 를 쓴다(coverage_degraded — 프로필이 그렇게 말한다). 관측 01-09 → 01-10
    assert _field(result, date(2024, 1, 10), "005930:1", "consensus.target_price") == 97_000.0


def test_event_fields_are_the_latest_filing_with_its_publication_date(
    adapter: EquityDuckdbAdapter,
) -> None:
    result = _raw(adapter, start=date(2024, 1, 4), end=END, fields=ALL_FIELDS, universe="krx.all")
    # 자사주 취득 결정 2건이 같은 공시일에 있으면 합한다(다른 event_type 은 섞이지 않는다).
    # 이벤트 축도 랙 1세션이라 공시일(01-05)이 아니라 다음 세션(01-08)부터 보인다.
    buyback = _cell(result, START, "005930:1", "event.buyback_amount")
    assert (buyback.value, buyback.available_date) == (1_500_000.0, date(2024, 1, 5))
    assert not _has(result, date(2024, 1, 5), "005930:1", "event.buyback_amount")
    # 임원 지분 증감은 같은 접수일의 보고자를 합하고(1,000 − 400) majorstock 은 빼놓는다
    insider = _cell(result, date(2024, 1, 10), "005930:1", "event.insider_net_buy")
    assert (insider.value, insider.available_date) == (600.0, date(2024, 1, 9))
    assert not _has(result, date(2024, 1, 9), "005930:1", "event.insider_net_buy")
    # 보고가 값 없이 하나뿐이면 합도 결측이다(0 으로 접지 않는다)
    empty = _cell(result, date(2024, 1, 10), "000660:1", "event.insider_net_buy")
    assert (empty.value, empty.kind) == (None, CellKind.MISSING)
    # 배당은 종류 축을 접어 보통주 값이 우선주 티커에도 간다(FIELD_MAP 부분 판정 ②)
    old = _cell(result, START, "005930:1", "event.dividend_per_share")
    assert (old.value, old.available_date) == (361.0, date(2023, 3, 7))
    assert _field(result, START, "005935:1", "event.dividend_per_share") == 361.0
    assert _field(result, date(2024, 1, 10), "005930:1", "event.dividend_per_share") == 400.0


def test_grid_fields_carry_the_missing_reason_and_never_a_synthetic_zero(
    adapter: EquityDuckdbAdapter,
) -> None:
    """S08~S10 격자 — `fill_kind` → `CellKind`, 0 채움 금지, 겹친 셀의 원천 선택."""

    # 격자 3표는 `dataset_profile` 이 1세션으로 확정한 축이다(원장이 다음 날 공표된다). 그래서
    # 원장 행의 날짜와 그 값이 보이는 세션이 한 칸 어긋난다 — `seen()` 이 그 사상을 이름 붙인다.
    def seen(row_date: date) -> date:
        return WB_SESSIONS[WB_SESSIONS.index(row_date) + 1]

    result = _raw(adapter, start=START, end=END, fields=ALL_FIELDS, history=1)
    # ① 같은 (ticker, date) 에 kiwoom·kis 두 행이 있으면 키움을 고른다(KIS 9,999 는 나오면 안 된다)
    assert _field(result, seen(START), "005930:1", "flow.foreign_net_buy") == -1_000_000.0
    assert _field(result, seen(START), "005930:1", "flow.retail_net_buy") == 3_000_000.0
    assert _field(result, seen(START), "005930:1", "flow.institution_net_buy") == -2_000_000.0
    # 키움이 없는 셀은 KIS 단독 행이 그대로 나간다
    assert _field(result, seen(START), "000660:1", "flow.foreign_net_buy") == -200_000.0
    # ② 진짜 0 은 OBSERVED 다 — 결측과 섞이지 않는다
    zero = _cell(result, seen(date(2024, 1, 11)), "005930:1", "flow.foreign_net_buy")
    assert (zero.value, zero.kind) == (0.0, CellKind.OBSERVED)
    # ③ src_omitted 는 값이 NULL 이라 MISSING 으로 접힌다(SOURCE_OMITTED_ZERO 는 값을 요구한다)
    omitted = _cell(result, seen(date(2024, 1, 9)), "005930:1", "flow.foreign_net_buy")
    assert (omitted.value, omitted.kind) == (None, CellKind.MISSING)
    # ④ not_collected 는 라벨이 살아 남는다 — '안 물어봤다' 와 '물었는데 없다' 는 다르다
    absent = _cell(result, seen(WB_HALT_DATE), "005930:1", "flow.retail_net_buy")
    assert (absent.value, absent.kind) == (None, CellKind.NOT_COLLECTED)
    # ⑤ 원장 행이 아예 없는 세션은 셀 자체가 없다(직전 값을 물지 않는다) — START 는 01-05 를 본다
    assert not _has(result, START, "005930:1", "flow.foreign_net_buy")
    # ⑥ short 는 원천을 고정한다 — 공매도는 키움 축, 대차는 KIS 축이고 사유 컬럼도 각자다
    assert _field(result, seen(START), "005930:1", "short.short_sale_value") == 70_000_000.0
    loan = _cell(result, seen(START), "005930:1", "short.borrowed_quantity")
    assert (loan.value, loan.kind) == (None, CellKind.NOT_COLLECTED)
    sale = _cell(result, seen(date(2024, 1, 9)), "005930:1", "short.short_sale_value")
    assert (sale.value, sale.kind) == (None, CellKind.MISSING)  # src_omitted
    assert _field(result, seen(date(2024, 1, 9)), "005930:1", "short.borrowed_quantity") == 12_345.0
    assert (
        _field(  # 음수 보존
            result, seen(WB_HALT_DATE), "005930:1", "short.borrowed_quantity"
        )
        == -50.0
    )

    # ⑦ 신용잔고 — measured 값, src_omitted·empty_response 는 MISSING, not_collected 는 그대로.
    # 신용 랙은 원장처럼 3세션이라(이슈 #246) 원장 행 날짜와 보이는 세션이 세 칸 어긋난다.
    def seen_credit(row_date: date) -> date:
        return WB_SESSIONS[WB_SESSIONS.index(row_date) + 3]

    measured = _field(result, seen_credit(date(2024, 1, 4)), "005930:1", "credit.margin_balance")
    assert measured == 8_359_855.0
    for session, kind in (
        (date(2024, 1, 5), CellKind.MISSING),  # src_omitted — 0 으로 굳히지 않는다
        (date(2024, 1, 8), CellKind.NOT_COLLECTED),
        (date(2024, 1, 9), CellKind.MISSING),  # empty_response(잔고 이상 격리 셀)
    ):
        cell = _cell(result, seen_credit(session), "005930:1", "credit.margin_balance")
        assert (cell.value, cell.kind) == (None, kind), session
    # 000660 은 권리락일(01-04)부터 무상증자 척도 창이다(#249). 신용잔고는 원장 뷰
    # `v_credit_balance` 가 가린 값을 읽으므로 원장 값(1,234)이 있어도 값이 없고, 셀 종류는 뷰의
    # 가림 표시(`bonus_window`)를 따른 MASKED 다 — 실행 결측 정책이 채우지 않는다(#298). 권리락
    # 전 행은 그대로다. 창 길이와 공시 전 세션 규칙은 뷰가 정한다
    # (원장 `tests/test_equity_v_credit_balance.py`).
    before = _cell(result, seen_credit(date(2024, 1, 3)), "000660:1", "credit.margin_balance")
    assert (before.value, before.kind) == (1_200.0, CellKind.OBSERVED)
    masked = _cell(result, seen_credit(WB_BONUS_EX), "000660:1", "credit.margin_balance")
    assert (masked.value, masked.kind, masked.available_date) == (
        None,
        CellKind.MASKED,
        WB_BONUS_EX,
    )
    # 창 안에서 원래 값이 없던 행(not_collected)도 MASKED 다 — 가림 표시가 결측 사유보다 먼저다.
    # 창의 값은 척도가 섞여 있어 모르는 값을 채워도 틀린다(#311 리뷰 P3-1)
    unknown = _cell(result, seen_credit(date(2024, 1, 5)), "000660:1", "credit.margin_balance")
    assert (unknown.value, unknown.kind) == (None, CellKind.MASKED)
    # ⑧ 프로필이 낼 수 있는 셀 종류를 선언한다 — 격자만 NOT_COLLECTED 를 갖고, 원장 뷰가 가리는
    # 원천만 MASKED 를 갖는다
    profiles = {p.field_id: p for p in adapter.list_fields()}
    assert profiles["credit.margin_balance"].coverage.supported_cell_kinds == (
        CellKind.OBSERVED,
        CellKind.MISSING,
        CellKind.NOT_COLLECTED,
        CellKind.MASKED,
    )
    assert profiles["price.close"].coverage.supported_cell_kinds == (
        CellKind.OBSERVED,
        CellKind.MISSING,
    )
    assert profiles["short.short_sale_value"].dataset_id == "short_daily"
    assert profiles["flow.institution_net_buy"].description.startswith("[부분]")


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
    # 01-09 에서 4세션 전 = 01-03 → 사업보고서(01-04 공개)는 아직 보이지 않는다. 값은 3분기 행의
    # TTM(2022 4분기 ~ 2023 3분기 매출 합)이다.
    (cell,) = panel.cells
    assert (cell.value, cell.available_date) == (425.0, date(2023, 11, 14))
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


def test_panel_rejects_unknown_or_malformed_security_ids(
    adapter: EquityDuckdbAdapter, root: Path
) -> None:
    unknown = adapter.load_panel(ResearchPanelQuery(START, END, ("000660:9",), ("price.close",)))
    assert unknown.status is DataLoadStatus.INVALID_QUERY
    assert unknown.detail is not None and "000660:9" in unknown.detail
    assert str(root.resolve()) not in unknown.detail  # 패널 미리보기 응답에 그대로 실린다(#163)
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
    metadata = adapter.resolve_factor_fields(("price.adj_close", "short.short_balance_ratio"))
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


def test_factor_observations_mark_cells_the_ledger_masked(adapter: EquityDuckdbAdapter) -> None:
    """팩터 연구 경로도 원장이 가린 셀을 `masked` 로 싣는다 — 결측 정책이 채우지 않게(#298)."""
    observations = adapter.load_factor_observations(
        FactorObservationQuery(("credit.margin_balance",), START, END, minimum_history_sessions=1)
    ).observations
    cells = {
        (item.as_of, item.security_id): field
        for item in observations
        for field in item.fields
    }
    # 권리락일(01-04) 행은 신용 랙 3세션 뒤(01-09)에 보인다 — 가린 셀이다
    masked = cells[(date(2024, 1, 9), "000660:1")]
    assert (masked.value, masked.masked) == (None, True)
    assert not cells[(date(2024, 1, 9), "005930:1")].masked


def test_factor_field_catalog_lists_every_field_as_a_numeric_series(
    adapter: EquityDuckdbAdapter,
) -> None:
    """compile 이 읽는 필드 계약 전부(P2-07). 같은 변환(`resolve_factor_fields`)을 거친다.

    이 어댑터는 그룹 필드를 주지 않으므로 그룹 연산은 unsupported 다. P2-08 스파이크 결론:
    원장에 PIT 섹터 시계열이 없다(`factor_field_catalog` docstring).
    """
    catalog = adapter.factor_field_catalog()

    field_ids = tuple(profile.field_id for profile in adapter.list_fields())
    assert catalog == adapter.resolve_factor_fields(field_ids).fields
    assert catalog, "필드가 하나도 없으면 compile 이 모든 필드를 없다고 본다"
    assert {field.value_type for field in catalog} == {NodeValueType.NUMERIC_SERIES}


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
    assert {b.session for b in dataset.bars if b.security_id == "036220:2"} == set(WB_SESSIONS[8:])
    # `unknown_krx` 는 corp_event 에 유형이 없는 KRX 기준가 원천 행이라 방향을 share_factor 가
    # 정한다(0.5 → reverse_split). 엔진 어댑터와 같은 어휘를 쓴다 — 한쪽만 알면 같은 데이터로
    # 한쪽에서만 run 이 죽는다.
    assert dataset.corporate_actions == (
        replace(
            dataset.corporate_actions[0],
            session=WB_SPLIT_DATE,
            security_id="000660:1",
            action_type="split",
            ratio="2.0",
            detail="000660:split:2024-01-08",
        ),
        replace(
            dataset.corporate_actions[1],
            session=date(2024, 1, 9),
            security_id="036220:2",
            action_type="reverse_split",
            ratio="0.5",
            detail="036220:krx_base:2024-01-09",
        ),
    )
    assert {m.security_id: (m.first_session, m.last_session) for m in dataset.memberships}[
        "036220:2"
    ] == (WB_SPLIT_DATE, END)
    assert [w.code for w in dataset.warnings] == ["equity.reference_rows_dropped"]


def test_backtest_dataset_answers_warmup_actions_apart_from_engine_actions(
    adapter: EquityDuckdbAdapter,
) -> None:
    """√ 충격 σ(검증 랩 V2-03)는 워밍업 구간 분할 날의 원주가 수익률을 빼야 한다. 워밍업 세션의
    사건은 엔진이 적용하는 `corporate_actions` 가 아니라 `history_corporate_actions` 로 답한다 —
    엔진 쪽에 섞이면 bar 없는 세션의 사건으로 run 이 죽는다."""
    start = WB_SESSIONS[WB_SESSIONS.index(WB_SPLIT_DATE) + 1]
    query = BacktestDataQuery(start, END, ("000660:1",), None)

    plain = adapter.load_backtest_dataset(query)
    warmed = adapter.load_backtest_dataset(replace(query, history_sessions_before_start=3))

    assert plain.history_corporate_actions == ()
    assert warmed.corporate_actions == plain.corporate_actions
    assert [(a.session, a.action_type) for a in warmed.history_corporate_actions] == [
        (WB_SPLIT_DATE, "split")
    ]


def test_backtest_dataset_drops_actions_after_the_last_bar_with_a_warning(
    tmp_path: Path,
) -> None:
    """정지 중 감자·병합처럼 창 안 마지막 bar 뒤에 오는 사건은 엔진이 정산할 세션이 없어
    run 전체를 죽인다(`CorporateActionWithoutBar`, engine/loop.py). 그 포지션은 이미 마지막
    체결가에 동결된 상태이므로 어댑터가 사건을 빼고 경고로 남긴다 — 정상 종목의 사건은 그대로."""
    root = build_workbench_root(
        tmp_path / "root",
        extra_factor_rows=[
            ("000660", WB_HALT_DATE, "000660:capred:2024-01-10", "capred", 0.5, True),
        ],
    )
    dataset = EquityDuckdbAdapter(root).load_backtest_dataset(
        BacktestDataQuery(START, WB_HALT_DATE, ("000660:1",), None)
    )
    assert [b.session for b in dataset.bars] == [START, date(2024, 1, 9)]  # 01-10 은 정지 행
    assert [(a.session, a.action_type) for a in dataset.corporate_actions] == [
        (WB_SPLIT_DATE, "split")
    ]
    assert [w.code for w in dataset.warnings] == [
        "equity.reference_rows_dropped",
        "equity.corporate_action_without_bar_dropped",
    ]
    dropped = dataset.warnings[1]
    assert dropped.severity is WarningSeverity.WARNING
    assert "dropped=1" in dropped.message and "000660:1@2024-01-10:reverse_split" in dropped.message
    assert all(_HANGUL.search(item.message) for item in dataset.warnings)


def test_backtest_dataset_adjusts_an_unfolded_level_shift_by_the_base_price_ratio(
    adapter: EquityDuckdbAdapter,
) -> None:
    """원장이 계수를 못 낸 층 이동(`krx_base_inconsistent`)은 적용일 KRX 기준가 비로 수량을
    바꾼다(#369).

    엔진은 원주가 × 보유 수량으로 평가해, 사건 없이 bar 만 내면 층 배수가 곧 손익이다(실원장
    025560 2020-06-11 종가 79 → 07-02 종가 3,700, ×46.8). 035420 은 01-10 에 정지했고 01-11
    기준가가 정지일 종가의 10배다.
    - 비의 분모는 앞 행(정지일 기준가 행) 종가 205,000 이라 비는 0.1 이다. 직전 거래 종가(01-09)
      204,500 을 쓰면 0.09976 이고, 한 정지 구간에 재설정이 여럿이면 앞 재설정이 겹쳐 곱해진다.
    - 01-09 의 늦게 공개된 ok 계수(뷰의 `factor_ok` 참)는 계수가 이미 층 이동을 설명한다 — 기준가
      비로 한 번 더 조정하지 않아, 사건은 ok 계수 하나와 미접힘 하나뿐이다.
    손계산: 01-09 종가 204,500 에 100주(2,045만 원) → 비 0.1 로 10주 → 01-11 종가 2,055,000 에
    2,055만 원(+0.49%, 정지일 종가 변화 포함). 조정이 없으면 2억 550만 원(×10.05)이다.
    """
    dataset = adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("035420:1",), None))
    shift = CorporateActionRecord(
        WB_INCONSISTENT,
        "035420:1",
        "reverse_split",
        "0.1",
        "원장 미접힘·기준가 비 prev_close=205000 base=2050000",
    )
    assert dataset.corporate_actions == (
        CorporateActionRecord(
            WB_LATE_FACTOR, "035420:1", "reverse_split", "0.5", "035420:krx_base:2024-01-09"
        ),
        shift,
    )
    closes = {bar.session: bar.close for bar in dataset.bars}
    assert WB_HALT_DATE not in closes  # 정지일은 bar 가 없다
    held = 100 * float(shift.ratio)
    assert held == 10
    assert held * closes[WB_INCONSISTENT] / (100 * closes[WB_LATE_FACTOR]) - 1 == pytest.approx(
        100_000 / 20_450_000
    )
    [warning] = [w for w in dataset.warnings if w.code == "equity.unfolded_level_shift"]
    assert warning.severity is WarningSeverity.WARNING
    assert "035420:1@2024-01-11 ratio=0.1 prev_close=205000 base=2050000" in warning.message
    assert _HANGUL.search(warning.message)
    # 워밍업 창의 층 이동은 충격 σ 만 읽는 사건이다 — 엔진 사건도 경고도 아니다
    warmed = adapter.load_backtest_dataset(
        BacktestDataQuery(END, END, ("035420:1",), None, history_sessions_before_start=2)
    )
    assert (warmed.corporate_actions, warmed.history_corporate_actions) == ((), (shift,))
    assert "equity.unfolded_level_shift" not in {w.code for w in warmed.warnings}


def test_backtest_dataset_counts_provisional_evening_rows_apart_from_invalid_ones(
    tmp_path: Path,
) -> None:
    """저녁 잠정판(e1.15.0 `price_daily.basis='evening'`)의 T 행은 bar 로 나가지 않되
    **`n_invalid` 와 다른 자리에서** 센다(DEFECT-C07).

    평일 22:40~09:20 KST 동안 equity 의 current 판은 저녁 잠정판이고 T 행은 키움 종가·거래량만
    있다(OHL NULL, KRX 확정 전). 예전에는 `basis` 를 SELECT 조차 하지 않아 그 행이 "OHLC 가 깨진
    행"(GAP-14)과 같은 카운터에 섞였다 — 소비자가 "그날 데이터가 깨졌다" 와 "잠정이라 뺐다" 를
    구별할 수 없었다.
    """
    root = build_workbench_root(tmp_path / "equity", evening_session=WB_EVENING_SESSION)
    dataset = EquityDuckdbAdapter(root).load_backtest_dataset(
        BacktestDataQuery(date(2024, 1, 11), WB_EVENING_SESSION, ("005930:1",), None)
    )
    assert [b.session for b in dataset.bars] == [date(2024, 1, 11), date(2024, 1, 12)]
    warnings = {w.code: w.message for w in dataset.warnings}
    assert "equity.invalid_ohlc_rows_dropped" not in warnings
    assert "dropped=1" in warnings["equity.provisional_rows_dropped"]
    assert _HANGUL.search(warnings["equity.provisional_rows_dropped"])


def test_backtest_dataset_reads_roots_without_the_basis_column(
    adapter: EquityDuckdbAdapter,
) -> None:
    """`basis` 는 e1.15.0 부터다 — 그 컬럼이 없는 옛 판 루트는 전부 확정(krx)으로 읽는다."""
    dataset = adapter.load_backtest_dataset(
        BacktestDataQuery(date(2024, 1, 11), date(2024, 1, 12), ("005930:1",), None)
    )
    assert [b.session for b in dataset.bars] == [date(2024, 1, 11), date(2024, 1, 12)]
    assert [w.code for w in dataset.warnings] == ["equity.reference_rows_dropped"]


def test_backtest_dataset_refuses_unknown_and_index_ids(
    adapter: EquityDuckdbAdapter, root: Path
) -> None:
    with pytest.raises(ValueError, match="unknown security_id") as unknown:
        adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("000660:9",), None))
    assert str(root.resolve()) not in str(unknown.value)  # run `error` 로 나간다(#163)
    with pytest.raises(ValueError, match="malformed security_id"):
        adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("000660:1",), "idx:코스피"))


def test_lag_falls_back_to_source_constants_and_says_so_when_the_profile_is_absent(
    tmp_path: Path,
) -> None:
    """`dataset_profile` 없는 루트는 원천 상수로 돌아가되 **그 사실을 근거 문자열에 남긴다**.

    조용히 폴백하면 어댑터가 자기 상수로 PIT 를 우기던 예전 상태로 되돌아간 것을 아무도 모른다
    (TECH_DEBT §4). 그래서 값이 갈리는 것보다 갈렸다는 표시가 중요하다.
    """
    root = build_workbench_root(tmp_path / "equity", profile=False)
    profiles = {p.field_id: p for p in EquityDuckdbAdapter(root).list_fields()}
    # 폴백 값도 원장 선언과 같다 — 표가 없다고 원장보다 짧게 읽지 않는다(이슈 #246). 원장과의
    # 대조는 `tests/contract/test_equity_fallback_lag.py` 가 한다.
    fixture_lags = {field_id: lag for field_id, lag, _ in WB_PROFILE_ROWS}
    assert {f: p.recommended_lag_sessions for f, p in profiles.items()} == {
        f: fixture_lags[f] for f in profiles
    }
    assert all(
        "fallback: no dataset_profile row" in p.available_date_basis for p in profiles.values()
    )
    # 대장이 있는 루트에서는 폴백 표시가 없다
    with_profile = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity2"))
    served = {p.field_id: p for p in with_profile.list_fields()}
    assert not any("fallback" in p.available_date_basis for p in served.values())
    assert served["credit.margin_balance"].recommended_lag_sessions == 3


# ── 카탈로그·환경 실패 ────────────────────────────────────────────────────────


def test_missing_or_stale_catalog_makes_macro_fields_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """카탈로그가 없거나 낡거나 원천이 읽는 매크로가 없으면 **매크로를 읽는 필드만** 빠지고,
    사유 문장과 부팅 경고가 `catalog_*` 코드와 재생성 조치(다시 만든 뒤 서버 재시작)를 싣는다.

    매크로 필드 목록의 정본은 FIELD_MAP §3 「부팅 검사」다. S23(2026-09-06)이 조정가를 표로 옮겨
    카탈로그 의존을 끊었지만, #220 부터 워크벤치는 조정 공백 적용일을 가린 원장 뷰를 읽는다 — 표로
    돌아가 읽으면 가린 공백·창이 조용히 다시 열리므로 원천을 뺀다(fail-closed). 매크로를 더한 코드를
    받고 카탈로그를 다시 만들지 않은 루트(`catalog_macro_missing`)는 예전에 경고 없이 신용 필드를
    뺐다(#292 리뷰 P2-2).
    """
    macro_fields = {
        "financial.book_equity", "consensus.forward_eps", "credit.margin_balance",
        "price.adj_close",
    }
    root = build_workbench_root(tmp_path / "equity", catalog=False)
    for code, prepare in (
        ("catalog_missing", lambda: None),
        ("catalog_stale", lambda: write_catalog(root, snapshot="deadbeefdeadbeef")),
        ("catalog_macro_missing", lambda: write_catalog(root, with_macros=False)),
    ):
        prepare()
        caplog.clear()
        with caplog.at_level("WARNING"):
            adapter = EquityDuckdbAdapter(root)
        served = {p.field_id for p in adapter.list_fields()}
        # 매크로가 없으면 그 매크로를 읽는 원천의 필드가 전부 빠진다 — 표 원천은 남는다
        assert "price.close" in served and "consensus.target_price" in served, code
        assert not served & macro_fields, code
        assert _raw(adapter, fields=("price.close",)).ok
        for field_id in ("credit.margin_balance", "price.adj_close"):
            denied = _raw(adapter, fields=(field_id,))
            assert denied.status is DataLoadStatus.INVALID_QUERY
            assert denied.detail is not None and code in denied.detail, (code, field_id)
            # 조치가 사유에 실린다 — 원천은 부팅 때 정해지므로 재시작까지 적는다
            assert "ledger_sync catalog" in denied.detail and "다시 띄워" in denied.detail
            # 사유는 질의 거절로 사용자에게 간다(#163)
            assert str(root.resolve()) not in denied.detail
        assert any(  # 부팅 로그에도 남는다
            code in r.getMessage() and "ledger_sync catalog" in r.getMessage()
            for r in caplog.records
        ), code
        # 백테스트는 원장이 접지 못한 층 이동을 카탈로그 뷰로만 읽는다 — 표로 돌아가 술어를 다시
        # 적지 않고, run 이 코드화된 실패(`backtest.run.data_not_ready`)로 끝나게 멈춘다(#369).
        # 조치는 사유 문장이 말한다
        with pytest.raises(BacktestDataNotReadyError, match=code) as stopped:
            adapter.load_backtest_dataset(BacktestDataQuery(START, END, ("005930:1",), None))
        assert "ledger_sync catalog" in str(stopped.value) and "다시 띄워" in str(stopped.value)
        assert str(root.resolve()) not in str(stopped.value)
    # 원장 판은 meta 가 아니라 MANIFEST 에서 온다
    assert adapter.snapshot().snapshot_id.startswith(
        snapshot_id(table_builds(root)) + SNAPSHOT_CONTRACT_SEPARATOR
    )


def test_catalog_without_required_view_column_drops_only_that_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """옛 카탈로그(`v_fin_latest` 에 원천이 읽는 열이 없다)는 재무 원천만 뺀다 (#233 리뷰 P2-1).

    읽는 열은 원천이 선언한 `period_frontier`(#225, `row_filter`)와 흐름 필드의 공개일 열(#238,
    `FieldSpec.available_expr`)이다. #225 전 판은 셋 다, 머지 직후 로컬·서버 판(#238 전)은 공개일
    두 열이 없다 — 가장 옛 판으로 셋을 모두 이름으로 알리는지 본다(#300 리뷰 P3-2).

    매크로는 게시돼 있어 `catalog_macro_missing` 가드는 통과한다. 예전에는 fin 원천의
    `row_filter` 가 커버율 질의에서 BinderException 을 던져 `list_fields()` 전체가 죽었다 — 필드
    목록을 쓰는 화면과 AI 컨텍스트가 모두 막혔다. 지금은 부팅 때 열을 확인해 재무 필드만 빠지고
    경고를 남긴다.
    """
    absent = ("period_frontier", "ttm_income_available_date", "ttm_cf_available_date")
    root = build_workbench_root(tmp_path / "equity", catalog=False)
    write_catalog(root, legacy_fin_columns=absent)
    with caplog.at_level("WARNING"):
        legacy = EquityDuckdbAdapter(root)
    served = {p.field_id for p in legacy.list_fields()}
    assert "price.close" in served and "consensus.forward_eps" in served
    assert not served & {"financial.book_equity", "financial.net_income"}
    denied = _raw(legacy, fields=("financial.book_equity",))
    assert denied.status is DataLoadStatus.INVALID_QUERY
    assert denied.detail is not None and "catalog_columns_missing" in denied.detail
    assert f"missing={list(absent)}" in denied.detail
    assert _raw(legacy, fields=("price.close",)).ok
    warned = [r.getMessage() for r in caplog.records if "catalog_columns_missing" in r.getMessage()]
    assert warned and "v_fin_latest" in warned[0] and "카탈로그" in warned[0]
    # 카탈로그 경로는 운영자 로그에만 남고 질의 거절 상세(API 응답)에는 없다(#163 댓글)
    assert str(root.resolve()) in warned[0] and str(root.resolve()) not in denied.detail


def test_catalog_view_without_its_mask_column_drops_only_that_source(tmp_path: Path) -> None:
    """가림 표시 열이 없는 옛 뷰는 그 원천만 뺀다 — 부팅 열 확인이 가림 표시(`masked_expr`)도
    선언에서 끌어온다(#311 리뷰 P3-4). 표시를 못 읽은 채 두면 가린 셀이 MISSING 으로 나가 결측
    정책이 다시 채운다."""
    import duckdb

    root = build_workbench_root(tmp_path / "equity")
    catalog = duckdb.connect(str(root / "equity.duckdb"))
    try:
        row = catalog.execute(
            "SELECT macro_definition FROM duckdb_functions() WHERE function_name = 'v_adj_close'"
        ).fetchone()
        assert row is not None
        catalog.execute(
            "CREATE OR REPLACE MACRO v_adj_close(as_of) AS TABLE "
            f"SELECT * EXCLUDE (adj_gap) FROM ({row[0]})"
        )
    finally:
        catalog.close()
    adapter = EquityDuckdbAdapter(root)
    served = {p.field_id for p in adapter.list_fields()}
    assert "price.close" in served and "price.adj_close" not in served
    denied = _raw(adapter, fields=("price.adj_close",))
    assert denied.detail is not None and "catalog_columns_missing" in denied.detail
    assert "missing=['adj_gap']" in denied.detail


@pytest.mark.parametrize(
    ("table", "macro", "dropped", "kept"),
    [
        ("fin_std", "v_fin_latest", "financial.book_equity", "consensus.forward_eps"),
        ("consensus_daily", "v_consensus", "consensus.forward_eps", "financial.book_equity"),
    ],
    ids=["fin", "consensus"],
)
def test_unreadable_catalog_macro_at_boot_drops_only_that_source(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    table: str,
    macro: str,
    dropped: str,
    kept: str,
) -> None:
    """부팅 때 매크로 읽기(DESCRIBE)가 duckdb 오류를 내도 어댑터는 뜨고 그 원천만 빠진다.

    #233 리뷰 후속.

    스냅샷은 맞는데 매크로가 가리키는 parquet 파일이 빠진 카탈로그다. #233 전에는 재무 질의만
    실패했는데, #233 의 부팅 DESCRIBE 가 IOException 을 생성자 밖으로 던져 어댑터 전체가 죽었다.
    확인할 열이 없는 `v_consensus` 원천은 부팅 때 읽지 않아 `list_fields()` 가 원시 IOException
    으로 죽었다(#275 리뷰 P3-5).
    """
    root = build_workbench_root(tmp_path / "equity")
    removed = sorted((root / table).rglob("*.parquet"))
    assert removed, f"{table} 파티션 파일이 없다 — root={root}"
    for path in removed:
        path.unlink()
    with caplog.at_level("WARNING"):
        broken = EquityDuckdbAdapter(root)
    served = {p.field_id for p in broken.list_fields()}
    assert {"price.close", kept} <= served and dropped not in served
    denied = _raw(broken, fields=(dropped,))
    assert denied.status is DataLoadStatus.INVALID_QUERY
    assert denied.detail is not None and "catalog_macro_unreadable" in denied.detail
    assert _raw(broken, fields=("price.close",)).ok
    warned = [
        r.getMessage() for r in caplog.records if "catalog_macro_unreadable" in r.getMessage()
    ]
    assert warned and macro in warned[0]
    # duckdb 원문은 빠진 parquet 경로를 담는다 — 원문과 카탈로그 경로는 로그에만 싣는다(#163 댓글)
    assert str(root.resolve()) in warned[0] and str(root.resolve()) not in denied.detail


@pytest.mark.parametrize("payload", [b"garbage" * 50, b""], ids=["garbage", "empty"])
def test_corrupt_catalog_parquet_at_boot_drops_only_that_source(
    tmp_path: Path, payload: bytes
) -> None:
    """손상·0바이트 parquet 는 duckdb `InvalidInputException` 이다 — 원천만 빼고 뜬다 (#245 P3-4).

    예외 목록을 카탈로그 성격으로 좁히면서 이 예외가 빠져, 부팅 전체가 다시 실패했다.
    """
    root = build_workbench_root(tmp_path / "equity")
    files = sorted((root / "fin_std").rglob("*.parquet"))
    assert files, f"fin_std 파티션 파일이 없다 — root={root}"
    for path in files:
        path.write_bytes(payload)
    broken = EquityDuckdbAdapter(root)
    served = {p.field_id for p in broken.list_fields()}
    assert "price.close" in served
    assert not served & {"financial.book_equity", "financial.net_income"}
    denied = _raw(broken, fields=("financial.book_equity",))
    assert denied.detail is not None and "catalog_macro_unreadable" in denied.detail
    assert "InvalidInputException" in denied.detail


def test_transient_duckdb_error_at_boot_is_not_cached_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """일시적 duckdb 오류(중단·메모리 부족)는 원천을 빼는 사유가 아니다 (#245 리뷰 P3-1).

    부팅 때 뺀 원천은 `_source_reason` 에 캐시돼 재시작할 때까지 돌아오지 않는다. 그래서 잡는 것은
    카탈로그 성격의 오류(파일 누락·매크로 누락·스키마 드리프트)뿐이고, 나머지는 부팅을 멈춰 다시
    시도하게 한다. 멈출 때는 원시 duckdb 예외가 아니라 원인 코드와 조치를 담은 설정 오류다(#247).
    """
    import duckdb

    root = build_workbench_root(tmp_path / "equity")
    real_open = _open

    class _Interrupting:
        def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
            self._inner = inner

        def __enter__(self) -> _Interrupting:
            return self

        def __exit__(self, *exc_info: object) -> None:
            self._inner.close()

        def execute(self, sql: str, *args: object) -> duckdb.DuckDBPyConnection:
            if sql.startswith("DESCRIBE"):
                raise duckdb.InterruptException("simulated interrupt during DESCRIBE")
            return self._inner.execute(sql, *args)

    # 부팅의 매크로 확인은 카탈로그를 직접 연다(질의용 `_connect` 는 잠김을 run 실패로 코드화한다,
    # #318). 카탈로그 연결만 감싸 그 DESCRIBE 에서 일시 오류를 낸다 — 앞선 파일 확인은 SELECT 다.
    monkeypatch.setattr(
        f"{_ADAPTER}._open",
        lambda path: real_open(path) if path is None else _Interrupting(real_open(path)),
    )
    with pytest.raises(EquityDuckdbSetupError, match="catalog_transient_error") as raised:
        EquityDuckdbAdapter(root)
    assert isinstance(raised.value.__cause__, duckdb.InterruptException)
    assert "다시 띄우면 다시 확인한다" in str(raised.value)


@pytest.mark.parametrize(
    ("name", "content", "detail"),
    [
        ("equity.duckdb", b"garbage" * 50, "not a valid DuckDB database file"),
        ("_catalog_meta.json", b'{"snapshot_id": ', "JSONDecodeError"),
        ("_catalog_meta.json", b"[]", "JSON object"),
    ],
    ids=["catalog-file", "meta-json", "meta-not-object"],
)
def test_corrupt_catalog_file_or_meta_at_boot_drops_every_macro_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str, content: bytes, detail: str
) -> None:
    """카탈로그 파일이나 meta 가 손상되면 카탈로그가 없을 때처럼 매크로 원천만 빠지고 뜬다.

    예전에는 열 확인의 연결이 `try` 밖이라 원시 `IOException`("not a valid DuckDB database
    file")으로 부팅이 죽었고(#247 a), meta 손상은 설정 오류로 부팅을 멈췄다(#278). 쓸 수 없는
    카탈로그면 매크로 원천(재무·컨센서스·신용·수정주가)이 모두 같이 빠져야 첫 질의에서 다시 죽지
    않는다.
    """
    root = build_workbench_root(tmp_path / "equity")
    (root / name).write_bytes(content)
    with caplog.at_level("WARNING"):
        broken = EquityDuckdbAdapter(root)
    served = {p.field_id for p in broken.list_fields()}
    assert "price.close" in served and "consensus.target_price" in served
    assert not served & {
        "financial.book_equity", "consensus.forward_eps", "credit.margin_balance", "price.adj_close"
    }
    denied = _raw(broken, fields=("consensus.forward_eps",))
    assert denied.status is DataLoadStatus.INVALID_QUERY
    assert denied.detail is not None and "catalog_unreadable" in denied.detail
    assert f"file={name}" in denied.detail and "ledger_sync catalog" in denied.detail  # 조치 안내
    assert _raw(broken, fields=("price.close",)).ok
    warned = [r.getMessage() for r in caplog.records if "catalog_unreadable" in r.getMessage()]
    assert len(warned) == 1 and detail in warned[0]
    assert str(root.resolve()) in warned[0] and str(root.resolve()) not in denied.detail


# 하위 프로세스가 카탈로그를 쓰기 모드로 잡는다. Windows 는 공유 위반, POSIX 는 fcntl 로 막히는
# 진짜 잠금이다.
_HOLD_CATALOG_FOR_WRITE = (
    "import sys, time, duckdb; con = duckdb.connect(sys.argv[1]); print('held', flush=True); "
    "time.sleep(120)"
)


def test_catalog_locked_by_another_process_stops_boot_with_a_coded_error(tmp_path: Path) -> None:
    """카탈로그가 잠겨 있으면 원시 `IOException` 대신 원인 코드와 조치를 담은 설정 오류다 (#247 b).

    잠김은 풀리면 원천이 돌아와야 하는 상태라 손상처럼 원천을 빼고 뜨지 않는다 — 뺀 사유는 재시작
    전까지 캐시된다(#245 의 일시 오류와 같은 규칙).
    """
    import duckdb

    root = build_workbench_root(tmp_path / "equity")
    with subprocess.Popen(
        [sys.executable, "-c", _HOLD_CATALOG_FOR_WRITE, str(root / "equity.duckdb")],
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
            with pytest.raises(EquityDuckdbSetupError, match="catalog_locked") as raised:
                EquityDuckdbAdapter(root)
        finally:
            holder.kill()  # 나가면서 `Popen` 이 파이프를 닫고 종료를 기다린다
    assert isinstance(raised.value.__cause__, duckdb.IOException)
    assert "닫은 뒤 다시 띄워야 한다" in str(raised.value)
    assert EquityDuckdbAdapter(root).list_fields()  # 잠금이 풀리면 그대로 뜬다


def test_catalog_locked_after_boot_fails_only_the_macro_queries(tmp_path: Path) -> None:
    """부팅 뒤 카탈로그가 잠겨도 매크로를 안 읽는 질의(원주가·유니버스)는 돈다 (#278).

    예전에는 질의마다 카탈로그를 열어, 잠긴 동안 가격 질의까지 원시 `IOException` 으로 죽었다.
    매크로 원천(재무) 질의는 잠금이 풀릴 때까지 실패한다 — 빈 결과로 넘어가지 않는다. 백테스트
    데이터도 원장이 접지 못한 층 이동을 카탈로그 뷰로 읽어(#369) 같이 실패한다 — 사건 없이 bar 만
    내면 그 종목 손익이 층 배수만큼 튄다. 실패는 duckdb 원문(점유 프로세스 경로·PID·POSIX 계정명)
    대신 조치만 담은 `catalog_locked` 사유다 — run 실패 사유로 화면에 나가기 때문이다(#318).
    """
    import duckdb

    root = build_workbench_root(tmp_path / "equity")
    booted = EquityDuckdbAdapter(root)
    fin = ("financial.book_equity",)
    backtest = BacktestDataQuery(START, END, ("005930:1",), None)
    with subprocess.Popen(
        [sys.executable, "-c", _HOLD_CATALOG_FOR_WRITE, str(root / "equity.duckdb")],
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
            assert _raw(booted, fields=("price.close",)).ok  # 표 원천만 읽는 격자
            assert booted.load_universe(UniverseHistoryQuery("XKRX", START, END)).ok
            with pytest.raises(BacktestDataNotReadyError, match="catalog_locked") as macro:
                _raw(booted, fields=fin)
            with pytest.raises(BacktestDataNotReadyError, match="catalog_locked") as dataset:
                booted.load_backtest_dataset(backtest)
        finally:
            holder.kill()  # 나가면서 `Popen` 이 파이프를 닫고 종료를 기다린다
    locked = "|".join(re.escape(marker) for marker in _LOCK_CONFLICT_MARKERS)
    for stopped in (macro.value, dataset.value):
        # 사유에는 원문이 없고, 원문은 예외 사슬로 서버 로그에 남는다
        assert re.search(locked, str(stopped)) is None
        assert str(root) not in str(stopped) and "PID" not in str(stopped)
        assert isinstance(stopped.__cause__, duckdb.IOException)
        assert re.search(locked, str(stopped.__cause__))
    assert _raw(booted, fields=fin).ok  # 잠금이 풀리면 다시 읽는다
    assert booted.load_backtest_dataset(backtest).bars


def test_a_non_lock_catalog_error_after_boot_is_not_reported_as_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """질의 중 잠김이 아닌 카탈로그 오류(손상 등)는 `catalog_locked` 로 삼키지 않는다
    (#406 리뷰 P3-1).

    삼키면 손상된 카탈로그에 "그 작업이 끝난 뒤 다시 실행한다"를 안내해, 운영자가 기다리기만 하고
    검증·재생성을 하지 않는다. 원래 duckdb 예외가 그대로 올라간다.
    """
    import duckdb

    root = build_workbench_root(tmp_path / "equity")
    booted = EquityDuckdbAdapter(root)
    corrupt = "IO Error: The file exists, but it is not a valid DuckDB database file!"

    def open_corrupt(path: Path | None) -> duckdb.DuckDBPyConnection:
        if path is None:
            return _open(path)
        raise duckdb.IOException(corrupt)

    monkeypatch.setattr(f"{_ADAPTER}._open", open_corrupt)
    with pytest.raises(duckdb.IOException, match="not a valid DuckDB database file"):
        _raw(booted, fields=("financial.book_equity",))
    with pytest.raises(duckdb.IOException, match="not a valid DuckDB database file"):
        booted.load_backtest_dataset(BacktestDataQuery(START, END, ("005930:1",), None))


def test_missing_required_table_fails_at_construction(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity")
    (root / "universe_policy" / "MANIFEST.json").unlink()
    with pytest.raises(EquityDuckdbSetupError, match="universe_policy"):
        EquityDuckdbAdapter(root)
    with pytest.raises(EquityDuckdbSetupError, match="not a directory"):
        EquityDuckdbAdapter(tmp_path / "nowhere")


def test_unreadable_dataset_profile_fails_with_setup_error(tmp_path: Path) -> None:
    """`dataset_profile` 이 MANIFEST 에는 있는데 읽히지 않으면 진단이 담긴 설정 오류다 (#245).

    예외 절이 `duckdb.Error` 를 이름으로 참조하는데 duckdb 는 `TYPE_CHECKING` 에서만 import 돼,
    이 경로에 들어가면 설정 오류 대신 NameError 가 났다.
    """
    root = build_workbench_root(tmp_path / "equity")
    removed = sorted((root / "dataset_profile").rglob("*.parquet"))
    assert removed, f"dataset_profile 파티션 파일이 없다 — root={root}"
    for path in removed:
        path.unlink()
    with pytest.raises(EquityDuckdbSetupError, match="dataset_profile exists but is unreadable"):
        EquityDuckdbAdapter(root)


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


@pytest.fixture(scope="module")
def degraded_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """선택 표 하나(`flow_daily`)와 카탈로그 meta 가 빠지고, 멤버가 없는 정책이 있는 루트."""
    root = build_workbench_root(
        tmp_path_factory.mktemp("degraded") / "equity",
        extra_policy_rows=[("krx.none", "none", 1, "FALSE")],
    )
    (root / "flow_daily" / "MANIFEST.json").unlink()
    (root / "_catalog_meta.json").unlink()
    return root


@pytest.mark.parametrize(
    ("phrase", "query"),
    [
        # 달력 안이지만 주말·신정뿐인 구간. 이 루트는 카탈로그 meta 가 없어 매크로 필드(조정가 등)가
        # 빠지므로 표 필드로 묻는다
        (
            "no sessions in range",
            lambda a: _raw(
                a, start=date(2023, 12, 30), end=date(2024, 1, 1), fields=("price.close",)
            ),
        ),
        ("no members in universe", lambda a: _raw(a, universe="krx.none", fields=("price.close",))),
        # 036220 의 1구간은 2023-12-29 에 끝난다
        (
            "no panel cells",
            lambda a: a.load_panel(ResearchPanelQuery(START, END, ("036220:1",), ("price.close",))),
        ),
        ("equity tables not built", lambda a: _raw(a, fields=("flow.foreign_net_buy",))),
        ("catalog_missing", lambda a: _raw(a, fields=("financial.book_equity",))),
    ],
    ids=["no-sessions", "no-members", "no-panel-cells", "table-not-built", "meta-missing"],
)
def test_rejection_details_do_not_expose_the_equity_root(
    degraded_root: Path,
    phrase: str,
    query: Callable[[EquityDuckdbAdapter], RawObservationSet | ResearchPanelResult],
) -> None:
    """질의 거절 상세는 preview·trace 422 와 패널 미리보기 200 본문으로 그대로 나간다 (#163).

    루트를 다시 붙여도 테스트가 통과하던 문장들이다(#275 리뷰 P2-1). 나머지 사용자 대면 문장의
    루트 부재는 그 문장을 만드는 테스트가 함께 본다.
    """
    detail = query(EquityDuckdbAdapter(degraded_root)).detail
    assert detail is not None and phrase in detail
    assert str(degraded_root.resolve()) not in detail and "root=" not in detail


def test_preview_and_trace_rejections_do_not_expose_the_equity_root(root: Path) -> None:
    """preview·trace 의 422 `portfolio.data.unavailable` 상세에 서버 절대 경로가 없다 (#163).

    예전에는 어댑터 detail 의 `root=<절대 경로>` 가 그대로 나가 사용자가 서버 디렉터리·계정명을
    봤다. 가리는 것은 run `error` 뿐이었다 — 이제 어댑터가 처음부터 경로 없이 쓴다.
    """
    client = TestClient(build_http_app(equity_adapter="duckdb", equity_root=root))
    spec = client.get("/api/v1/strategies/template").json()
    # 픽스처 달력(2023-12-26..2024-01-12) 밖 — 어댑터가 커버리지 밖 NO_DATA 로 답한다
    environment = {"start": "2024-02-01", "end": "2024-02-29", "universe_id": "krx.common-stock"}
    preview = client.post(
        "/api/v1/portfolio/preview", json={"spec": spec, "environment": environment}
    )
    trace = client.post(
        "/api/v1/strategies/debug/trace",
        json={
            "strategy_source": {"kind": "inline_draft", "spec": spec},
            "environment": environment,
            "security_ids": ["005930:1"],
            "factor_id": spec["factors"][0]["factor_id"],
        },
    )
    for response in (preview, trace):
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert (detail["code"], detail["status"]) == ("portfolio.data.unavailable", "no_data")
        assert "outside coverage" in detail["detail"]
        assert str(root.resolve()) not in detail["detail"] and "root=" not in detail["detail"]


def _environment() -> RunEnvironment:
    """실행 설정은 1.2 부터 요청이 싣는다(P2-03)."""
    return RunEnvironment(start=START, end=END, universe_id="krx.common-stock")


def _momentum_spec(field_id: str) -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    return replace(
        template,
        factors=(
            FactorSignal(
                factor_id="mom_3",
                label="3세션 모멘텀",
                direction=FactorDirection.HIGH,
                weight=1.0,
                graph=FactorGraph(
                    nodes=(
                        FieldNode("px", field_id, "field"),
                        TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "px", 3, "time_series"),
                    ),
                    output_node_id="mom",
                ),
            ),
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
        result = service.run_pipeline(
            PortfolioPreviewRequest(_momentum_spec(field_id), environment=_environment())
        )
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


def test_raw_load_reports_monotonic_progress_ending_at_one(adapter: EquityDuckdbAdapter) -> None:
    """이슈 #162: 실데이터 원시 로딩(격자 조립 + 관측 조립 + 검증)은 수십 초라 진행을 보고한다.

    격자 구간(0~0.52)과 생성 시 계약 검증 구간(0.91~1.0) 안에서도 오르고 1.0 에서 끝난다.
    보고 유무가 결과를 바꾸지 않는다.
    """
    query = RawObservationQuery("KRX", "krx.common-stock", START, END, PRICE_FIELDS, 0)
    reported: list[float] = []

    result = adapter.load_raw_observations_reporting(
        query, checkpoint=lambda: None, progress=reported.append
    )

    baseline = adapter.load_raw_observations(query)
    assert result.observations == baseline.observations
    assert result.sessions == baseline.sessions
    assert reported == sorted(reported)
    assert any(0.0 < fraction < 0.52 for fraction in reported)
    assert any(0.91 < fraction < 1.0 for fraction in reported)
    assert reported[-1] == 1.0


class _Cancelled(Exception):
    """테스트의 취소 예외 — 어댑터는 checkpoint 가 던진 예외를 종류와 무관하게 그대로 올린다."""


def test_cancellation_while_reading_latest_sources_stops_before_the_panel_is_built(
    adapter: EquityDuckdbAdapter,
) -> None:
    """이슈 #160: 격자를 다 읽은 뒤 법인 대응·LATEST 원천(재무)을 읽는 동안 취소하면 패널 구체화가
    끝나기 전에 멈춘다. 예전에는 이 구간에 checkpoint 가 없어 패널을 다 만든 뒤에야 취소를 봤다.

    구간은 진행 보고로 가른다 — 격자가 끝나면 0.9 × 0.52, 패널 구체화가 끝나면 0.52 를 보고한다.
    """
    query = RawObservationQuery(
        "KRX", "krx.common-stock", START, END, ("price.close", "financial.book_equity"), 0
    )
    cancelled = Event()
    reported: list[float] = []

    def progress(fraction: float) -> None:
        reported.append(fraction)
        if fraction >= 0.45:  # 격자 끝(0.468). 격자 안 보고는 이보다 작다
            cancelled.set()

    def checkpoint() -> None:
        if cancelled.is_set():
            raise _Cancelled

    with pytest.raises(_Cancelled):
        adapter.load_raw_observations_reporting(query, checkpoint=checkpoint, progress=progress)

    assert reported[-1] == pytest.approx(0.9 * 0.52)


def test_every_query_of_a_raw_load_watches_the_callers_checkpoint(
    adapter: EquityDuckdbAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#284 리뷰 P3-4: 격자·법인 대응·LATEST 질의가 모두 호출자의 checkpoint 를 보며 돈다.

    픽스처 질의는 감시 간격(0.1초)보다 빨리 끝나 실제로 끊기지 않는다. 그래서 질의마다
    `_fetchall` 에 넘어간 checkpoint 를 직접 대조한다. 하나라도 no-op 으로 바뀌면 실원장에서 그
    질의(6개월 약 0.6초) 동안 취소가 늦어진다.
    """
    import duckdb

    passed: list[Callable[[], None]] = []

    def recording(
        con: duckdb.DuckDBPyConnection,
        sql: str,
        params: list[object],
        checkpoint: Callable[[], None],
    ) -> list[tuple[object, ...]]:
        passed.append(checkpoint)
        return _fetchall(con, sql, params, checkpoint)

    monkeypatch.setattr(
        "strategy_workbench.adapters.outbound.equity_duckdb._adapter._fetchall", recording
    )

    def checkpoint() -> None:
        pass

    adapter.load_raw_observations_cancellable(
        RawObservationQuery(
            "KRX", "krx.common-stock", START, END, ("price.close", "financial.book_equity"), 0
        ),
        checkpoint=checkpoint,
    )

    # 격자 · 법인 대응(재무는 법인 축) · 재무 LATEST — 틀리면 몇 번째 질의인지 보인다
    assert passed == [checkpoint] * 3


def test_a_duckdb_query_cancelled_while_running_is_interrupted_into_the_callers_error() -> None:
    """이슈 #160: 질의 하나는 나눌 수 없어 행 단위 checkpoint 가 닿지 않는다. 도는 동안 취소되면
    감시 스레드가 `interrupt()` 로 끊고, duckdb 오류 대신 호출자의 취소 예외가 올라간다 — duckdb
    오류가 그대로 새면 취소한 run 이 내부 오류(`backtest.run.internal`)를 달고 끝나고, trace 요청은
    499 대신 500 이 된다.
    """
    import duckdb

    cancelled = Event()

    def checkpoint() -> None:
        if cancelled.is_set():
            raise _Cancelled

    con = duckdb.connect()
    # 질의를 시작할 때 이미 취소돼 있다. 호출 스레드는 질의 안에서 checkpoint 를 부르지 못하므로
    # 감시 스레드의 첫 폴링이 본다.
    cancelled.set()
    try:
        with pytest.raises(_Cancelled) as info:
            # 이 기계에서 끊지 않으면 약 30초 걸리는 질의다. 폴링 간격(0.1초)보다 훨씬 길다.
            _fetchall(
                con,
                "SELECT sum(a.range * b.range) FROM range(0, 60000) a CROSS JOIN range(0, 60000) b",
                [],
                checkpoint,
            )
    finally:
        con.close()

    assert isinstance(info.value.__context__, duckdb.Error)


class _PendingConnection:
    """질의가 `interrupt()` 를 받을 때까지 돌다가 주어진 duckdb 오류로 끝나는 연결 대역.

    `lost` 는 질의가 시작되기 전에 와서 사라지는 interrupt 수다 — duckdb 는 시작 전 interrupt 를
    버리고 질의를 끝까지 돌린다.
    """

    def __init__(self, error: Exception, *, lost: int = 0) -> None:
        self._error = error
        self._needed = lost + 1
        self._calls = 0
        self._interrupts = Condition()

    def execute(self, sql: str, params: list[object]) -> None:
        with self._interrupts:
            if not self._interrupts.wait_for(lambda: self._calls >= self._needed, timeout=30):
                raise TimeoutError(
                    f"query was never interrupted after it started — sql={sql!r} "
                    f"interrupts={self._calls} needed={self._needed}"
                )
        raise self._error

    def interrupt(self) -> None:
        with self._interrupts:
            self._calls += 1
            self._interrupts.notify_all()


@pytest.mark.parametrize(
    "error_type",
    ["InterruptException", "InvalidInputException"],
)
def test_an_interrupted_query_becomes_the_callers_error_whatever_duckdb_raises(
    error_type: str,
) -> None:
    """이슈 #160: 끊긴 질의는 끊긴 단계에 따라 `InterruptException` 이나 `InvalidInputException`
    ("Attempting to execute an unsuccessful or closed pending query result" + "INTERRUPT Error")으로
    온다(실원장 격자 질의에서 뒤의 것을 봤다). 어느 쪽이든 호출자의 취소 예외가 올라간다.
    """
    import duckdb

    error = getattr(duckdb, error_type)("INTERRUPT Error: Interrupted!")

    def checkpoint() -> None:
        raise _Cancelled

    with pytest.raises(_Cancelled) as info:
        _fetchall(
            cast("duckdb.DuckDBPyConnection", _PendingConnection(error)), "SELECT 1", [], checkpoint
        )

    assert info.value.__context__ is error


def test_an_interrupt_lost_before_the_query_starts_is_sent_again() -> None:
    """이슈 #160: duckdb 는 질의가 시작되기 전에 온 `interrupt()` 를 버리고 질의를 끝까지 돌린다.
    감시 스레드가 한 번 끊고 멈추면 그 사이에 시작한 질의는 끊기지 않으므로, 호출이 끝날 때까지 다시
    끊는다.
    """
    import duckdb

    error = duckdb.InterruptException("INTERRUPT Error: Interrupted!")

    def checkpoint() -> None:
        raise _Cancelled

    with pytest.raises(_Cancelled) as info:
        _fetchall(
            cast("duckdb.DuckDBPyConnection", _PendingConnection(error, lost=1)),
            "SELECT 1",
            [],
            checkpoint,
        )

    assert info.value.__context__ is error
