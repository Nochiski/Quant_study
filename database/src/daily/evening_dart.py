"""저녁 DART 판(D13 · N-62) — 18:05 DART 수집이 끝나면 그날 공시·재무를 모델 DB 루트에 다시 짓는다.

장 마감 체인 evening 모드(`scripts/postclose_chain.sh`)가 빌드 락 안에서 부른다. 원장 dart.db 를 한 번만 떠서(스냅숏) 그
판으로 stage DART 폐포(`STAGE_TABLES`)와 equity DART 폐포(`EQUITY_TABLES`)를 모델 DB 루트(`data/model_db/stage` ·
`data/model_db/equity`)에 저녁 판(e_)으로 짓고, 판 목록을 저녁 DART 인계(`handoff_path`)에 쓴다. 장 마감 fi 는 그
인계를 `--dart-builds-from` 으로 읽어 DART 정보 시점만 T 로 연다(`factor_inputs.build`). 엑셀 Events 시트도 같은
판을 읽는다. 연구 루트에는 쓰지 않는다 — 연구 인계 이력(stage_builds)에 저녁 판이 섞이면 안 된다(T-29 와 같은 이유).

폐포: 표 목록은 이 모듈 한 곳이다(fi 가 인계를 검사할 때도 여기서 읽는다).
  · stage 16표는 모두 원장 dart.db 하나만 읽는다. `stg_rcept_dt_map` 이 먼저다(공개일 조회표). 문서 표
    (`stg_doc_meta`·`stg_doc_correction`)는 같은 스냅숏의 문서 캐시가 있어야 지어진다.
  · equity 6표는 이 stage 표와 서로만 입력으로 쓴다(corp → disclosure_version → fin_std … 순서).
  · 수정주가 사슬(기업행위 → 계수 → 유니버스)은 넣지 않는다 — fi 의 T-6 당일 기업행위 보류가 맡는다.

순서(앞 단계가 실패하면 멈추고, 인계는 health fail 로 쓴다 — fi 가 그 인계를 거절해 체인이 전 거래일 공시 판으로 간다):
  ① 스냅숏 — dart.db 하나만 `<model_db>/snapshots` 에 VACUUM INTO(원장 락 없이 — 아침 빌드와 같은 방식, 원장 무변경)
  ② 문서 캐시 — 연구 루트의 최신 ok 캐시를 기준으로 증분(`stage.doc_prepass --base-stage-root`, 손대지 않은 샤드는 하드링크)
  ③ stage 16표 — 표마다 따로 프로세스(`python -m stage`, ① 의 스냅숏 id 를 모두 쓴다 · 게이트 임계는 연구 stage
     baseline 을 `--baseline` 으로 — 모델 DB stage 루트엔 그 파일이 없어 한도 0 으로 걸린다, 10-29 미리 돌리기)
  ④ equity 6표 — `python -m equity --baseline <연구 baseline>`(사본을 두면 S1 처럼 상수가 늘 때 갈린다)
  ⑤ 인계 기록(원자 교체) → 스냅숏 GC(keep = KEEP_DEFAULT, 현재 판 보호) · 지난 문서 캐시 정리(현재 판이 선 것만 남김)
     · equity `_pinned` 정리(`equity.inputs.gc_pinned` — 현재 판이 가리키는 판 + 표별 최신 keep, 연구 체인과 같은 함수)
표마다 따로 프로세스인 이유: stg_fin 은 6GB 메모리 한도로 돈다 — 한 프로세스에 쌓지 않는다(연구 체인 run_stage_all 과 같다).
시간 한도는 두지 않는다(P9 — 늦어짐은 저녁 워치독이 알린다).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from equity import handoff, inputs
from stage import manifest, snapshot
from stage.model import basis_of_build_id
from stage.rules import LEDGER_FILES

# 원장 → stage 순서(run_stage_all.sh ORDER 의 상대 순서 — 공개일 조회표가 먼저, 문서 캐시 표가 끝)
STAGE_TABLES: tuple[str, ...] = (
    "stg_rcept_dt_map",
    "stg_fin", "stg_dividend", "stg_audit", "stg_holder_elestock", "stg_holder_majorstock",
    "stg_disclosure", "stg_company", "stg_corp_map", "stg_doc_index",
    "stg_event_tsstk_aq", "stg_event_piic", "stg_event_cvbd_is", "stg_event_tsstk_dp",
    "stg_doc_meta", "stg_doc_correction",
)
# stage → equity 순서(입력 의존 순 — corp·disclosure_version 이 fin_std·audit_opinion·holder_daily 앞)
EQUITY_TABLES: tuple[str, ...] = ("corp", "disclosure_version", "fin_std", "dividend_event",
                                  "audit_opinion", "holder_daily")
BASIS = "evening"
LEDGER = "dart"
DOC_WORKERS = 3
_OK = "ok"
_FAIL = "fail"

Runner = Callable[[Sequence[str]], int]


def handoff_path(model_db: Path, t: str) -> Path:
    """저녁 DART 인계 — `<model_db>/dart/<T>_evening.json`(T = YYYYMMDD)."""
    return Path(model_db) / "dart" / f"{t}_{BASIS}.json"


class HandoffRejected(ValueError):
    """저녁 DART 인계를 받지 않는다 — 읽는 쪽(fi · compat)이 자기 오류로 감싸 rc 2 로 멈춘다."""


@dataclass(frozen=True)
class DartPin:
    """저녁 DART 인계 — 판 목록과 그 판을 해석할 모델 DB equity 루트."""
    pin: handoff.Handoff
    equity_root: Path


def load_handoff(path: Path, t: str, research_equity_root: Path, need: Sequence[str]) -> DartPin:
    """저녁 DART 인계를 읽고 다음을 본다 — 하나라도 아니면 `HandoffRejected`(D' 판으로 대신하지 않는다, P1).
    ① 그날 저녁 판(basis evening · date T, YYYYMMDD) ② stage·equity health ok ③ DART 폐포(`EQUITY_TABLES`) 표만 싣고
    읽는 쪽이 쓰는 DART 표(`need`)가 다 있다 ④ 판이 저녁 판(e_) ⑤ equity 루트가 있고 연구 equity 루트가 아니다."""
    try:
        h = handoff.load(path)
    except handoff.HandoffError as e:
        raise HandoffRejected(str(e)) from e
    where = f"인계={path}"
    if h.basis != BASIS:
        raise HandoffRejected(f"저녁 DART 인계가 저녁 판(basis=evening) 것이 아니다: {where} basis={h.basis}")
    if h.date != t:
        raise HandoffRejected(f"저녁 DART 인계 날짜가 판 날짜 T 와 다르다(그날 저녁 판만 읽는다): {where} "
                              f"date={h.date} --date={t}")
    if not h.health_ok:
        raise HandoffRejected(f"저녁 DART 인계의 health 가 ok 가 아니다(그날 저녁 DART 판 실패): {where} "
                              f"health={h.health} 기대={{'stage': 'ok', 'equity': 'ok'}}")
    extra = sorted(set(h.equity_builds) - set(EQUITY_TABLES))
    if extra:
        raise HandoffRejected(f"저녁 DART 인계에 DART 폐포 밖 equity 표가 있다: {where} 표={extra} "
                              f"— 폐포는 daily.evening_dart.EQUITY_TABLES {EQUITY_TABLES}")
    missing = [x for x in need if x not in h.equity_builds]
    if missing:
        raise HandoffRejected(f"저녁 DART 인계에 읽는 쪽이 쓰는 DART 표가 없다: {where} 없는 표={missing}")
    not_evening = {x: b for x, b in sorted(h.equity_builds.items()) if basis_of_build_id(b) != BASIS}
    if not_evening:
        raise HandoffRejected(f"저녁 DART 인계의 판이 저녁 판(e_)이 아니다: {where} {not_evening}")
    if h.equity_root is None:
        raise HandoffRejected(f"저녁 DART 인계에 equity_root 가 없다: {where}")
    root = Path(h.equity_root)
    if root.resolve() == Path(research_equity_root).resolve():
        raise HandoffRejected(
            f"저녁 DART 인계의 equity 루트가 연구 equity 루트다: {where} equity_root={root} — 저녁 판은 모델 DB "
            "루트(data/model_db/equity)에 짓는다(연구 인계 이력에 저녁 판이 섞이지 않게)")
    return DartPin(h, root)


class BoardStatus(Enum):
    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True)
class Step:
    name: str            # snapshot · doc_prepass · stage:<표> · equity:<표>
    rc: int
    elapsed_s: float
    detail: str = ""


@dataclass(frozen=True)
class BoardResult:
    status: BoardStatus
    handoff: Path
    snapshot_id: str | None
    steps: tuple[Step, ...] = field(default_factory=tuple)
    failed: str | None = None        # 멈춘 단계와 사유(성공이면 None)

    @property
    def ok(self) -> bool:
        return self.status is BoardStatus.OK

    def summary(self) -> str:
        total = round(sum(s.elapsed_s for s in self.steps), 1)
        head = (f"저녁 DART 판 {self.status.value} snapshot={self.snapshot_id or '-'} "
                f"단계 {len(self.steps)} · {total}초 · 인계 {self.handoff}")
        return head if self.failed is None else f"{head} · 멈춤: {self.failed}"


@dataclass(frozen=True)
class Roots:
    """저녁 DART 판이 읽고 쓰는 자리 — 운영은 `default_roots(QL_HOME)`."""
    model_db: Path               # 쓰기: stage · equity · snapshots · dart(인계)
    raw_dir: Path                # 읽기: 원장 dart.db · 문서 원문(documents)
    research_stage: Path         # 읽기: 문서 캐시 기준(<research_stage>/_tmp/doc)
    baseline: Path               # 읽기: 연구 equity baseline.json

    @property
    def stage(self) -> Path:
        return self.model_db / "stage"

    @property
    def equity(self) -> Path:
        return self.model_db / "equity"

    @property
    def snapshots(self) -> Path:
        return self.model_db / "snapshots"


def default_roots(home: Path) -> Roots:
    data = Path(home) / "data"
    return Roots(model_db=data / "model_db", raw_dir=data / "raw", research_stage=data / "stage",
                 baseline=data / "equity" / "baseline.json")


def _subprocess_runner(cmd: Sequence[str]) -> int:
    """자식 출력은 그대로 부르는 쪽 로그로 간다(체인 로그 logs/postclose/<T>_evening.log)."""
    print(f"  $ {' '.join(cmd)}", flush=True)
    return subprocess.run(list(cmd), check=False).returncode


def doc_base(research_stage: Path, exclude: str) -> str | None:
    """문서 캐시 기준 — 연구 루트 `_tmp/doc/*/summary.json` 중 status ok 인 것의 mtime 최신(`exclude` 제외).
    규칙은 `scripts/doc_prepass_daily.sh` 와 같다. 없으면 None."""
    cache = Path(research_stage) / "_tmp" / "doc"
    found: list[tuple[float, str]] = []
    for summary in cache.glob("*/summary.json"):
        sid = summary.parent.name
        if sid == exclude:
            continue
        try:
            ok = json.loads(summary.read_text(encoding="utf-8")).get("status") == _OK
        except (OSError, ValueError):
            continue
        if ok:
            found.append((summary.stat().st_mtime, sid))
    return max(found)[1] if found else None


def _current(root: Path, table: str) -> str | None:
    path = root / table / "MANIFEST.json"
    try:
        return manifest.load(path).current_build
    except (FileNotFoundError, KeyError, ValueError):
        return None


def _write_handoff(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _prune_doc_caches(stage_root: Path, keep: set[str]) -> int:
    """모델 DB 문서 캐시 중 현재 판이 선 스냅숏(`keep`) 밖을 지운다 — 다음 저녁은 연구 캐시를 기준으로 새로 세운다.
    샤드 대부분이 연구 캐시와 하드링크라 지워도 그 캐시는 그대로다."""
    cache = stage_root / "_tmp" / "doc"
    n = 0
    for d in sorted(p for p in cache.glob("snap_*") if p.is_dir()):
        if d.name not in keep:
            shutil.rmtree(d)
            n += 1
    return n


def build(t: str, roots: Roots, *, run: Runner = _subprocess_runner, python: str = sys.executable,
          snapshot_id: str | None = None) -> BoardResult:
    """T(YYYYMMDD) 저녁 DART 판을 짓고 인계를 쓴다. 실패해도 인계는 health fail 로 남긴다(옛 인계가 남아
    fi 가 낡은 판을 읽지 않게). 인자 오류(날짜 형식)는 `ValueError`."""
    datetime.strptime(t, "%Y%m%d").replace(tzinfo=UTC)       # 형식만 본다
    out = handoff_path(roots.model_db, t)
    steps: list[Step] = []
    health = {"stage": _FAIL, "equity": _FAIL}
    snap_id: str | None = None
    failed: str | None = None

    def step(name: str, cmd: Sequence[str]) -> bool:
        t0 = time.time()
        rc = run(cmd)
        steps.append(Step(name, rc, round(time.time() - t0, 1)))
        return rc == 0

    t0 = time.time()
    try:
        snap = snapshot.make_snapshot({LEDGER: roots.raw_dir / LEDGER_FILES[LEDGER]}, roots.snapshots,
                                      snapshot_id=snapshot_id)
        snap_id = snap.snapshot_id
        steps.append(Step("snapshot", 0, round(time.time() - t0, 1),
                          f"{LEDGER}={snap.files[LEDGER].bytes / 1e9:.2f}GB"))
    except (OSError, sqlite3.Error) as e:
        steps.append(Step("snapshot", 1, round(time.time() - t0, 1), f"{type(e).__name__}: {e}"))
        failed = f"snapshot ({type(e).__name__}: {e})"
    if failed is None and snap_id is not None:
        base = doc_base(roots.research_stage, snap_id)
        if base is None:
            failed = (f"doc_prepass (연구 문서 캐시 {roots.research_stage}/_tmp/doc 에 ok 기준이 없다 — "
                      "문서 표 stg_doc_meta·stg_doc_correction 을 지을 수 없다)")
            steps.append(Step("doc_prepass", 1, 0.0, "no base cache"))
        elif not step("doc_prepass", [
                python, "-m", "stage.doc_prepass", "--snapshot-id", snap_id,
                "--snapshot-root", str(roots.snapshots), "--stage-root", str(roots.stage),
                "--docs-dir", str(roots.raw_dir / "documents"), "--base-snapshot", base,
                "--base-stage-root", str(roots.research_stage), "--workers", str(DOC_WORKERS)]):
            failed = f"doc_prepass (rc={steps[-1].rc}, 기준 {base})"
    if failed is None and snap_id is not None:
        for table in STAGE_TABLES:
            if not step(f"stage:{table}", [
                    python, "-m", "stage", "--table", table, "--basis", BASIS,
                    "--raw-dir", str(roots.raw_dir), "--stage-root", str(roots.stage),
                    "--snapshot-root", str(roots.snapshots), "--snapshot-id", snap_id,
                    "--baseline", str(roots.research_stage / "baseline.json")]):
                failed = f"stage:{table} (rc={steps[-1].rc})"
                break
        else:
            health["stage"] = _OK
    if failed is None:
        for table in EQUITY_TABLES:
            if not step(f"equity:{table}", [
                    python, "-m", "equity", "--root", str(roots.equity),
                    "--stage-root", str(roots.stage), "--baseline", str(roots.baseline),
                    "build", table, "--basis", BASIS]):
                failed = f"equity:{table} (rc={steps[-1].rc})"
                break
        else:
            health["equity"] = _OK
    payload: dict[str, object] = {
        "date": t, "basis": BASIS,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage_snapshot_id": snap_id,
        "stage_root": str(roots.stage.resolve()), "equity_root": str(roots.equity.resolve()),
        # 실패한 날에도 판 목록을 남긴다(그 시점 current — 직전 저녁 판이 섞일 수 있어 health 로 가른다, handoff 규약)
        "stage_builds": {x: b for x in STAGE_TABLES if (b := _current(roots.stage, x))},
        "equity_builds": {x: b for x in EQUITY_TABLES if (b := _current(roots.equity, x))},
        "health": health, "failed": failed,
        "elapsed_s": {s.name: s.elapsed_s for s in steps},
    }
    _write_handoff(out, payload)
    if failed is None:
        protect = snapshot.current_snapshot_ids(roots.stage)
        gc = snapshot.gc(roots.snapshots, keep=snapshot.KEEP_DEFAULT, protect=protect)
        pruned = _prune_doc_caches(roots.stage, protect)
        pins = inputs.gc_pinned(roots.equity)
        print(f"  저녁 DART 스냅숏 {gc.summary()} · 지난 문서 캐시 {pruned}개 정리 · _pinned 삭제 "
              f"{pins.n_removed}(확보 {pins.freed_bytes / 1e9:.2f}GB)", flush=True)
        for err in pins.errors:
            print(f"  _pinned gc 오류: {err}", flush=True)
    return BoardResult(BoardStatus.OK if failed is None else BoardStatus.FAILED, out, snap_id,
                       tuple(steps), failed)


def main(argv: list[str] | None = None) -> int:
    home = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(prog="python -m daily.evening_dart", description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("build", help="T 저녁 DART 판(stage 16 · equity 6) → 인계")
    b.add_argument("--date", required=True, help="T(YYYYMMDD)")
    defaults = default_roots(home)
    b.add_argument("--model-db", type=Path, default=defaults.model_db)
    b.add_argument("--raw-dir", type=Path, default=defaults.raw_dir)
    b.add_argument("--research-stage-root", type=Path, default=defaults.research_stage)
    b.add_argument("--baseline", type=Path, default=defaults.baseline)
    a = ap.parse_args(argv)
    try:
        r = build(a.date, Roots(a.model_db, a.raw_dir, a.research_stage_root, a.baseline))
    except ValueError as e:
        print(f"저녁 DART 판 인자 오류: --date={a.date!r} ({e})", file=sys.stderr)
        return 2
    print(r.summary(), flush=True)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
