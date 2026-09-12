"""stage 건전성 C1~C5 — `python -m stage.health` (v1 플랜 P4 Task 4.3 / v2 Task B.1).

판정은 **MANIFEST.json 과 `_failed/` 만** 읽는다. 원장·스냅샷·parquet 를 열지 않으므로 비용이 0이고
빌드 뒤 아무 때나 돌려도 같은 답이 나온다. 그래서 C4 "재현성" 도 재빌드가 아니라 다음 술어다 —
**직전 판 대비 소스 계수(n_src·n_dedup·n_reject)가 하나도 안 움직인 표는 content_hash 도 같아야
한다.** 판정 단위는 파일 mtime 이 아니라 테이블이다(원장 5개를 매일 쓰므로 `_meta.src_mtime` 은
항상 바뀌어 공집합이 된다 — v1 리뷰 2차 #3).

  C1  선언된 표 전부가 오늘(KST) 판이고 basis 가 일치한다
  C2  오늘 날짜의 `_failed/<build_id>.json` 이 0건이다
  C3  append_only 표의 행수가 직전 판보다 줄지 않았다
  C4  소스 계수가 동결된 표는 content_hash 도 동결이다
  C5  빌드 소요가 예산(기본 40분) 안이다

프리패스 캐시가 없어 `run_stage_all.sh` 가 건너뛴 문서층 4표처럼 **의도적으로 안 지은 표**는
`--skip` 으로 빼며, 뺀 사실은 리포트에 남는다(조용히 통과시키지 않는다).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import manifest, model, rules

KST = dt.timezone(dt.timedelta(hours=9))
BUDGET_S_DEFAULT = 2400          # C5 — 스냅샷 3.8분 + 전량 빌드 22.5분 실측에 여유를 준 40분
# 문서층 파싱 로그는 파싱 순서·소요가 행에 들어가 소스가 동결돼도 해시가 흔들린다 (v1 Task 4.3)
C4_EXCLUDE = frozenset({"stg_doc_parse_log"})


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    metrics: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status.value, "detail": self.detail,
                "metrics": dict(self.metrics)}


@dataclass(frozen=True)
class StageHealth:
    date: str
    basis: str
    stage_root: str
    checks: tuple[Check, ...]
    built_on: str = ""      # 판이 커밋된 KST 날짜. 아침 확정판은 date(T-1) 와 다르다 — C1·C2·C5 의 기준

    @property
    def ok(self) -> bool:
        return not any(c.status is Status.FAIL for c in self.checks)

    def summary(self) -> str:
        n_pass = sum(1 for c in self.checks if c.status is Status.PASS)
        head = (f"stage 건전성 {self.date}/{self.basis}: {'OK' if self.ok else 'FAIL'} "
                f"pass {n_pass}/{len(self.checks)}")
        bad = [f"{c.name} {c.detail}" for c in self.checks if c.status is Status.FAIL]
        skipped = [c.name for c in self.checks if c.status is Status.SKIP]
        if bad:
            head += " | 실패: " + "; ".join(bad)
        if skipped:
            head += " | 건너뜀: " + ", ".join(skipped)
        return head

    def as_dict(self) -> dict[str, object]:
        return {"date": self.date, "basis": self.basis, "built_on": self.built_on,
                "stage_root": self.stage_root, "ok": self.ok, "summary": self.summary(),
                "checks": [c.as_dict() for c in self.checks]}


@dataclass(frozen=True)
class _Pair:
    """표 하나의 현재 판과 그 직전 판."""

    table: str
    current: manifest.BuildRecord | None
    previous: manifest.BuildRecord | None


def _pair(stage_root: Path, table: str) -> _Pair:
    m = manifest.load(stage_root / table / "MANIFEST.json")
    cur = prev = None
    for i, b in enumerate(m.builds):
        if b.build_id == m.current_build:
            cur, prev = b, (m.builds[i - 1] if i else None)
    return _Pair(table, cur, prev)


def _kst_date(rec: manifest.BuildRecord) -> str | None:
    """판이 커밋된 KST 날짜. 아침 판은 UTC 로 전날이라 시간대 변환을 건너뛰면 안 된다."""
    try:
        when = dt.datetime.fromisoformat(rec.built_at_utc)
    except ValueError:
        when = model.build_id_time(rec.build_id)
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(KST).strftime("%Y%m%d")


def _listed(label: str, names: list[str], limit: int = 8) -> str:
    """한 줄 요약용 목록 — 전량은 metrics 에 있고 여기는 앞 몇 개만. 알림 본문이 900자에 잘린다."""
    if not names:
        return ""
    head = ", ".join(names[:limit])
    more = f" 외 {len(names) - limit}표" if len(names) > limit else ""
    return f" | {label} {head}{more}"


def _g1(rec: manifest.BuildRecord) -> dict[str, object] | None:
    for g in rec.gates:
        if g.get("name") == "G1":
            m = g.get("metrics")
            return dict(m) if isinstance(m, dict) else None
    return None


def _c1_fresh(pairs: list[_Pair], basis: str, date_kst: str, skipped: list[str]) -> Check:
    missing = [p.table for p in pairs if p.current is None]
    stale = [p.table for p in pairs
             if p.current is not None and _kst_date(p.current) != date_kst]
    mismatch = [p.table for p in pairs
                if p.current is not None and _kst_date(p.current) == date_kst
                and p.current.basis != basis]
    bad = missing + stale + mismatch
    detail = (f"{len(pairs) - len(bad)}/{len(pairs)}표가 {date_kst} {basis} 판"
              + _listed("판 없음", missing) + _listed("오늘 판 아님", stale)
              + _listed("basis 불일치", mismatch) + _listed("건너뜀", skipped))
    return Check("C1", Status.FAIL if bad else Status.PASS, detail,
                 {"n_tables": len(pairs), "n_ok": len(pairs) - len(bad), "missing": missing,
                  "stale": stale, "basis_mismatch": mismatch, "skipped": skipped})


def _c2_failed(stage_root: Path, date_kst: str) -> Check:
    d = stage_root / "_failed"
    files = sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
    today = []
    for bid in files:
        when = model.build_id_time(bid)
        if when is not None and when.astimezone(KST).strftime("%Y%m%d") == date_kst:
            today.append(bid)
    detail = f"오늘 게이트 폐기 {len(today)}건 (누적 {len(files)}건)" + _listed("폐기", today)
    return Check("C2", Status.FAIL if today else Status.PASS, detail,
                 {"today": today, "n_files": len(files)})


def _c3_monotonic(pairs: list[_Pair], write_modes: Mapping[str, str]) -> Check:
    decreased: list[dict[str, object]] = []
    n_checked = 0
    for p in pairs:
        if write_modes.get(p.table) != "append_only" or p.current is None or p.previous is None:
            continue
        n_checked += 1
        if p.current.n_rows < p.previous.n_rows:
            decreased.append({"table": p.table, "previous": p.previous.n_rows,
                              "current": p.current.n_rows})
    detail = f"append_only {n_checked}표 행수 비감소" + _listed(
        "감소", [f"{d['table']} {d['previous']}→{d['current']}" for d in decreased])
    return Check("C3", Status.FAIL if decreased else Status.PASS, detail,
                 {"n_checked": n_checked, "decreased": decreased})


def _c4_frozen(pairs: list[_Pair], unversioned: Collection[str] = ()) -> Check:
    """소스 계수가 안 움직인 표는 해시도 그대로여야 한다 — 재빌드 없이 재현성을 본다.

    `unversioned`(콜·유닛 로그, `versioned=False`)는 대조하지 않는다 — 원장 로그는 행이 늘지 않아도
    상태 컬럼이 제자리에서 바뀐다(`ingest_log` 유닛 status). 09-12 아침 `stg_units_dart` 가 계수 동일·
    해시 상이로 C4 를 깨뜨렸다.
    """
    keys = ("n_src", "n_dedup", "n_reject")
    mismatched: list[dict[str, object]] = []
    n_frozen = n_compared = 0
    for p in pairs:
        if p.table in C4_EXCLUDE or p.table in unversioned or p.current is None or p.previous is None:
            continue
        cur_g1, prev_g1 = _g1(p.current), _g1(p.previous)
        if cur_g1 is None or prev_g1 is None:
            continue                       # G1 계수가 없는 판(구 레코드) — 판정 불가
        n_compared += 1
        if any(cur_g1.get(k) != prev_g1.get(k) for k in keys):
            continue                       # 소스가 자랐다 — 해시가 달라야 정상
        n_frozen += 1
        if p.current.content_hash != p.previous.content_hash:
            mismatched.append({"table": p.table, "previous": p.previous.content_hash,
                               "current": p.current.content_hash})
    detail = f"소스 동결 {n_frozen}표 / 대조 {n_compared}표" + _listed(
        "해시 불일치", [f"{d['table']} {d['previous']}→{d['current']}" for d in mismatched])
    return Check("C4", Status.FAIL if mismatched else Status.PASS, detail,
                 {"n_frozen": n_frozen, "n_compared": n_compared, "mismatched": mismatched})


def _c5_elapsed(pairs: list[_Pair], date_kst: str, started_at: str | None,
                budget_s: int) -> Check:
    """시작 = 체인이 준 `started_at`(스냅샷 시작) 또는 오늘 첫 판의 빌드 id 시각. 끝 = 마지막 커밋."""
    today = [p.current for p in pairs
             if p.current is not None and _kst_date(p.current) == date_kst]
    if not today:
        return Check("C5", Status.SKIP, f"{date_kst} 에 커밋된 판이 없다", {"n_builds": 0})
    starts = [t for t in (model.build_id_time(r.build_id) for r in today) if t is not None]
    ends = []
    for r in today:
        try:
            ends.append(dt.datetime.fromisoformat(r.built_at_utc))
        except ValueError:
            continue
    if started_at:
        begin = dt.datetime.fromisoformat(started_at)
    elif starts:
        begin = min(starts)
    else:
        return Check("C5", Status.SKIP, "빌드 시작 시각을 읽을 수 없다 (--started-at 로 주면 된다)",
                     {"n_builds": len(today)})
    if not ends:
        return Check("C5", Status.SKIP, "커밋 시각을 읽을 수 없다", {"n_builds": len(today)})
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=dt.UTC)
    end = max(e if e.tzinfo else e.replace(tzinfo=dt.UTC) for e in ends)
    elapsed = (end - begin).total_seconds()
    # 예산 초과는 기록형이다(2026-09-12 계약 변경): 3차 재빌드에서 stage 42.8분 > 40분으로 C5 가 FAIL 해 옳은
    # 판을 통째로 버리고 equity 를 건너뛰었다. 느린 것은 슬롯을 놓친 것이지 판이 틀린 것이 아니다 — 워치독이
    # 시각으로 잡고, 여기서는 `over_budget` 로 남겨 소요 추세를 본다.
    over = elapsed > budget_s
    return Check("C5", Status.PASS,
                 f"{len(today)}표 {elapsed / 60:.1f}분 (예산 {budget_s / 60:.0f}분"
                 f"{' — 초과, 기록만' if over else ''})",
                 {"elapsed_s": elapsed, "budget_s": budget_s, "over_budget": over,
                  "n_builds": len(today),
                  "started_at": begin.isoformat(timespec="seconds"),
                  "finished_at": end.isoformat(timespec="seconds")})


def check_stage(stage_root: Path, basis: str, date_kst: str, *,
                built_on: str | None = None,
                tables: Mapping[str, str] | None = None, skip: Collection[str] = (),
                started_at: str | None = None,
                budget_s: int = BUDGET_S_DEFAULT) -> StageHealth:
    """C1~C5 를 판정한다. `tables` 는 표 이름 → write_mode (기본은 stage 규칙 전수).

    `date_kst` 는 대상 거래일(리포트·파일명의 D), `built_on` 은 판이 커밋된 KST 날짜다. 저녁 잠정판은
    둘이 같지만 아침 확정판은 D=T-1 이고 커밋은 T 아침이라 다르다 — C1(오늘 판)·C2(오늘 폐기)·C5(오늘
    소요)는 `built_on` 으로 본다. 생략하면 `date_kst` 와 같다(저녁·수동 빌드 규약).
    """
    if len(date_kst) != 8 or not date_kst.isdigit():
        raise ValueError(f"date must be YYYYMMDD: date_kst={date_kst!r}")
    built_on = built_on or date_kst
    if len(built_on) != 8 or not built_on.isdigit():
        raise ValueError(f"built_on must be YYYYMMDD: built_on={built_on!r}")
    if basis not in model.BASIS_PREFIX:
        raise ValueError(f"unknown build basis: basis={basis!r} "
                         f"allowed={sorted(model.BASIS_PREFIX)}")
    write_modes = dict(tables) if tables is not None else {
        name: rule.write_mode for name, rule in rules.RULES.items()}
    unversioned = {name for name, rule in rules.RULES.items()
                   if name in write_modes and not getattr(rule, "versioned", True)}
    skipped = sorted(set(skip) & set(write_modes))
    judged = [_pair(stage_root, t) for t in sorted(write_modes) if t not in skipped]
    checks = (_c1_fresh(judged, basis, built_on, skipped),
              _c2_failed(stage_root, built_on),
              _c3_monotonic(judged, write_modes),
              _c4_frozen(judged, unversioned),
              _c5_elapsed(judged, built_on, started_at, budget_s))
    return StageHealth(date_kst, basis, str(stage_root), checks, built_on)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(t for t in raw.replace(",", " ").split() if t)


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(description="stage 건전성 C1~C5 (읽기 전용)")
    ap.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    ap.add_argument("--basis", required=True, choices=sorted(model.BASIS_PREFIX))
    ap.add_argument("--date", default=dt.datetime.now(KST).strftime("%Y%m%d"),
                    help="대상 거래일 D (KST YYYYMMDD, 리포트·파일명). 기본은 오늘")
    ap.add_argument("--built-on", default=None,
                    help="판이 커밋된 KST 날짜 (C1·C2·C5 기준). 기본은 --date 와 같다 — 아침 확정판은 오늘을 준다")
    ap.add_argument("--out", type=Path, help="결과 JSON 경로 (기본 logs/health/stage_<D>_<basis>.json)")
    ap.add_argument("--skip", default="", help="의도적으로 안 지은 표 (쉼표·공백 구분)")
    ap.add_argument("--started-at", help="빌드 시작 시각 ISO (C5. 기본은 오늘 첫 판의 빌드 id 시각)")
    ap.add_argument("--budget-s", type=int, default=BUDGET_S_DEFAULT)
    a = ap.parse_args(argv)

    r = check_stage(a.stage_root, a.basis, a.date, built_on=a.built_on, skip=_split(a.skip),
                    started_at=a.started_at, budget_s=a.budget_s)
    out = a.out or base / "logs" / "health" / f"stage_{a.date}_{a.basis}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(r.summary())
    for c in r.checks:
        print(f"  {c.name} {c.status.value:4s} {c.detail}")
    print(f"  리포트 {out}")
    return 0 if r.ok else 2


if __name__ == "__main__":
    sys.exit(main())
