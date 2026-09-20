"""S25 `sector_snapshot` + 매크로 `v_sector` — stage 표를 실제로 지어 equity 왕복 (플랜 wics-weekly T3).

stage 입력은 `test_stage_wics` 의 원문 픽스처(2026-09-18 실측 모양)로 두 스냅샷(09-11·09-18)을 짓는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
from test_stage_wics import AT, _body, _fixtures, _row, _write_wics

from equity import build, rules_s25, views
from equity.baseline import Baseline
from equity.gates import GateStatus
from equity.model import RULES
from stage import build as stage_build
from stage import rules as stage_rules
from stage import snapshot

D1, D2 = "20260911", "20260918"


def _rows(dt: str, extra_l1_only: bool = False) -> list[tuple]:
    l1 = [_row("006400", "G45", "WICS IT", "G45", "IT"), _row("034220", "G45", "WICS IT", "G45", "IT"),
          _row("005930", "G45", "WICS IT", "G45", "IT")]
    rows = [
        (dt, "G45", AT, 200, 3, _body(l1)),
        (dt, "G4535", AT, 200, 1, _body([_row("006400", "G4535", "WICS 전자와 전기제품", "G45", "IT")])),
        (dt, "G4540", AT, 200, 1, _body([_row("034220", "G4540", "WICS 디스플레이", "G45", "IT")])),
        (dt, "G4530", AT, 200, 1, _body([_row("005930", "G4530", "WICS 반도체와반도체장비", "G45", "IT",
                                             mkt_val=3_500_000_000, shr=5_969_782_550)])),
    ]
    if extra_l1_only:   # L1 에는 있는데 어느 L2 에도 없는 종목 — EG3 가 잡아야 한다
        rows.append((dt, "G10", AT, 200, 1, _body([_row("096770", "G10", "WICS 에너지", "G10", "에너지")])))
    return rows


def _stage(tmp_path: Path, *, extra_l1_only: bool = False) -> Path:
    raw = tmp_path / "raw"; raw.mkdir()
    _write_wics(raw / "wiseindex.db", _rows(D1) + _rows(D2, extra_l1_only))
    snap = snapshot.make_snapshot({"wiseindex": raw / "wiseindex.db"}, tmp_path / "snapshots", snapshot_id="s")
    stage_root = tmp_path / "stage"
    r = stage_build.build_table(stage_rules.RULES["stg_wics_components"], snap, stage_root, fixtures_path=_fixtures(tmp_path))
    assert r.ok, [g for g in r.gates if g.status.value == "fail"]
    return stage_root


def _equity_fixtures(tmp_path: Path) -> Path:
    """EG4 — equity 는 픽스처 부재가 실패다. 합성 stage 값에 맞춘 테스트용 픽스처(운영 픽스처는 src/equity/fixtures)."""
    fx = tmp_path / "sector_fixtures.json"
    fx.write_text(json.dumps([
        {"id": "T-25-a", "key": {"ticker": "005930", "snapshot_date": "2026-09-18"}, "column": "wics_l2_nm",
         "expect": "WICS 반도체와반도체장비", "source": "test", "note": "L2 라벨 = IDX_NM_KOR"},
        {"id": "T-25-b", "key": {"ticker": "005930", "snapshot_date": "2026-09-18"}, "column": "float_mktcap_krw",
         "expect": str(3_500_000_000 * 1_000_000), "source": "test", "note": "백만원 × 1e6"},
    ], ensure_ascii=False), encoding="utf-8")
    return fx


def _equity(tmp_path: Path, stage_root: Path) -> tuple[Path, build.BuildResult]:
    equity_root = tmp_path / "equity"
    r = build.build_table(rules_s25.SECTOR_SNAPSHOT, stage_root, equity_root, Baseline({}), build_id="b_s25",
                          fixtures_path=_equity_fixtures(tmp_path))
    return equity_root, r


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    tmp = tmp_path_factory.mktemp("s25")
    return _equity(tmp, _stage(tmp))


def _con(equity_root: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    src = views.parquet_source(equity_root, "sector_snapshot")
    con.execute(f"CREATE OR REPLACE TEMP VIEW ss AS SELECT * FROM {src}")
    made = views.install_temp_macros(con, {"sector_snapshot": src})
    assert "v_sector" in made
    return con


def test_registered_and_in_build_order() -> None:
    assert RULES["sector_snapshot"] is rules_s25.SECTOR_SNAPSHOT
    order = [ln.strip() for ln in (Path(__file__).parent.parent / "scripts" / "equity_order.txt")
             .read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert "sector_snapshot" in order and order.index("sector_snapshot") < order.index("dataset_profile")
    assert views.SIGNATURES["v_sector"] == "v_sector(as_of)" and views.MACRO_INPUTS["v_sector"] == ("sector_snapshot",)
    assert "v_sector" not in views.ASOF_VIEWS if hasattr(views, "ASOF_VIEWS") else True


def test_build_passes_all_gates_and_keeps_only_l2_rows(built: tuple[Path, build.BuildResult]) -> None:
    equity_root, r = built
    assert r.ok, [(g.name, g.detail) for g in r.gates if g.status is GateStatus.FAIL]
    names = [g.name for g in r.gates]
    assert "EG3_sector_snapshot" in names and next(g for g in r.gates if g.name == "EG3_sector_snapshot").status is GateStatus.PASS
    con = _con(equity_root)
    assert con.execute("SELECT count(*), count(DISTINCT ticker), count(DISTINCT snapshot_date) FROM ss").fetchone() == (6, 3, 2)
    row = con.execute("SELECT wics_l1_cd, wics_l1_nm, wics_l2_cd, wics_l2_nm, float_mktcap_krw, float_shares_shr, available_basis "
                      "FROM ss WHERE ticker = '005930' AND snapshot_date = DATE '2026-09-18'").fetchone()
    assert row == ("G45", "IT", "G4530", "WICS 반도체와반도체장비", 3_500_000_000 * 1_000_000, 5_969_782_550, "convention")
    assert con.execute("SELECT count(*) FROM ss WHERE length(wics_l2_cd) <> 5").fetchone()[0] == 0
    con.close()


def test_v_sector_returns_latest_snapshot_at_or_before_as_of_with_age(built: tuple[Path, build.BuildResult]) -> None:
    equity_root, _ = built
    con = _con(equity_root)
    rows = con.execute("SELECT ticker, snapshot_date, days_since_snapshot FROM v_sector(DATE '2026-09-22') ORDER BY ticker").fetchall()
    assert [(t, d) for t, d, _ in rows] == [("005930", date(2026, 9, 18)), ("006400", date(2026, 9, 18)), ("034220", date(2026, 9, 18))]
    assert {a for _, _, a in rows} == {4}
    rows = con.execute("SELECT DISTINCT snapshot_date, days_since_snapshot FROM v_sector(DATE '2026-09-15')").fetchall()
    assert rows == [(date(2026, 9, 11), 4)]                                  # 09-18 스냅샷은 아직 안 보인다
    assert con.execute("SELECT count(*) FROM v_sector(DATE '2026-09-10')").fetchone()[0] == 0   # 첫 스냅샷 전 — 채우지 않는다
    assert con.execute("SELECT count(*) FROM v_sector(DATE '2026-09-18')").fetchone()[0] == 3    # 당일 포함
    con.close()


def test_eg3_rejects_a_ticker_that_has_l1_but_no_l2(tmp_path: Path) -> None:
    _, r = _equity(tmp_path, _stage(tmp_path, extra_l1_only=True))
    assert not r.ok
    g = next(g for g in r.gates if g.name == "EG3_sector_snapshot")
    assert g.status is GateStatus.FAIL and g.metrics["n_l1_only_tickers"] == 1 and "n_l1_only_tickers=1" in g.detail
