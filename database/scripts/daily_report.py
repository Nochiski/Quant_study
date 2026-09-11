#!/usr/bin/env python3
"""통합 일일 리포트 — 원장·stage·빌드·런·디스크·락을 한 메시지로 묶어 텔레그램으로 보낸다.

플랜 v1 `docs/plans/2026-09-09-daily-incremental.md` §9 Task 6.1, 플랜 v2 §2-1 알림 정책(V2-7).
입력은 전부 읽기 전용이다 — JSON 은 열어서 읽기만 하고 `daily_run.db` 는 `mode=ro` 로 연다
(표를 만들지 않으려고 `daily.runlog` 의 열기 경로 대신 직접 연다. 행 모양만 `runlog.Run` 을 재사용).

등급(`docs/COLLECT_PLAN.md` §4-4 · 플랜 v2 §2-1):
  crit — 수집 실패(런 `failed` · 저녁 원장 rc≠0) · 게이트 폐기(stage·빌드 health 실패)
         · `kael` 키 사용(건전성 halt) · 디스크 여유 < 50 GB
  warn — 건전성 warn 항목 실패, 아직 `running` 인 런
  info — 그 밖의 일일 요약

입력 파일이 없으면 "없음" 으로만 적고 등급을 올리지 않는다 — 보고 누락 판정은 워치독(`scripts/watchdog.sh`)
몫이다. 날짜가 D 와 다른 인계 파일(전날 것이 남아 있는 경우)도 같은 이유로 결측 취급한다.

사용: daily_report.py --date YYYYMMDD [--home DIR] [--dry-run] [--lock-glob PATTERN]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum

from daily.runlog import Run

KST = dt.timezone(dt.timedelta(hours=9))
MAX_TEXT = 3900                 # notify.sh 가 텔레그램에 넘기는 상한과 같다
DISK_CRIT_GB = 50.0             # COLLECT_PLAN §4-4 즉시 등급
BASES = ("evening", "morning")  # 잠정판 · 확정판
LOCK_GLOB = "/tmp/quant_ledger_*.lock"


class ReportStatus(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRIT = "crit"


@dataclass(frozen=True)
class ReportResult:
    """리포트 한 건. `status` 가 그대로 notify.sh 의 등급 인자가 된다."""

    status: ReportStatus
    text: str
    missing: tuple[str, ...]


# ── 읽기 도우미 ────────────────────────────────────────────────────────────
def _read_json(path: str) -> dict[str, object] | None:
    """읽기 실패(없음·깨짐·객체 아님)는 전부 None — 호출부가 결측으로 적는다."""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _checks(rep: dict[str, object]) -> list[dict[str, object]]:
    raw = rep.get("checks")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _health_ok(v: object) -> bool | None:
    """빌드·stage 리포트의 통과 여부. 형식이 낯설면 None(판정 보류 — 등급을 올리지 않는다)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("ok", "pass", "passed", "true", "정상"):
            return True
        if s in ("fail", "failed", "error", "폐기"):
            return False
        return None
    if isinstance(v, dict):
        for key in ("ok", "status", "health"):
            if key in v:
                return _health_ok(v[key])
    return None


def _health_text(v: object) -> str:
    if isinstance(v, bool):
        return "ok" if v else "FAIL"
    if isinstance(v, dict):
        inner = _health_ok(v)
        return "형식 미상" if inner is None else ("ok" if inner else "FAIL")
    return str(v)[:40]


def _hhmm_kst(utc_iso: object) -> str:
    """runlog 의 `YYYY-MM-DDTHH:MM:SSZ`(UTC)를 KST HH:MM 으로. 파싱 실패는 원문 그대로."""
    if not isinstance(utc_iso, str) or not utc_iso:
        return "?"
    try:
        stamp = dt.datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    except ValueError:
        return utc_iso[:19]
    return stamp.astimezone(KST).strftime("%H:%M")


def _disk_free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / 1024**3


def _lock_state(path: str) -> str:
    """flock 을 비차단으로 잡아 보고 바로 푼다 — 잡히면 아무도 안 쓰는 것이다."""
    import fcntl
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as e:
        return f"확인 불가({type(e).__name__})"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "점유"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "해제"
    finally:
        os.close(fd)


def _locks_text(lock_glob: str) -> str:
    files = sorted(glob.glob(lock_glob))
    if not files:
        return "락 파일 없음"
    parts = []
    for p in files:
        name = os.path.basename(p).removeprefix("quant_ledger_").removesuffix(".lock")
        parts.append(f"{name}={_lock_state(p)}")
    return "락 " + " ".join(parts)


