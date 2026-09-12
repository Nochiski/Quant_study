"""CLI — `PYTHONPATH=src python -m stage --table stg_price_daily [--snapshot-id ID]`.

스냅샷이 없으면 rules 가 필요로 하는 DB 만 VACUUM INTO 로 새로 뜬다.
`--basis evening|morning` 은 빌드 id 접두어(`e_`/`m_`)와 MANIFEST 의 `basis` 를 정한다 —
하루 2판(저녁 잠정·아침 확정) 규약, 플랜 v2 Task B.1.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import build, model, rules, snapshot


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", required=True, choices=sorted(rules.RULES))
    ap.add_argument("--raw-dir", type=Path, default=base / "data" / "raw")
    ap.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    ap.add_argument("--snapshot-root", type=Path, default=base / "data" / "snapshots")
    ap.add_argument("--snapshot-id", help="기존 스냅샷 재사용 (재현성 검증용)")
    ap.add_argument("--build-id")
    ap.add_argument("--basis", choices=sorted(model.BASIS_PREFIX), default=model.BASIS_MANUAL,
                    help="빌드 판 구분 — evening=e_ · morning=m_ · manual=b_ (기본)")
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--g2", type=float, help="G2 임계 override")
    ap.add_argument("--g7", type=float, help="G7 임계 override")
    a = ap.parse_args(argv)

    rule = rules.RULES[a.table]
    if a.snapshot_id:
        snap = snapshot.load_snapshot(a.snapshot_root / a.snapshot_id)
    else:
        dbs = {s.db for s in rule.sources} | ({rule.cross_check.db} if rule.cross_check else set())
        raw = {db: a.raw_dir / rules.LEDGER_FILES[db] for db in sorted(dbs)}
        print(f"snapshot: VACUUM INTO {sorted(dbs)} → {a.snapshot_root}", flush=True)
        snap = snapshot.make_snapshot(raw, a.snapshot_root)
    print(f"snapshot={snap.snapshot_id} " + " ".join(
        f"{k}={v.bytes / 1e9:.2f}GB" for k, v in snap.files.items()), flush=True)
    thr = {k: v for k, v in (("G2", a.g2), ("G7", a.g7)) if v is not None}
    fx = Path(__file__).parent / "fixtures" / f"{a.table}.json"   # 골든 픽스처는 코드와 함께 산다
    bid = a.build_id or model.make_build_id(a.basis)   # --build-id 를 주면 그 접두어가 basis 다
    r = build.build_table(rule, snap, a.stage_root, build_id=bid, gate_thresholds=thr,
                          fixtures_path=fx if fx.exists() else None,
                          memory_limit=a.memory_limit, threads=a.threads)
    print(f"{r.status.value} table={r.table} build={r.build_id} "
          f"basis={model.basis_of_build_id(r.build_id)} rows={r.n_rows:,} src={r.n_src:,} "
          f"dedup={r.n_dedup:,} reject={r.n_reject:,} hash={r.content_hash} {r.elapsed_s}s")
    for g in r.gates:
        print(f"  {g.name} {g.status.value:5s} {g.detail}  {g.metrics}")
    if r.failed_report:
        print(f"  failed report: {r.failed_report}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
