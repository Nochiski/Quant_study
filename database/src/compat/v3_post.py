"""v3 `quant.db` 제자리 반영 — 스테이징 → 게이트 → 9표 한 트랜잭션 (컷오버 트랙 QL-F).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-F · T-16 · T-27, 로드맵 §8 K3-2·K3-3.
`scripts/v3_post.sh` 가 단계마다 부른다(`python -m compat stage` · `export --in-place` · `apply`).

왜 compat 을 v3 파일에 직접 돌리지 않는가:
  · compat 은 표마다 따로 커밋한다(`quant_db._upsert`). v3 파일에 직접 쓰면 반영 도중 소비자(브리핑·export·
    uni rsync)가 일부 표만 바뀐 상태를 읽고, 중간 표에서 실패하면 그 반쪽이 남는다(G9).
  · 필수 열(NOT NULL ∪ PK)이 빈 행을 5% 까지 조용히 건너뛴다(G11, `MAX_SKIP_RATIO`).
그래서 v3 파일의 온라인 백업 사본(스테이징)에 compat 을 돌리고, 게이트를 통과할 때만 9표를 v3 파일에
**한 트랜잭션**으로 옮긴다. 그림자 compat(별도 파일 `data/compat/quant.db`)의 5% 허용은 그대로 둔다.

반영 범위 — compat 이 쓴 범위와 정확히 같다(스테이징 `_compat_meta` 의 이번 실행 기록이 정본):
  · `daily_prices`·`investor_detail_flows`: `trade_date` 가 기록의 창 `[from_date, to_date]` 안
    (compat SQL 의 `date >= from_date AND date <= date` 와 같은 창).
  · `score_history`·`score_history_v2`: `score_date = date`(compat 이 그날 행을 지우고 넣는다 — T-16).
  · 나머지 5표(`stocks`·컨센서스 3표·`financial_summary`): **표 전체**. 날짜 창이 없는 as-of 스냅샷이라
    compat 이 쓰는 행이 날짜 범위로 묶이지 않는다(`stocks` 는 이번 유니버스 밖 행 전부를 `is_active=0`
    으로 바꾼다). 스테이징은 같은 락 안에서 뜬 본 파일 사본이므로 compat 이 안 건드린 행은 같은 값으로
    다시 들어간다.
  compat 이 쓰지 않는 표(`market_*`·`pipeline_runs`·`research_reports` 등 — T-27)는 건드리지 않는다.
  스테이징을 뜬 뒤 v3 가 그 표들에 쓴 행도 그대로 남는다(본 파일을 통째로 바꾸지 않는 이유).

게이트(COMMIT 전 — 하나라도 걸리면 본 파일을 열어 쓰지 않는다):
  · 스테이징 `_compat_meta` 에 이번 실행 기록이 정확히 1행(본 파일에 없던 `exported_at`) ·
    status ok · date·basis 가 요청과 같다
  · 기록에 9표가 전부 있고 표마다 넣은 행 > 0 · **필수 열이 빈 행을 건너뛴 수 0**(P1 — 모호하면 결측:
    빈 행을 빼고 나머지만 넣으면 v3 소비자는 그 종목이 '없는' 줄로 읽는다)
  · 표마다 반영 범위 행 > 0 — 점수 두 표는 `score_date = D` 행(점수 행 > 0)

v3 파일은 열 때마다 읽기 전용 URI(`mode=ro`)거나 쓰기 전용(`mode=rw` — 없으면 만들지 않는다)이다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .mappings import MAPPINGS
from .quant_db import META_DDL, META_TABLE, CompatError, _ensure_meta_columns

# 반영 대상 9표 — compat 매핑 선언이 정본이다(순서도 compat 이 쓰는 순서).
TABLES: tuple[str, ...] = tuple(m.v3_table for m in MAPPINGS)
# 날짜 창 표 → 창 열. 점수 표 → 날짜 열. 여기 없는 표는 표 전체(스냅샷).
_WINDOW_COLUMN = {"daily_prices": "trade_date", "investor_detail_flows": "trade_date"}
_DATE_COLUMN = {"score_history": "score_date", "score_history_v2": "score_date"}
_SCORE_TABLES = tuple(_DATE_COLUMN)
# 본 파일 쓰기 락 대기(ms). v3 의 짧은 쓰기(pipeline_runs·research_reports 등)가 끝나기를 기다린다.
BUSY_TIMEOUT_MS = 60_000


class V3PostGateError(CompatError):
    """게이트 실패 — v3 본 파일에 쓰지 않았다."""

    def __init__(self, report: GateReport) -> None:
        self.report = report
        super().__init__("게이트 실패(v3 본 파일 무변경): " + " · ".join(report.failures))


@dataclass(frozen=True)
class GateReport:
    """게이트 결과. `failures` 가 비면 통과."""

    date: str                                   # 요청 날짜 ISO
    basis: str
    exported_at: str | None = None              # 이번 compat 기록
    window: tuple[str, str] | None = None       # (from_date, to_date) ISO
    counts: dict[str, int] = field(default_factory=dict)   # 표 → 스테이징 반영 범위 행 수
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        win = f"{self.window[0]}~{self.window[1]}" if self.window else "?"
        counts = " ".join(f"{t}={n}" for t, n in self.counts.items())
        head = "통과" if self.ok else "실패"
        return (f"v3_post 게이트 {head} date={self.date} basis={self.basis} "
                f"exported_at={self.exported_at} window={win} | {counts}")


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _require(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} 이 없다: {path}")


def snapshot(v3_db: Path, out: Path) -> None:
    """v3 본 파일 → 스테이징 사본(sqlite 온라인 백업 — CLI `.backup` 과 같은 API, 한 번에 전 페이지).

    본 파일은 읽기 전용으로 연다(닫을 때 체크포인트로 바이트가 바뀌지 않게). 앞 실행이 남긴 스테이징과
    -wal/-shm 은 먼저 지운다 — 옛 -wal 이 새 사본에 다시 적용되면 사본이 깨진다.
    """
    v3_db, out = Path(v3_db), Path(out)
    _require(v3_db, "v3 quant.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    for p in (out, Path(f"{out}-wal"), Path(f"{out}-shm"), Path(f"{out}-journal")):
        p.unlink(missing_ok=True)
    src, dst = _ro(v3_db), sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _scope(table: str, window: tuple[str, str], d_iso: str) -> tuple[str, tuple[str, ...]]:
    """표의 반영 범위 WHERE 절과 인자(모듈 머리 주석)."""
    if table in _WINDOW_COLUMN:
        col = _WINDOW_COLUMN[table]
        return f'"{col}" >= ? AND "{col}" <= ?', window
    if table in _DATE_COLUMN:
        return f'"{_DATE_COLUMN[table]}" = ?', (d_iso,)
    return "1", ()


def _meta_rows(con: sqlite3.Connection) -> list[dict]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (META_TABLE,)).fetchone():
        return []
    cur = con.execute(f"SELECT * FROM {META_TABLE}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def gate(staging: Path, v3_db: Path, date: str, basis: str) -> GateReport:
    """스테이징을 읽어 게이트를 판정한다(쓰기 없음). 본 파일은 이전 기록 확인에만 읽는다."""
    staging, v3_db = Path(staging), Path(v3_db)
    _require(staging, "스테이징")
    _require(v3_db, "v3 quant.db")
    d_iso = datetime.strptime(date, "%Y%m%d").date().isoformat()
    con = _ro(v3_db)
    try:
        seen = {r["exported_at"] for r in _meta_rows(con)}
    finally:
        con.close()
    stg = _ro(staging)
    try:
        new = [r for r in _meta_rows(stg) if r["exported_at"] not in seen]
        if len(new) != 1:
            why = ("compat 기록 없음 — 스테이징 _compat_meta 에 이번 실행 행이 없다(compat 이 안 돌았다)"
                   if not new else f"compat 기록이 {len(new)}행이다(이번 실행 1행이어야 한다)")
            return GateReport(d_iso, basis, failures=(why,))
        meta = new[0]
        win = json.loads(meta["window"])
        window = (str(win["from_date"]), str(win["to_date"]))
        fails: list[str] = []
        if meta["status"] != "ok":
            fails.append(f"compat 실패 기록(status={meta['status']}, "
                         f"failed_table={meta['failed_table']})")
        if meta["date"] != d_iso:
            fails.append(f"compat 기록 날짜 {meta['date']} ≠ 요청 {d_iso}")
        if meta["basis"] != basis:
            fails.append(f"compat 기록 basis {meta['basis']} ≠ 요청 {basis}")
        if window[1] != d_iso:
            fails.append(f"compat 창 끝 {window[1]} ≠ 요청 {d_iso}")
        written = json.loads(meta["tables"])
        counts: dict[str, int] = {}
        for table in TABLES:
            res = written.get(table)
            if res is None:
                fails.append(f"{table}: 이번 compat 실행에 없다 — 9표를 한 실행으로 반영해야 한다")
                continue
            if int(res["n_rows"]) <= 0:
                fails.append(f"{table}: compat 이 넣은 행 0")
            if int(res["n_skipped"]) != 0:
                fails.append(f"{table}: 필수 열(NOT NULL·PK)이 빈 행 {res['n_skipped']}건을 "
                             "건너뛰었다 — 제자리 반영은 0건이어야 한다(P1)")
            where, params = _scope(table, window, d_iso)
            n = int(stg.execute(f'SELECT count(*) FROM "{table}" WHERE {where}',
                                params).fetchone()[0])
            counts[table] = n
            if n == 0:
                fails.append(f"{table}: 점수 행 0(score_date={d_iso})" if table in _SCORE_TABLES
                             else f"{table}: 반영 범위 행 0")
        return GateReport(d_iso, basis, meta["exported_at"], window, counts, tuple(fails))
    finally:
        stg.close()


def _move(staging: Path, v3_db: Path, report: GateReport) -> None:
    """9표를 본 파일에 한 트랜잭션으로 옮긴다 — 표별 범위 DELETE → 스테이징 범위 INSERT → 기록 1행."""
    assert report.window is not None and report.exported_at is not None
    con = sqlite3.connect(f"{v3_db.resolve().as_uri()}?mode=rw", uri=True,
                          isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        con.execute("ATTACH DATABASE ? AS stg", (f"{staging.resolve().as_uri()}?mode=ro",))
        con.execute("BEGIN IMMEDIATE")
        try:
            for table in TABLES:
                where, params = _scope(table, report.window, report.date)
                cols = ", ".join(f'"{r[1]}"' for r in
                                 con.execute(f'PRAGMA stg.table_info("{table}")'))
                con.execute(f'DELETE FROM main."{table}" WHERE {where}', params)
                con.execute(f'INSERT INTO main."{table}" ({cols}) '
                            f'SELECT {cols} FROM stg."{table}" WHERE {where}', params)
            con.execute(META_DDL)
            _ensure_meta_columns(con)
            cols = ", ".join(f'"{r[1]}"' for r in
                             con.execute(f"PRAGMA stg.table_info({META_TABLE})"))
            con.execute(f"INSERT INTO main.{META_TABLE} ({cols}) "
                        f"SELECT {cols} FROM stg.{META_TABLE} WHERE exported_at = ?",
                        (report.exported_at,))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("DETACH DATABASE stg")
    finally:
        con.close()


def apply(staging: Path, v3_db: Path, date: str, basis: str,
          shadow: bool = False) -> GateReport:
    """게이트 → (그림자가 아니면) 한 트랜잭션 반영. 게이트 실패는 `V3PostGateError`(본 파일 무변경)."""
    report = gate(staging, v3_db, date, basis)
    if not report.ok:
        raise V3PostGateError(report)
    if not shadow:
        _move(Path(staging), Path(v3_db), report)
    return report
