"""duckdb 어댑터의 WISE 3표·사모 순매수 필드(원장 S26, #608).

손 픽스처(`tests/equity_fixture.py` 의 `build_workbench_root`)로 다음을 본다.

- WISE 표가 없는 옛 루트에서는 그 필드만 빠지고 사유가 조치(원장 받기)를 말한다.
- 매트릭스: 그날 수집분에서 수집 달 이후 가장 가까운 결산기를 고르고, 수집이 없는 세션은 결측이다.
- 연간 추정: 그날 수집분에 당해 12월기 추정이 없으면 옛 수집분 값을 잇지 않고 결측이다.
- WISE 비율: 추정(E) 열이 아닌 마지막 12월 열, 접힌 판의 값이 다음 판까지 이어진다.
- 사모 순매수: 수급 표의 사모 열을 다른 순매수처럼 읽고 행이 없는 날은 0 이 아니라 결측이다.
- 행 경로(`load_raw_observations`)와 열 경로(`load_raw_panel`)가 같은 셀을 낸다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._source import LEDGER_SYNC
from strategy_workbench.adapters.outbound.equity_duckdb._specs import SOURCE_BY_NAME
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValueType,
    RawObservationQuery,
    RawPanel,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind
from tests.equity_fixture import (
    WB_SPLIT_DATE,
    WB_WISE_ESTIMATE_COLUMN,
    WB_WISE_FIELDS,
    build_workbench_root,
    wb_wise_matrix_value,
)

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (  # noqa: E402
    EquityDuckdbAdapter,
)

SECURITY = "005930:1"


@pytest.fixture(scope="module")
def adapter(tmp_path_factory: pytest.TempPathFactory) -> EquityDuckdbAdapter:
    return EquityDuckdbAdapter(build_workbench_root(tmp_path_factory.mktemp("wise") / "equity"))


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


def test_WISE_표가_없는_루트는_그_필드만_빼고_조치를_말한다(tmp_path: Path) -> None:
    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity", wise=False))

    served = {profile.field_id for profile in adapter.list_fields()}
    reasons = adapter.unavailable_factor_fields()

    assert not served & set(WB_WISE_FIELDS)
    assert {"price.close", "flow.private_fund_net_buy"} <= served
    assert all("table_missing" in reasons[field_id] for field_id in WB_WISE_FIELDS)
    assert all(LEDGER_SYNC in reasons[field_id] for field_id in WB_WISE_FIELDS)


def test_WISE_필드는_대장_랙을_읽고_커버_시작은_첫_수집일이다(
    adapter: EquityDuckdbAdapter,
) -> None:
    profiles = {profile.field_id: profile for profile in adapter.list_fields()}

    assert {profiles[f].recommended_lag_sessions for f in WB_WISE_FIELDS} == {1}
    assert not [f for f in WB_WISE_FIELDS if "fallback" in profiles[f].available_date_basis]
    assert profiles["wise.consensus_op"].coverage.starts_on == date(2023, 12, 28)
    assert profiles["wise.fy0_op_estimate"].coverage.starts_on == date(2023, 12, 28)
    assert profiles["wise.per"].coverage.starts_on == date(2023, 12, 27)
    assert {profiles[f].dataset_id for f in WB_WISE_FIELDS} == {
        "consensus_matrix",
        "consensus_annual",
        "fin_ratio_wise",
    }


def test_매트릭스는_전_지식_세션_수집분에서_가장_가까운_결산기를_고른다(
    adapter: EquityDuckdbAdapter,
) -> None:
    fields = ("wise.consensus_op", "wise.consensus_op_1w_ago", "wise.consensus_ni_3m_ago")
    cells = _cells(adapter, date(2023, 12, 29), date(2024, 1, 8), fields)

    # 12-28 장 뒤 수집분(2023-12) → 2023/12 기, 지식 세션 12-28 · 랙 1 이라 12-29 세션에 보인다
    assert cells[(date(2023, 12, 29), SECURITY, "wise.consensus_op")] == (
        wb_wise_matrix_value("121500", "202312", "current", date(2023, 12, 28)),
        date(2023, 12, 28),
        CellKind.OBSERVED,
    )
    # 01-03 장 전 수집분은 01-02 지식이라 그날(01-03) 세션에 보인다.
    # 2023/12 는 끝난 기라 2024/12 기다
    assert cells[(date(2024, 1, 3), SECURITY, "wise.consensus_op_1w_ago")] == (
        wb_wise_matrix_value("121500", "202412", "1w", date(2024, 1, 3)),
        date(2024, 1, 2),
        CellKind.OBSERVED,
    )
    assert cells[(date(2024, 1, 5), SECURITY, "wise.consensus_ni_3m_ago")][0] == (
        wb_wise_matrix_value("122710", "202412", "3m", date(2024, 1, 4))
    )
    # 01-05(금) 지식 수집분이 둘(토 01-06 · 월 01-08 장 전) — 01-08 세션은 늦은 01-08 수집분
    assert cells[(date(2024, 1, 8), SECURITY, "wise.consensus_op")][0] == (
        wb_wise_matrix_value("121500", "202412", "current", date(2024, 1, 8))
    )
    # 12-29·01-03 지식 세션에는 판이 통째로 없다 — 직전 판(12-28·01-02 지식)을 잇는다(한도 5 세션)
    assert cells[(date(2024, 1, 2), SECURITY, "wise.consensus_op")] == (
        wb_wise_matrix_value("121500", "202312", "current", date(2023, 12, 28)),
        date(2023, 12, 28),
        CellKind.OBSERVED,
    )
    assert cells[(date(2024, 1, 4), SECURITY, "wise.consensus_op")][1] == date(2024, 1, 2)


def test_WISE_판_잇기는_한도_세션까지만이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """판이 통째로 없는 세션은 한도 안에서만 직전 판을 잇는다 — 한도 1 이면 두 세션째부터 빈다."""
    for name in [n for n in SOURCE_BY_NAME if n.startswith("wise_matrix_")]:
        monkeypatch.setitem(SOURCE_BY_NAME, name, replace(SOURCE_BY_NAME[name], carry_sessions=1))
    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))
    cells = _cells(adapter, date(2024, 1, 8), date(2024, 1, 12), ("wise.consensus_op",))

    # 마지막 판은 01-05(금) 지식 — 01-08 세션은 그 판, 01-09 는 한 세션 이은 판, 01-10 부터는 빈다
    assert cells[(date(2024, 1, 8), SECURITY, "wise.consensus_op")][1] == date(2024, 1, 5)
    assert cells[(date(2024, 1, 9), SECURITY, "wise.consensus_op")][1] == date(2024, 1, 5)
    assert (date(2024, 1, 10), SECURITY, "wise.consensus_op") not in cells


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
    assert (
        cells[(date(2024, 1, 3), SECURITY, "wise.fy0_ni_estimate")][0] == 460.0
    )  # 장 전 수집, 그날
    # 01-04 수집분에는 2024/12 추정이 없다 — 판은 있으니 01-03 수집분 값을 잇지 않는다
    # (판 단위로만 잇는다)
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
    assert cells[(date(2024, 1, 8), SECURITY, "wise.per")][0] == 11.0  # 다음 판이 올 때까지 잇는다
    assert WB_WISE_ESTIMATE_COLUMN not in {cell[0] for cell in cells.values()}


def test_사모_순매수는_키움을_고르고_행이_없는_날을_0_이_아니라_결측으로_낸다(
    adapter: EquityDuckdbAdapter,
) -> None:
    cells = _cells(adapter, date(2024, 1, 9), date(2024, 1, 12), ("flow.private_fund_net_buy",))

    # 01-08 은 키움·KIS 두 행 — 키움 값(2,000,000)이다. 랙 1 이라 01-09 세션에 보인다
    assert cells[(date(2024, 1, 9), SECURITY, "flow.private_fund_net_buy")] == (
        2_000_000.0,
        WB_SPLIT_DATE,
        CellKind.OBSERVED,
    )
    # 키움이 없는 셀은 KIS 단독 행이 채운다(다른 순매수와 같은 합집합)
    assert cells[(date(2024, 1, 9), "000660:1", "flow.private_fund_net_buy")][0] == -700_000.0
    # 01-09 는 원천이 행을 뺀 날(src_omitted) — 0 이 아니라 결측이다
    assert cells[(date(2024, 1, 10), SECURITY, "flow.private_fund_net_buy")][0] is None
    assert cells[(date(2024, 1, 10), SECURITY, "flow.private_fund_net_buy")][2] is CellKind.MISSING
    # 01-11 의 진짜 0 은 0 이다
    assert cells[(date(2024, 1, 12), SECURITY, "flow.private_fund_net_buy")][0] == 0.0


def test_WISE_필드도_행_경로와_열_경로가_같은_셀을_낸다(adapter: EquityDuckdbAdapter) -> None:
    query = RawObservationQuery(
        "KRX",
        "krx.all",
        date(2023, 12, 28),
        date(2024, 1, 12),
        (*WB_WISE_FIELDS, "flow.private_fund_net_buy", "price.close"),
        2,
    )
    reference = RawPanel.from_observation_set(
        adapter.load_raw_observations(query), field_ids=query.field_ids
    )

    columnar = adapter.load_raw_panel(query)

    assert list(columnar.observations()) == list(reference.observations())