def _runs(db_path: str, date: str) -> list[Run] | None:
    """D 의 런 기록. 파일·표가 없으면 None(결측)."""
    if not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute(
            "SELECT run_id,date,source,started,ended,n_calls,n_rows,status,detail FROM run "
            "WHERE date=? ORDER BY run_id", (date,)).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()
    return [Run(*r) for r in rows]


# ── 절별 조립 ──────────────────────────────────────────────────────────────
def _ledger_section(rep: dict[str, object] | None) -> tuple[str, list[str], list[str]]:
    if rep is None:
        return "■ 원장 건전성 없음", [], []
    checks = _checks(rep)
    bad = [str(c.get("name")) for c in checks
           if c.get("status") == "fail" and c.get("level") in ("required", "halt")]
    warn = [str(c.get("name")) for c in checks
            if c.get("status") == "fail" and c.get("level") == "warn"]
    summary = rep.get("summary")
    line = "■ " + (summary if isinstance(summary, str) and summary
                   else f"원장 건전성 {rep.get('date')}: {'OK' if rep.get('ok') else 'FAIL'} "
                        f"pass {sum(1 for c in checks if c.get('status') == 'pass')}/{len(checks)}")
    crit = [f"원장 {', '.join(bad)}"] if bad else []
    warns = [f"원장 경고 {', '.join(warn)}"] if warn else []
    return line, crit, warns


def _basis_section(label: str, reports: dict[str, dict[str, object] | None],
                   value: str) -> tuple[str, list[str]]:
    """basis(evening·morning) 두 판을 한 줄로. `value` 는 표시에 쓸 키(stage 는 summary, 빌드는 health)."""
    parts, crit = [], []
    for basis in BASES:
        rep = reports.get(basis)
        if rep is None:
            parts.append(f"{basis} 없음")
            continue
        ok = _health_ok(rep.get(value, rep))
        if value == "health":
            at = str(rep.get("generated_at") or "?")[:19]
            elapsed = rep.get("elapsed_s")
            el = f" {int(elapsed)}초" if isinstance(elapsed, int | float) else ""
            parts.append(f"{basis} {_health_text(rep.get('health'))} {at}{el}")
        else:
            summary = rep.get("summary")
            parts.append(f"{basis} {summary}" if isinstance(summary, str) and summary
                         else f"{basis} {'OK' if ok else 'FAIL' if ok is False else '형식 미상'}")
        if ok is False:
            crit.append(f"{label} {basis} 게이트 폐기")
    return f"■ {label} " + " · ".join(parts), crit


def _evening_section(rep: dict[str, object] | None) -> tuple[str, list[str]]:
    if rep is None:
        return "■ 저녁 원장 없음", []
    bad = [f"{k}={rep.get(k)}" for k in ("kiwoom_rc", "dart_rc", "wise_rc") if rep.get(k) not in (0, None)]
    line = ("■ 저녁 원장 "
            f"키움 rc={rep.get('kiwoom_rc')}({str(rep.get('kiwoom_done_at') or '?')[11:19]}) "
            f"DART rc={rep.get('dart_rc')} "
            f"WISE rc={rep.get('wise_rc')}({str(rep.get('wise_done_at') or '?')[11:19]}) "
            f"종료 {_hhmm_kst(rep.get('finished_at'))}")
    crit = [f"저녁 원장 수집 실패 {' '.join(bad)}"] if bad else []
    return line, crit


def _runs_section(runs: list[Run] | None) -> tuple[str, list[str], list[str]]:
    if runs is None:
        return "■ 런 기록 없음", [], []
    if not runs:
        return "■ 런 기록 0건", [], []
    parts = [f"{r.source} {r.status}({_hhmm_kst(r.started)}→{_hhmm_kst(r.ended)})" for r in runs]
    failed = sorted({r.source for r in runs if r.status not in ("ok", "running")})
    running = sorted({r.source for r in runs if r.status == "running"})
    crit = [f"수집 실패 런 {', '.join(failed)}"] if failed else []
    warns = [f"미완 런 {', '.join(running)}"] if running else []
    return "■ 런 " + " · ".join(parts), crit, warns


