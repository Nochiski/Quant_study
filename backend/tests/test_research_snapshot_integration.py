"""합성 원장의 연구 보존 root를 실제 DuckDB 어댑터로 다시 연다."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
    EquityDuckdbAdapter,
    EquityDuckdbSetupError,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from tests.equity_fixture import build_workbench_root, import_ledger_module


def _source(tmp_path: Path) -> Path:
    root = build_workbench_root(tmp_path / "source", evening_session=date(2024, 1, 12))
    layout = import_ledger_module("ledger_sync.layout")
    hashing = import_ledger_module("ledger_sync.hashing")
    catalog = import_ledger_module("equity.catalog")
    views = import_ledger_module("equity.views")
    with duckdb.connect() as connection:
        for path in sorted(root.glob("*/MANIFEST.json")):
            document = json.loads(path.read_bytes())
            build = layout.parse_manifest(path.parent.name, path.read_bytes()).current
            for raw, partition in zip(
                document["builds"][0]["partitions"], build.partitions, strict=True,
            ):
                raw["content_hash"] = hashing.compute_partition_hash(
                    connection, path.parent / partition.path, partition)
            path.write_text(json.dumps(document), encoding="utf-8")
    catalog.write_catalog(root, views.render_macros(root)[0])
    return root


def test_snapshot_adapter_retains_observations_identity_and_contract_after_gc(
    tmp_path: Path,
) -> None:
    snapshots = import_ledger_module("equity.research_snapshot")
    source = _source(tmp_path)
    frozen = tmp_path / "frozen"
    before = EquityDuckdbAdapter(source)
    query = RawObservationQuery("KRX", "krx.common-stock", date(2024, 1, 2), date(2024, 1, 5),
                                ("price.close", "price.adj_close"), 0)
    expected = before.load_raw_observations(query)
    assert expected.ok, expected.detail
    assert expected.observations
    result = snapshots.create_snapshot(
        source, frozen, repo_root=Path(__file__).resolve().parents[2])
    assert result.ok, result.detail
    retained = EquityDuckdbAdapter(frozen)
    assert retained.snapshot().snapshot_id == before.snapshot().snapshot_id
    assert retained._field_contract() == before._field_contract()
    assert retained.load_raw_observations(query) == expected
    # 원본 current 교체와 옛 판본 GC를 흉내 낸다. 재접속은 원본 카탈로그·parquet에 기대면 실패한다.
    for table in source.iterdir():
        if not table.is_dir() or table.name.startswith("_"):
            continue
        path = table / "MANIFEST.json"
        document = json.loads(path.read_bytes())
        old_build = document["current_build"]
        shutil.copytree(table / f"v={old_build}", table / "v=b_new")
        document["current_build"] = "b_new"
        document["builds"][0]["build_id"] = "b_new"
        for part in document["builds"][0]["partitions"]:
            part["path"] = part["path"].replace(f"v={old_build}", "v=b_new", 1)
        path.write_text(json.dumps(document), encoding="utf-8")
        shutil.rmtree(table / f"v={old_build}")
    checked = snapshots.verify_snapshot(frozen)
    assert checked.ok, checked.detail
    reopened = EquityDuckdbAdapter(checked.root)
    assert reopened.snapshot().snapshot_id == retained.snapshot().snapshot_id
    assert reopened.load_raw_observations(query) == expected
    # 카탈로그 SQL을 그대로 재사용하되 경로는 보존 root만 가리켜야 한다.
    with duckdb.connect(str(frozen / "equity.duckdb"), read_only=True) as connection:
        bodies = connection.execute(
            "SELECT macro_definition FROM duckdb_functions() WHERE function_type='table_macro' "
            "AND NOT internal").fetchall()
    assert bodies
    assert all(str(source.resolve()) not in str(body) for body in bodies)
    assert any(str(frozen.resolve()) in str(body) for body in bodies)


def test_pending_snapshot_is_refused_even_with_complete_tables(tmp_path: Path) -> None:
    root = _source(tmp_path)
    (root / "_research_snapshot.pending").touch()
    with pytest.raises(EquityDuckdbSetupError, match="publication pending"):
        EquityDuckdbAdapter(root)
