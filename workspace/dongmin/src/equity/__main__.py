"""CLI — `PYTHONPATH=src python -m equity <verb>` (EQUITY_WORKFLOW §7-3).

  pin     <stg_table>   stage current_build 를 `_pinned/` 에 하드링크로 고정
  build   <table>       빌드 → 게이트 → 통과 시 MANIFEST 교체
  gate    <table>       커밋된 current_build 를 재판정만 한다 (폐기 없음)
  catalog               equity.duckdb 재생성 (빌드·GC 뒤에는 반드시)

루트는 `--root`(기본 `$QL_HOME/data/equity`), stage 는 `--stage-root`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import duckdb
from stage import manifest

from . import baseline as baseline_mod
from . import (
    build,
    catalog,
    gates,
    inputs,
    rules_s01,  # noqa: F401  # reason: 등록 부작용 — S01 corp·security·corp_ticker
    rules_s02,  # noqa: F401  # reason: 등록 부작용 — S02 캘린더·구간·지수
    rules_s03,  # noqa: F401  # reason: 등록 부작용 — S03 유니버스 존재·상태·정책
    rules_s04,  # noqa: F401  # reason: 등록 부작용 — S04 가격 정본
    rules_s05,  # noqa: F401  # reason: 등록 부작용 — S05 기업행위 corp_event
    rules_sample,  # noqa: F401  # reason: T0 샘플 테이블
)
from .model import RULES


def _print_gates(results: list[gates.GateResult]) -> None:
    for g in results:
        print(f"  {g.name:5s} {g.status.value:5s} {g.detail}  {g.metrics}")


def _cmd_pin(a: argparse.Namespace) -> int:
    pb = inputs.pin(a.stage_root, a.root, a.stg_table)
    print(f"pinned table={pb.table} build={pb.build_id} partitions={len(pb.partition_paths)} "
          f"root={pb.root}")
    return 0


def _cmd_build(a: argparse.Namespace) -> int:
    rule = RULES[a.table]
    bl = baseline_mod.load(a.baseline or baseline_mod.path_for(a.root))
    r = build.build_table(rule, a.stage_root, a.root, bl, keep=a.keep,
                          memory_limit=a.memory_limit, threads=a.threads,
                          build_id=a.build_id)
    print(f"{r.status.value} table={r.table} build={r.build_id} rows={r.n_rows:,} "
          f"reject={r.n_reject:,} hash={r.content_hash} {r.elapsed_s}s")
    _print_gates(r.gates)
    if r.failed_report:
        print(f"  failed report: {r.failed_report}")
    return 0 if r.ok else 1


def _cmd_gate(a: argparse.Namespace) -> int:
    rule = RULES[a.table]
    table_root = a.root / rule.name
    m = manifest.load(table_root / "MANIFEST.json")
    bid = a.build or m.current_build
    rec = next((b for b in m.builds if b.build_id == bid), None)
    if rec is None:
        raise FileNotFoundError(f"no committed build to re-adjudicate — table={rule.name} "
                                f"build={bid} manifest={table_root / 'MANIFEST.json'}")
    pinned = {t: inputs.load_pinned(a.root, t, b) for t, b in rec.inputs.items()}
    part = "year=*/" if rule.partition_class != "whole" else ""
    glob = str(table_root / f"v={rec.build_id}" / part / "*.parquet")
    meta_path = table_root / str(rec.partitions[0]["path"]) / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    reasons = meta.get("n_reject_by_reason") or {}
    con = duckdb.connect()
    try:
        inputs.create_views(con, pinned, {t: list(rule.declared_columns(t)) for t in rule.inputs})
        # `v=<build_id>` 도 하이브 컬럼으로 붙는다 — 선언 스키마 대조를 위해 걷어낸다.
        con.execute("CREATE OR REPLACE TEMP VIEW out_pq AS SELECT * EXCLUDE (v) FROM "
                    f"read_parquet('{glob}', hive_partitioning=true)")
        row = con.execute("SELECT count(*) FROM out_pq").fetchone()
        if row is None:
            raise RuntimeError(f"row count returned no row — table={rule.name} "
                               f"build={rec.build_id} glob={glob}")
        n_out = int(str(row[0]))
        prev = next((b for b in reversed(m.builds) if b.build_id != rec.build_id), None)
        ctx = gates.EquityGateContext(
            con=con, rule=rule, out_view="out_pq", reject_view=None, pinned=pinned, n_out=n_out,
            n_reject=int(meta.get("n_reject", 0)), reject_by_reason=dict(reasons),
            inputs=dict(rec.inputs),
            partition_hashes={str(p["path"]).split("/", 1)[1] if "/" in str(p["path"]) else "whole":
                              str(p.get("content_hash", "")) for p in rec.partitions},
            baseline=baseline_mod.load(a.baseline or baseline_mod.path_for(a.root)),
            previous=prev, fixtures=gates.load_fixtures(rule.name, a.root))
        results = gates.run_all(ctx)
    finally:
        con.close()
    n_fail = sum(1 for g in results if g.status is gates.GateStatus.FAIL)
    print(f"{'gate_failed' if n_fail else 'ok'} table={rule.name} build={rec.build_id} "
          f"rows={n_out:,} fail={n_fail}")
    _print_gates(results)
    return 1 if n_fail else 0


def _cmd_catalog(a: argparse.Namespace) -> int:
    path = catalog.write_catalog(a.root, catalog.MACROS)
    builds = catalog.table_builds(a.root)
    print(f"ok catalog={path} macros={len(catalog.MACROS)} tables={len(builds)} "
          f"snapshot_id={catalog.snapshot_id(builds)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(prog="equity", description=__doc__)
    ap.add_argument("--root", type=Path, default=base / "data" / "equity")
    ap.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    ap.add_argument("--baseline", type=Path)
    sub = ap.add_subparsers(dest="verb", required=True)

    p_pin = sub.add_parser("pin", help="stage current_build 를 _pinned/ 에 고정")
    p_pin.add_argument("stg_table")
    p_pin.set_defaults(fn=_cmd_pin)

    p_build = sub.add_parser("build", help="빌드 → 게이트 → MANIFEST 교체")
    p_build.add_argument("table", choices=sorted(RULES))
    p_build.add_argument("--build-id")
    p_build.add_argument("--keep", type=int, default=manifest.KEEP_DEFAULT)
    p_build.add_argument("--memory-limit", default="6GB")
    p_build.add_argument("--threads", type=int, default=3)
    p_build.set_defaults(fn=_cmd_build)

    p_gate = sub.add_parser("gate", help="커밋된 빌드 재판정 (폐기 없음)")
    p_gate.add_argument("table", choices=sorted(RULES))
    p_gate.add_argument("--build")
    p_gate.set_defaults(fn=_cmd_gate)

    p_cat = sub.add_parser("catalog", help="equity.duckdb 재생성")
    p_cat.set_defaults(fn=_cmd_catalog)

    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
