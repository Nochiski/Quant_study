"""baseline.json (§9) — ★ 회귀 고정값과 테이블별 게이트 임계를 코드가 아니라 데이터로 둔다."""
import json
import sqlite3
from pathlib import Path

import pytest

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


# ── G9 술어 단일화 · 허용폭 · 부분 재측정 (DEFECT-B02) ─────────────────────────
def test_게이트와_baseline_이_같은_교차조인_상수를_쓴다() -> None:
    """09-02 baseline 은 시각 조건 없는 조인으로 쟀고 게이트는 20:00 KST 컷오프를 쓴다 —
    서로 다른 모집단이라 '회귀' 가 성립하지 않았다. 술어는 상수 하나에서만 나와야 한다."""
    from stage import rules_krx
    tmpl = rules_krx.CROSS_JOIN_PREDICATE_SQL
    assert "INTERVAL 20 HOUR" in tmpl
    gate_sql = rules_krx.STG_PRICE_DAILY.cross_check.join_sql
    assert gate_sql == tmpl.format(kw="o", ticker="s.ticker",
                                   dt8="strftime(s.date, '%Y%m%d')", day="s.date")
    price = {m.metric: m.sql for m in baseline.METRICS if m.table == "stg_price_daily"}
    base_pred = tmpl.format(kw="f", ticker="p.ISU_CD", dt8="p.BAS_DD",
                            day="strptime(p.BAS_DD, '%Y%m%d')")
    for metric in ("close_match_ratio", "volume_match_ratio", "close_joined"):
        assert base_pred in price[metric], metric


def test_measure_는_G9_허용폭_선언상수를_싣는다(tmp_path: Path) -> None:
    s = _snap(tmp_path)
    data = baseline.measure(s, METRICS, THRESHOLDS, measured_at="2026-09-19")
    assert data["stg_price_daily"]["volume_match_ratio_tol"] == 5e-5
    entry = next(e for e in data["_measured"] if e["metric"] == "volume_match_ratio_tol")
    assert entry["reason"] and entry["value"] == 5e-5


def test_only_는_지정한_지표만_재고_나머지는_보존한다(tmp_path: Path) -> None:
    """전량 재측정은 그날 원장 상태로 24지표를 통째로 느슨하게 만든다(09-09 리뷰 D5-(b))."""
    s = _snap(tmp_path)
    out = tmp_path / "baseline.json"
    baseline.write(out, baseline.measure(s, METRICS, THRESHOLDS, measured_at="2026-09-02"))
    stale = json.loads(out.read_text(encoding="utf-8"))
    stale["stg_probe_base"]["n_rows"] = 1              # 옛 판 — 재측정하면 3 이 된다
    stale["stg_probe_base"]["negative_rows"] = 99      # 손대면 안 되는 값
    baseline.write(out, stale)

    data = baseline.measure_only(s, json.loads(out.read_text(encoding="utf-8")),
                                 ["stg_probe_base.n_rows"], metrics=METRICS,
                                 measured_at="2026-09-19", note="G9 술어 변경 재측정")
    assert data["stg_probe_base"]["n_rows"] == 3
    assert data["stg_probe_base"]["negative_rows"] == 99          # 보존
    assert data["stg_probe_base"]["thresholds"] == {"G2": 0.5}    # 보존
    ents = {e["metric"]: e for e in data["_measured"]}
    assert ents["n_rows"]["measured_at"] == "2026-09-19"
    assert ents["n_rows"]["note"] == "G9 술어 변경 재측정" and ents["n_rows"]["snapshot_id"] == "s"
    assert ents["negative_rows"]["measured_at"] == "2026-09-02"   # 나머지는 옛 측정일 유지
    assert data["measured_at"] == "2026-09-02"                    # 전량 측정일도 그대로


def test_only_는_알_수_없는_지표_이름을_거부한다(tmp_path: Path) -> None:
    s = _snap(tmp_path)
    base = baseline.measure(s, METRICS, THRESHOLDS, measured_at="2026-09-02")
    with pytest.raises(ValueError, match="unknown baseline metric"):
        baseline.measure_only(s, base, ["stg_probe_base.nope"], metrics=METRICS)


def test_only_cli_는_기존_baseline_이_없으면_거부한다(tmp_path: Path) -> None:
    """`--only` 는 갱신이다 — 파일이 없으면 전량 측정으로 조용히 바뀌면 안 된다."""
    _snap(tmp_path)
    assert baseline.main(["--snapshot-id", "s", "--snapshot-root", str(tmp_path / "snapshots"),
                          "--out", str(tmp_path / "baseline.json"),
                          "--only", "stg_price_daily.volume_match_ratio"]) == 2
