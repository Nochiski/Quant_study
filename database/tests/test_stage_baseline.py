"""baseline.json (§9) — ★ 회귀 고정값과 테이블별 게이트 임계를 코드가 아니라 데이터로 둔다."""
import json
import sqlite3
from pathlib import Path

from stage import baseline, build, gates, model, snapshot


def _snap(tmp_path: Path) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE t (k TEXT, v TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?,?)", [
        ("a", "1", "2026-08-30T10:00:00"), ("b", "x", "2026-08-30T10:00:00"),   # 'x' → cast_failed
        ("c", "-3", "2026-08-30T10:00:00")])
    con.commit()
    con.close()
    return snapshot.make_snapshot({"x": d / "x.db"}, tmp_path / "snapshots", snapshot_id="s")


PROBE = model.TableRule(
    name="stg_probe_base",
    sources=(model.SourceRef("x", "t", "t"),),
    columns=(model.ColumnRule("k", "k", model.KIND_TEXT, key=True),
             model.ColumnRule("v", "v", model.KIND_NUMERIC, 5, 0)),
    natural_key=("k",), partition_class="whole", partition_expr=None, partition_src=None,
    observed_src="collected_at", write_mode="append_only", fanout=1,
    payload_exclude=("collected_at",), lag_known=False, available=model.AVAILABLE_NONE,
)
METRICS = (
    baseline.Metric("stg_probe_base", "negative_rows", "x",
                    "SELECT count(*) FROM x.t WHERE TRY_CAST(v AS INTEGER) < 0", growing=False),
    baseline.Metric("stg_probe_base", "n_rows", "x", "SELECT count(*) FROM x.t", growing=True),
)
THRESHOLDS = {"stg_probe_base": {"G2": (0.5, "비숫자 1/3 실측")}}


def test_measure_runs_each_metric_on_the_snapshot_and_keeps_provenance(tmp_path: Path) -> None:
    s = _snap(tmp_path)
    data = baseline.measure(s, METRICS, THRESHOLDS, measured_at="2026-09-03")
    assert data["stg_probe_base"]["negative_rows"] == 1 and data["stg_probe_base"]["n_rows"] == 3
    assert data["stg_probe_base"]["thresholds"] == {"G2": 0.5}
    ents = {(e["table"], e["metric"]): e for e in data["_measured"]}
    e = ents[("stg_probe_base", "n_rows")]
    assert e["value"] == 3 and e["growing"] is True and e["measured_at"] == "2026-09-03"
    assert e["sql"].startswith("SELECT count(*)") and data["snapshot_id"] == "s"
    out = tmp_path / "baseline.json"
    baseline.write(out, data)
    assert json.loads(out.read_text(encoding="utf-8"))["stg_probe_base"]["thresholds"]["G2"] == 0.5


def test_build_reads_per_table_thresholds_from_baseline(tmp_path: Path) -> None:
    s = _snap(tmp_path)
    r0 = build.build_table(PROBE, s, tmp_path / "stage")
    assert r0.status is build.BuildStatus.GATE_FAILED      # 기본 G2 0 — cast_failed 1/3
    bp = tmp_path / "baseline.json"
    baseline.write(bp, baseline.measure(s, METRICS, THRESHOLDS, measured_at="2026-09-03"))
    r1 = build.build_table(PROBE, s, tmp_path / "stage", baseline_path=bp)
    assert r1.ok, [g for g in r1.gates if g.status is gates.GateStatus.FAIL]
    assert next(g for g in r1.gates if g.name == "G2").metrics["limit"] == 0.5
    r2 = build.build_table(PROBE, s, tmp_path / "stage", baseline_path=bp,
                           gate_thresholds={"G2": 0.0})
    assert r2.status is build.BuildStatus.GATE_FAILED      # CLI 가 baseline 을 덮는다


def test_default_metric_catalogue_covers_the_starred_tables() -> None:
    tables = {m.table for m in baseline.METRICS}
    starred = {"stg_price_daily", "stg_etf_price_daily", "stg_listing_daily", "stg_disclosure",
               "stg_fin", "stg_doc_index", "stg_audit", "stg_credit_daily", "stg_capital",
               "stg_event_tsstk_dp"}
    assert starred <= tables
    assert {"close_match_ratio", "volume_match_ratio"} <= {m.metric for m in baseline.METRICS
                                                            if m.table == "stg_price_daily"}
    assert set(baseline.THRESHOLDS) >= {"stg_listing_daily", "stg_shares", "stg_delisted_master"}
