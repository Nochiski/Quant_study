"""duckdb 어댑터의 stage 참조 원천(#608).

stage 표는 equity 로 승격하지 않고 어댑터가 equity 루트 옆 `stage/` 에서 읽기만 한다. 손 픽스처
(`tests/equity_fixture.py` 의 `build_stage_root`)로 다음을 본다.

- stage 루트가 없으면 stage 필드만 빠지고 사유가 조치를 말한다. equity 필드·원장 판은 그대로다.
- 랙은 선언이 정본이라 폴백 경고·근거에 `fallback` 이 없다. stage 판은 스냅샷 id 뒷부분에 실린다.
- 매트릭스: 그날 수집분에서 수집 달 이후 가장 가까운 결산기를 고르고, 수집이 없는 세션은 결측이다.
- 연간 추정: 그날 수집분에 당해 12월기 추정이 없으면 옛 수집분 값을 잇지 않고 결측이다.
- WISE 비율: 추정(E) 열이 아닌 마지막 12월 열, 접힌 판의 값이 다음 판까지 이어진다.
- 키움 사모: 행이 없는 날은 0 이 아니라 결측이다.
- 행 경로(`load_raw_observations`)와 열 경로(`load_raw_panel`)가 같은 셀을 낸다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
    SourceLayer,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValueType,
    RawObservationQuery,
    RawPanel,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind
from tests.equity_fixture import (
    WB_STAGE_ESTIMATE_COLUMN,
    WB_STAGE_KIWOOM_ROWS,
    build_stage_root,
    build_workbench_root,
    wb_stage_matrix_value,
)

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (  # noqa: E402
    EquityDuckdbAdapter,
)

STAGE_FIELDS = tuple(
    field_id
    for field_id, spec in FIELD_BY_ID.items()
    if SOURCE_BY_NAME[spec.source].layer is SourceLayer.STAGE
)
SECURITY = "005930:1"


@pytest.fixture
def adapter(tmp_path: Path) -> EquityDuckdbAdapter:
    root = build_workbench_root(tmp_path / "equity")
    build_stage_root(root)
    return EquityDuckdbAdapter(root)


def _cells(
    adapter: EquityDuckdbAdapter, start: date, end: date, fields: tuple[str, ...]
) -> dict[tuple[date, str, str], tuple[RawFieldValueType, date, CellKind]]:
    result = adapter.load_raw_observations(
        RawObservationQuery("KRX", "krx.all", start, end, fields, 0)
    )
    assert result.status.value == "ok", result.detail
    return {
        (observation.as_of, observation.security_id, field.field_id): (
            field.value,
            field.available_date,
            field.kind,
        )
        for observation in result.observations
        for field in observation.fields
    }


def test_stage_루트가_없으면_stage_필드만_빠지고_조치를_말한다(tmp_path: Path) -> None:
    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))

    served = {profile.field_id for profile in adapter.list_fields()}
    reasons = adapter.unavailable_factor_fields()

    assert not served & set(STAGE_FIELDS)
    assert "price.close" in served
    assert {field_id: "stage_table_missing" in reasons[field_id] for field_id in STAGE_FIELDS} == {
        field_id: True for field_id in STAGE_FIELDS
    }
    assert all("ledger_sync --layer stage" in reasons[field_id] for field_id in STAGE_FIELDS)


def test_stage_필드는_선언_랙을_정본으로_내고_폴백_경고를_내지_않는다(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = build_workbench_root(tmp_path / "equity")
    build_stage_root(root)

    with caplog.at_level("WARNING"):
        profiles = {
            profile.field_id: profile for profile in EquityDuckdbAdapter(root).list_fields()
        }

    assert set(STAGE_FIELDS) <= set(profiles)
    assert {profiles[f].recommended_lag_sessions for f in STAGE_FIELDS} == {1}
    assert not [f for f in STAGE_FIELDS if "fallback" in profiles[f].available_date_basis]
    assert not [r for r in caplog.records if "profile_lag_fallback" in r.getMessage()]
    assert profiles["wise.consensus_op"].coverage.starts_on == date(2023, 12, 28)


def test_stage_판은_스냅샷_id_뒷부분에만_실린다(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity")
    without = EquityDuckdbAdapter(root).snapshot().snapshot_id
    build_stage_root(root)
    with_stage = EquityDuckdbAdapter(root).snapshot()

    ledger_without, contract_without = without.split(":")
    ledger_with, contract_with = with_stage.snapshot_id.split(":")
    assert ledger_with == ledger_without  # 카탈로그 meta·EG-C 와 대조하는 원장 판은 그대로
    assert contract_with != contract_without
    assert {"stg_consensus_matrix", "stg_flow_daily_kiwoom"} <= {
        revision.dataset_id for revision in with_stage.dataset_revisions
    }


def test_매트릭스는_전_세션_수집분에서_가장_가까운_결산기를_고른다(
    adapter: EquityDuckdbAdapter,
) -> None:
    fields = ("wise.consensus_op", "wise.consensus_op_1w_ago", "wise.consensus_ni_3m_ago")
    cells = _cells(adapter, date(2023, 12, 29), date(2024, 1, 8), fields)

    # 12-28 수집분(2023-12) → 2023/12 기, 랙 1 이라 12-29 세션에 보인다
    assert cells[(date(2023, 12, 29), SECURITY, "wise.consensus_op")] == (
        wb_stage_matrix_value("121500", "202312", "current", date(2023, 12, 28)),
        date(2023, 12, 28),
        CellKind.OBSERVED,
    )
    # 01-03 수집분(2024-01) → 2023/12 는 끝난 기라 2024/12 기
    assert cells[(date(2024, 1, 4), SECURITY, "wise.consensus_op_1w_ago")][0] == (
        wb_stage_matrix_value("121500", "202412", "1w", date(2024, 1, 3))
    )
    assert cells[(date(2024, 1, 5), SECURITY, "wise.consensus_ni_3m_ago")][0] == (
        wb_stage_matrix_value("122710", "202412", "3m", date(2024, 1, 4))
    )
    # 12-29·01-05 에는 수집이 없다 — 다음 세션 셀이 비고, 토요일(01-06) 수집분은 세션이 아니다
    assert (date(2024, 1, 2), SECURITY, "wise.consensus_op") not in cells
    assert (date(2024, 1, 8), SECURITY, "wise.consensus_op") not in cells


def test_연간_추정은_그날_수집분에_없으면_옛_값을_잇지_않는다(
    adapter: EquityDuckdbAdapter,
) -> None:
    cells = _cells(
        adapter,
        date(2023, 12, 29),
        date(2024, 1, 5),
        ("wise.fy0_op_estimate", "wise.fy0_ni_estimate"),
    )

    assert cells[(date(2023, 12, 29), SECURITY, "wise.fy0_op_estimate")][0] == 900.0  # 2023/12 E
    assert cells[(date(2024, 1, 4), SECURITY, "wise.fy0_ni_estimate")][0] == 460.0  # 2024/12 E
    # 01-04 수집분에는 2024/12 추정이 없다 — 01-03 수집분 값을 잇지 않는다
    assert (date(2024, 1, 5), SECURITY, "wise.fy0_op_estimate") not in cells


def test_WISE_비율은_마지막_실적_12월_열을_다음_판까지_잇는다(
    adapter: EquityDuckdbAdapter,
) -> None:
    fields = ("wise.per", "wise.pbr", "wise.dividend_yield")
    cells = _cells(adapter, date(2023, 12, 28), date(2024, 1, 8), fields)

    assert cells[(date(2023, 12, 28), SECURITY, "wise.per")] == (
        10.0,
        date(2023, 12, 27),
        CellKind.OBSERVED,
    )
    assert cells[(date(2024, 1, 4), SECURITY, "wise.pbr")][0] == 1.5  # 01-04 판은 01-05 부터
    assert cells[(date(2024, 1, 5), SECURITY, "wise.dividend_yield")][0] == 2.1
    assert WB_STAGE_ESTIMATE_COLUMN not in {cell[0] for cell in cells.values()}


def test_키움_사모는_행이_없는_날을_0_이_아니라_결측으로_낸다(
    adapter: EquityDuckdbAdapter,
) -> None:
    cells = _cells(adapter, date(2024, 1, 3), date(2024, 1, 5), ("kiwoom.private_fund_net_buy",))
    expected = {row[1]: row[2] for row in WB_STAGE_KIWOOM_ROWS if row[0] == "005930"}

    assert (
        cells[(date(2024, 1, 3), SECURITY, "kiwoom.private_fund_net_buy")][0]
        == expected[date(2024, 1, 2)]
    )
    # 01-03 행이 없다 — 01-04 세션 셀은 결측이다(0 이 아니다)
    missing = cells.get((date(2024, 1, 4), SECURITY, "kiwoom.private_fund_net_buy"))
    assert missing is None or (missing[0] is None and missing[2] is CellKind.MISSING)
    assert cells[(date(2024, 1, 5), "000660:1", "kiwoom.private_fund_net_buy")][0] == -5_000_000.0


def test_stage_필드도_행_경로와_열_경로가_같은_셀을_낸다(adapter: EquityDuckdbAdapter) -> None:
    query = RawObservationQuery(
        "KRX", "krx.all", date(2023, 12, 28), date(2024, 1, 12), (*STAGE_FIELDS, "price.close"), 2
    )
    reference = RawPanel.from_observation_set(
        adapter.load_raw_observations(query), field_ids=query.field_ids
    )

    columnar = adapter.load_raw_panel(query)

    assert list(columnar.observations()) == list(reference.observations())
