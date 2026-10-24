"""종목 id → 이름·티커 계약(`SecurityDirectoryPort.describe_securities`).

두 equity 어댑터(mock·duckdb)가 같은 규칙으로 답한다: 그날 유니버스가 준 종목은 id 로 다시 찾아도
같은 이름·티커이고, 모르거나 형식이 틀린 id 는 지어내지 않고 빠지며, 결과는 id 오름차순이다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.ports import SecurityDirectoryPort

AS_OF = date(2024, 1, 2)
UNIVERSE = "krx.common-stock"


def _mock(_: Path) -> SecurityDirectoryPort:
    return MockEquityDataAdapter.demo()


def _duckdb(tmp_path: Path) -> SecurityDirectoryPort:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    return EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))


@pytest.mark.parametrize("build", [_mock, _duckdb], ids=["mock", "duckdb"])
def test_describe_securities_matches_universe_names_and_drops_unknown_ids(
    build: Callable[[Path], SecurityDirectoryPort], tmp_path: Path
) -> None:
    adapter = build(tmp_path)
    members = adapter.universe_securities("KRX", UNIVERSE, AS_OF)
    assert members, "계약 fixture 유니버스가 비었다"

    requested = [item.security_id for item in reversed(members)]
    described = adapter.describe_securities([*requested, "000000:9", "not-an-id", requested[0]])

    assert described == tuple(sorted(members, key=lambda item: item.security_id))
    assert adapter.describe_securities([]) == ()