# ── 본체 ──────────────────────────────────────────────────────────────────
def build_report(home: str, date: str, *, now_kst: dt.datetime | None = None,
                 lock_glob: str = LOCK_GLOB, disk_free_gb: float | None = None) -> ReportResult:
    """D 하루치 입력을 모아 한 메시지와 등급을 만든다. 어떤 입력도 쓰지 않는다(읽기 전용).

    Args:
        home: 파이프라인 루트(`QL_HOME`). 모든 입력 경로의 기준이며 디스크 여유도 이 경로로 잰다.
        date: 대상 거래일 YYYYMMDD.
        now_kst: 작성 시각(테스트 주입용). 기본은 현재 KST.
        lock_glob: 점유 여부를 볼 락 파일 glob.
        disk_free_gb: 디스크 여유 override(테스트 주입용). 기본은 `home` 의 실측.

    Returns:
        ReportResult — 등급·메시지·결측 입력 목록(home 기준 상대 경로).
    """
    now = now_kst or dt.datetime.now(KST)
    free_gb = _disk_free_gb(home) if disk_free_gb is None else disk_free_gb
    missing: list[str] = []

    def _load(rel: str) -> dict[str, object] | None:
        rep = _read_json(os.path.join(home, rel))
        if rep is None:
            missing.append(rel)
            return None
        if str(rep.get("date") or "") not in ("", date):
            missing.append(f"{rel}(D={rep.get('date')} 불일치)")
            return None
        return rep

    ledger = _load(f"logs/health/{date}.json")
    stage = {b: _load(f"logs/health/stage_{date}_{b}.json") for b in BASES}
    latest = {b: _load(f"data/deliver/latest_{b}.json") for b in BASES}
    evening = _load("data/deliver/ledger_evening.json")
    run_rel = "data/raw/daily_run.db"
    runs = _runs(os.path.join(home, run_rel), date)
    if runs is None:
        missing.append(run_rel)

    crit: list[str] = []
    warns: list[str] = []
    lines: list[str] = []
    for section in (_ledger_section(ledger),):
        line, c, w = section
        lines.append(line)
        crit += c
        warns += w
    for label, reports, key in (("stage", stage, "ok"), ("빌드", latest, "health")):
        line, c = _basis_section(label, reports, key)
        lines.append(line)
        crit += c
    line, c = _evening_section(evening)
    lines.append(line)
    crit += c
    line, c, w = _runs_section(runs)
    lines.append(line)
    crit += c
    warns += w
    if free_gb < DISK_CRIT_GB:
        crit.append(f"디스크 여유 {free_gb:,.1f} GB < {DISK_CRIT_GB:,.0f} GB")
    lines.append(f"■ 디스크 여유 {free_gb:,.1f} GB · {_locks_text(lock_glob)}")
    if missing:
        lines.append("■ 없음: " + ", ".join(missing))

    status = ReportStatus.CRIT if crit else (ReportStatus.WARN if warns else ReportStatus.INFO)
    head = [f"일일 리포트 D={date} · 작성 {now.strftime('%m-%d %H:%M')} KST · 판정 {status.value}"]
    if crit:
        head.append("■ 즉시: " + "; ".join(crit))
    if warns:
        head.append("■ 경고: " + "; ".join(warns))
    text = "\n".join(head + lines)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 4] + " …"
    return ReportResult(status, text, tuple(missing))


def _send(home: str, status: ReportStatus, title: str, body: str) -> bool:
    """notify.sh 로 넘긴다. 발송 자체가 실패하면 False(rc 1) — 리포트 내용과는 별개다."""
    notify = os.path.join(home, "scripts", "notify.sh")
    try:
        proc = subprocess.run([notify, status.value, title, body], check=False, capture_output=True, text=True)
    except OSError as e:
        print(f"notify 실행 실패 — path={notify} ({type(e).__name__}: {e})", file=sys.stderr)
        return False
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print(f"notify 발송 실패 rc={proc.returncode} title={title} stderr={proc.stderr.strip()[:200]}",
              file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="통합 일일 리포트")
    ap.add_argument("--date", required=True, help="대상 거래일 YYYYMMDD (보통 T-1)")
    ap.add_argument("--home", default=os.environ.get("QL_HOME", "/home/kael/quant-ledger"))
    ap.add_argument("--dry-run", action="store_true", help="발송 없이 메시지만 출력")
    ap.add_argument("--lock-glob", default=LOCK_GLOB, help=f"락 파일 glob (기본 {LOCK_GLOB})")
    a = ap.parse_args(argv)
    if len(a.date) != 8 or not a.date.isdigit():
        raise ValueError(f"--date 는 YYYYMMDD 여야 한다: got={a.date!r}")
    r = build_report(a.home, a.date, lock_glob=a.lock_glob)
    print(r.text)
    if a.dry_run:
        return 0
    return 0 if _send(a.home, r.status, f"일일 리포트 {a.date}", r.text) else 1


if __name__ == "__main__":
    sys.exit(main())
