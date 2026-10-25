"""실행 설정 유니버스 선택지 계약.

두 equity 어댑터(mock·duckdb)가 같은 규칙으로 `list_universes` 를 답한다.

선택지는 실행이 받는 유니버스 id 와 같은 집합이어야 한다 — 목록에 있는 id 는 그날 구성 종목을 주고,
목록에 없는 id 는 받지 않는다(빈 튜플). 순서는 id 오름차순이고 시장은 어댑터의 시장이다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Protocol

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.domain.equity.facade.research_data import SecurityRef, UniverseDefinition


class _UniverseAdapter(Protocol):
    """계약이 읽는 두 메서드. 두 어댑터가 같은 모양으로 구현한다."""

    def list_universes(self) -> tuple[UniverseDefinition, ...]: ...

    def universe_securities(
        self, market: str, universe_id: str, as_of: date
    ) -> tuple[SecurityRef, ...]: ...


def _mock(_: Path) -> tuple[_UniverseAdapter, date]:
    return MockEquityDataAdapter.demo(), date(2024, 1, 2)


def _duckdb(tmp_path: Path) -> tuple[_UniverseAdapter, date]:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    return EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity")), date(2024, 1, 2)


@pytest.mark.parametrize("build", [_mock, _duckdb], ids=["mock", "duckdb"])
def test_listed_universes_are_exactly_the_ids_the_run_accepts(
    build: Callable[[Path], tuple[_UniverseAdapter, date]], tmp_path: Path
) -> None:
    adapter, as_of = build(tmp_path)
    universes = adapter.list_universes()

    ids = [item.universe_id for item in universes]
    assert ids, "어댑터가 고를 유니버스를 하나도 답하지 않았다"
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert {item.market for item in universes} == {"KRX"}
    assert all(item.label and item.description for item in universes)
    for item in universes:
        members = adapter.universe_securities(item.market, item.universe_id, as_of)
        assert members, f"목록의 유니버스가 구성 종목을 안 준다 — id={item.universe_id}"
    assert adapter.universe_securities("KRX", "krx.not-listed", as_of) == ()
