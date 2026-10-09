"""아침 확정판 재사용 (묶음 7-3, 플랜 2026-10-09-batch7-wise-dedup.md '설계 7-3').

`TableRule.morning_reuse` 표는 아침(m_) 빌드 전에 저녁 판(e_)을 그대로 쓸 수 있는지 본다. 전부 참일 때만:
  ① 표 선언 morning_reuse · 빌드 id 가 m_ · 끄기 파일 `<stage_root>/REUSE_OFF` 없음
  ② MANIFEST 현재 판이 저녁 판(basis evening)이고 그 자신이 재사용 판이 아님
  ③ 현재 판 rules_version = 코드 RULES_VERSION
  ④ 현재 판 code_rev = 지금 배포 rev(`$QL_HOME/DEPLOYED.json`) — 둘 중 하나라도 없으면 재사용 안 함
  ⑤ 아침 스냅샷에서 다시 계산한 원장 지문 = 저녁 판 기록(`fold.input_fingerprint` — 빌드와 같은 함수)
  ⑥ baseline.json 그 표 항목·골든 픽스처·연도(G7 범위) = 저녁 판 `_meta.json` 기록(`build.build_inputs`)
  ⑦ 하드링크한 parquet 의 content_hash 재계산 = 기록
하나라도 아니면 `Declined(사유)` — 호출자(`__main__`)가 사유 한 줄을 찍고 일반 `build_table` 로 간다(P1).
src_bytes·src_mtime 은 DB 전체 축이라 판정에 쓰지 않고 기록만 한다(D7-6).

재커밋은 새 m_ id 로 한다. 저녁 판 parquet·`_reject` 는 하드링크하고 `_meta.json` 은 새 파일로 쓴다(하드링크된
저녁 파일을 제자리에서 고치면 저녁 판이 바뀐다). gates 는 저녁 판에서 복사한다. m_ 판이라 건전성 C1 은 예외
규칙 없이 통과하고, C4 는 계수·해시 동결로 통과한다(재사용 정합의 독립 확인). keep 3 으로 저녁 판 디렉터리가
지워져도 하드링크라 m_ 판은 그대로 읽힌다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from . import build, fold, gates, manifest, model
from .model import RULES_VERSION, TableRule
from .snapshot import Snapshot

OFF_FILE = "REUSE_OFF"          # `<stage_root>/REUSE_OFF` 가 있으면 다음 아침부터 일반 빌드(코드·크론 변경 없음)


class Declined(Exception):
    """재사용 판정이 아님 — `reason` 한 줄이 출력·로그에 남는다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def deployed_rev(home: Path) -> str | None:
    """`<home>/DEPLOYED.json` 의 rev(scripts/deploy.sh 가 `--apply` 때 쓴다). 없거나 못 읽으면 None."""
    try:
        meta = json.loads((home / "DEPLOYED.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rev = meta.get("rev") if isinstance(meta, dict) else None
    return rev if isinstance(rev, str) and rev else None


def _current(table_root: Path) -> manifest.BuildRecord:
    m = manifest.load(table_root / "MANIFEST.json")
    cur = next((b for b in m.builds if b.build_id == m.current_build), None)
    if cur is None:
        raise Declined(f"no_current_build table={table_root.name}")
    return cur


def _judge(rule: TableRule, snap: Snapshot, stage_root: Path, build_id: str, code_rev: str | None,
           fixtures_path: Path | None, baseline_path: Path | None
           ) -> tuple[manifest.BuildRecord, str]:
    """판정 ①~⑥. 통과하면 (저녁 판 레코드, 다시 계산한 원장 지문)."""
    bs = rule.blob_source
    if not rule.morning_reuse or bs is None:                                             # ①
        raise Declined(f"not_declared table={rule.name}")
    if model.basis_of_build_id(build_id) != "morning":
        raise Declined(f"basis={model.basis_of_build_id(build_id)} build={build_id}")
    if (stage_root / OFF_FILE).exists():
        raise Declined(f"off_file={stage_root / OFF_FILE}")
    cur = _current(stage_root / rule.name)
    if cur.basis != "evening" or cur.reused_from is not None:                            # ②
        raise Declined(f"current_not_evening build={cur.build_id} basis={cur.basis} "
                       f"reused_from={cur.reused_from}")
    if cur.rules_version != RULES_VERSION:                                               # ③
        raise Declined(f"rules_version current={cur.rules_version} code={RULES_VERSION}")
    if cur.code_rev is None or code_rev is None or cur.code_rev != code_rev:             # ④
        raise Declined(f"code_rev current={cur.code_rev} deployed={code_rev}")
    lite = build._sqlite_ro(snap.files[bs.db].path)                                      # ⑤
    try:
        fp = fold.input_fingerprint(lite, bs)
    finally:
        lite.close()
    if cur.input_fingerprint is None or fp != cur.input_fingerprint:
        raise Declined(f"input_fingerprint current={cur.input_fingerprint} snapshot={fp}")
    src = stage_root / rule.name / f"v={cur.build_id}"                                   # ⑥
    metas = sorted(src.glob("**/_meta.json"))
    if not metas:
        raise Declined(f"no_meta dir={src}")
    recorded = json.loads(metas[0].read_text(encoding="utf-8")).get("build_inputs")
    now = build.build_inputs(rule, stage_root, fixtures_path, baseline_path,
                             datetime.now(UTC).year)
    if recorded != now:
        raise Declined(f"build_inputs recorded={recorded} now={now}")
    return cur, fp


def try_reuse(rule: TableRule, snap: Snapshot, stage_root: Path, build_id: str, *,
              code_rev: str | None, fixtures_path: Path | None = None,
              baseline_path: Path | None = None) -> build.BuildResult:
    """판정 ①~⑦ 이 전부 참이면 저녁 판을 새 m_ 판(`build_id`)으로 다시 커밋한다. 아니면 `Declined`.

    중간에 실패하면 반쯤 만든 `_tmp/<build_id>`·`v=<build_id>` 를 지우고 예외를 다시 올린다 — MANIFEST 는
    마지막에만 바꾼다. 호출자는 같은 `build_id` 로 일반 빌드를 하면 된다.
    """
    t0 = time.time()
    cur, fp = _judge(rule, snap, stage_root, build_id, code_rev, fixtures_path, baseline_path)
    table_root = stage_root / rule.name
    src = table_root / f"v={cur.build_id}"
    tmp_root = stage_root / "_tmp" / build_id
    tmp_table = tmp_root / rule.name
    final_dir = table_root / f"v={build_id}"
    src_files = [snap.files[s.db] for s in rule.sources if s.db in snap.files]
    try:
        if tmp_root.exists():
            shutil.rmtree(tmp_root)
        tmp_table.mkdir(parents=True)
        for f in sorted(src.rglob("*")):
            dst = tmp_table / f.relative_to(src)
            if f.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            elif f.name == "_meta.json":         # 새 파일 — 저녁 판 _meta.json 은 바이트 그대로 둔다
                meta = json.loads(f.read_text(encoding="utf-8"))
                meta.update(build_id=build_id, snapshot_id=snap.snapshot_id,
                            src_bytes=sum(x.bytes for x in src_files),
                            src_mtime=max((x.src_mtime for x in src_files), default=0.0),
                            reused_from=cur.build_id)
                build._write_json(dst, meta)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.link(f, dst)
        glob = str(tmp_table / ("year=*/*.parquet" if build._is_partitioned(rule) else "*.parquet"))
        con = duckdb.connect()                                                           # ⑦
        try:
            got = build._content_hash(con, glob)
        finally:
            con.close()
        if got != cur.content_hash:
            raise Declined(f"content_hash recorded={cur.content_hash} linked={got}")
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(tmp_table), str(final_dir))
        shutil.rmtree(tmp_root, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp_root, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        raise
    prefix = f"v={cur.build_id}"
    partitions = [{**p, "path": f"v={build_id}" + str(p.get("path", ""))[len(prefix):]}
                  for p in cur.partitions]
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id=snap.snapshot_id, rules_version=RULES_VERSION,
        built_at_utc=datetime.now(UTC).isoformat(timespec="seconds"), n_rows=cur.n_rows,
        content_hash=cur.content_hash, partitions=partitions, gates=[dict(g) for g in cur.gates],
        max_available_date=cur.max_available_date, max_observed_date=cur.max_observed_date,
        reused_from=cur.build_id, input_fingerprint=fp, code_rev=code_rev))
    results = [gates.GateResult(str(g["name"]), gates.GateStatus(g["status"]), str(g["detail"]),
                                dict(g["metrics"]) if isinstance(g.get("metrics"), dict) else {})
               for g in cur.gates]
    g1 = next((r.metrics for r in results if r.name == "G1"), {})
    return build.BuildResult(build.BuildStatus.OK, rule.name, build_id, snap.snapshot_id,
                             cur.n_rows, int(str(g1.get("n_src", 0))),
                             int(str(g1.get("n_dedup", 0))), int(str(g1.get("n_reject", 0))),
                             cur.content_hash, results, round(time.time() - t0, 1), final_dir,
                             None, reused_from=cur.build_id)
