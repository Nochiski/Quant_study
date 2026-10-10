"""v3 `quant.db` 제자리 반영 — 스테이징 → 게이트 → 표 한 트랜잭션 (컷오버 트랙 QL-F).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-F·QL-F2 · T-16 · T-27 · T-31 · T-34 · T-35 · T-38,
로드맵 §8 K3-2·K3-3.
`scripts/v3_post.sh` 가 단계마다 부른다(`python -m compat stage` · `v3-tables` · `export --in-place` · `apply`).

왜 compat 을 v3 파일에 직접 돌리지 않는가:
  · compat 은 표마다 따로 커밋한다(`quant_db._upsert`). v3 파일에 직접 쓰면 반영 도중 소비자(브리핑·export·
    uni rsync)가 일부 표만 바뀐 상태를 읽고, 중간 표에서 실패하면 그 반쪽이 남는다(G9).
  · 필수 열(NOT NULL ∪ PK)이 빈 행을 5% 까지 조용히 건너뛴다(G11, `MAX_SKIP_RATIO`).
그래서 v3 파일의 온라인 백업 사본(스테이징)에 compat 을 돌리고, 게이트를 통과할 때만 표들을 v3 파일에
**한 트랜잭션**으로 옮긴다. 그림자 compat(별도 파일 `data/compat/quant.db`)의 5% 허용은 그대로 둔다.

반영 표(T-34 · T-38): 기본은 compat 9표 전부. 셸이 compat 에 넘기는 `--tables` 와 게이트·반영이 보는 표 목록은
  같은 함수(`tables_for`)에서 나온다.
  · `--basis morning`(다음 날 아침 KRX 확정 재반영)은 본 파일에 같은 D 의 **점수 두 표를 반영한** 장 마감
    (evening) ok 기록이 있으면 점수 두 표를 뺀 7표다 — 저녁에 보낸 엑셀과 v3 DB 점수가 같게 두고, 가격 재반영이
    아침 모델 판 실패에 묶이지 않게 한다. 그런 기록이 없으면 아침 모델 판 점수를 쓴다(T-7 대체 발송과 같은 뜻).
    점수 표를 반영했는지는 기록의 `tables`(compat 이 쓴 표 → 결과) 키로 본다.
  · 점수 없는 저녁 반영(`scores=False`, 셸 `--no-scores` — T-38): 21:05 원장 뒤 재반영(refill)은 늘 이 모드로
    가격 등 7표만 반영한다 — 점수는 장 마감 반영(⑥)만 쓴다. compat 이 점수 표를 고르지 않으므로 장 마감 모델 판이
    없는 날(판 실패일·세션 예외일 T-26)에도 돈다. 이 기록은 점수 표가 없으므로 그날 ⑥ 의 점수 포함 기록이 없으면
    다음 날 아침 재반영이 점수를 채운다. 순서(T-35)는 (D, evening) 그대로다. `evening` 전용(아침은 T-34 가 정한다).

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
    status ok · date·basis 가 요청과 같다 · 창 끝 = D
  · 순서(T-35): 본 파일에 이번보다 (날짜, basis — 같은 날은 morning > evening) 가 큰 ok 반영 기록이 없다.
    락 대기가 길어 옛 D 가 나중에 반영되면 확정값이 조용히 옛 값으로 돌아간다. 재생은 `allow_older`
  · 기록에 반영 표가 전부 있고 표마다 넣은 행 > 0 · **필수 열이 빈 행을 건너뛴 수 0**(P1 — 빈 행을 빼고
    나머지만 넣으면 v3 소비자는 그 종목이 '없는' 줄로 읽는다)
  · 기록에 반영 표 밖의 표가 없다 — 기록은 본 파일에 그대로 옮겨지고 T-34 가 그 표 목록으로 점수 반영 여부를
    판정한다. 옮기지 않은 점수 표가 기록에 있으면 다음 날 아침이 점수를 건너뛴다(QL-F2)
  · 신선도(T-31 ③): compat 이 이번에 `daily_prices` 에 **쓴** trade_date = D 행 ≥ 1(`metrics.n_on_date`).
    스테이징은 본 파일 사본이라 'D 행이 있다' 만으로는 옛 D 행에도 참이 된다. 없으면 07:00 브리핑이 D−1 장을
    오늘 장으로 보고한다(DEFECT-C02). 비율 하한은 두지 않는다(새 정지 조건이라)
  · 표마다 반영 범위 행 > 0 — 점수 두 표는 `score_date = D` 행(점수 행 > 0)

v3 파일은 열 때마다 읽기 전용 URI(`mode=ro`)거나 쓰기 전용(`mode=rw` — 없으면 만들지 않는다)이다.
스테이징 경로가 v3 본 파일(또는 그 -wal/-shm/-journal)과 같은 파일이면 거부한다 — 스테이징을 뜨기 전에
그 경로를 지우므로 본 파일이 사라진다(QL-F 리뷰 MAJOR-1).
"""
from __future__ import annotations

import json
import os
import shutil
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
SCORE_TABLES = tuple(_DATE_COLUMN)
# 같은 날짜 안 반영 순서(T-35) — 아침 KRX 확정이 장 마감 판보다 나중이다.
_BASIS_RANK = {"evening": 0, "morning": 1}
# 본 파일 쓰기 락 대기(ms). v3 의 짧은 쓰기(pipeline_runs·research_reports 등)가 끝나기를 기다린다.
BUSY_TIMEOUT_MS = 60_000
# 스테이징을 뜨기 전에 확보할 여유 — 본 파일(+ -wal) 크기의 배수. 사본 1 + compat 쓰기(WAL) 여유.
DISK_FACTOR = 2
_SIDECARS = ("", "-wal", "-shm", "-journal")


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
    tables: tuple[str, ...] = ()                # 반영 표(T-34)
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
                f"tables={len(self.tables)} exported_at={self.exported_at} window={win} | {counts}")


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _require(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} 이 없다: {path}")


def _iso(date: str) -> str:
    return datetime.strptime(date, "%Y%m%d").date().isoformat()


def _same_file(a: Path, b: Path) -> bool:
    """경로가 같은 파일인가 — 링크(심볼릭·하드)와 `..` 를 풀어 본다. 없는 경로는 문자열로만 비교."""
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def guard_paths(v3_db: Path, staging: Path) -> None:
    """스테이징이 v3 본 파일·그 사이드카와 같은 파일이면 `CompatError`(MAJOR-1).

    스테이징을 뜨기 전에 스테이징 경로와 -wal/-shm 을 지우므로, 같은 파일이면 본 파일(또는 아직 체크포인트
    안 된 -wal — 커밋된 데이터)이 사라진다.
    """
    for a in _SIDECARS:
        for b in _SIDECARS:
            if _same_file(Path(f"{staging}{a}"), Path(f"{v3_db}{b}")):
                raise CompatError(f"스테이징 경로가 v3 본 파일과 같은 파일이다: {staging}{a} = "
                                  f"{v3_db}{b} — 스테이징을 뜨기 전에 지우므로 거부한다")


def _clear(out: Path) -> None:
    for s in _SIDECARS:
        Path(f"{out}{s}").unlink(missing_ok=True)


def snapshot(v3_db: Path, out: Path) -> None:
    """v3 본 파일 → 스테이징 사본(sqlite 온라인 백업 — CLI `.backup` 과 같은 API, 한 번에 전 페이지).

    순서: 경로 가드 → 디스크 여유(본 파일 + -wal 크기 × `DISK_FACTOR`) → 앞 실행이 남긴 스테이징과 -wal/-shm
    지우기(옛 -wal 이 새 사본에 다시 적용되면 사본이 깨진다) → 백업. 백업이 실패하면 부분 사본을 지운다.
    본 파일은 읽기 전용으로 연다(닫을 때 체크포인트로 바이트가 바뀌지 않게).
    """
    v3_db, out = Path(v3_db), Path(out)
    _require(v3_db, "v3 quant.db")
    guard_paths(v3_db, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wal = Path(f"{v3_db}-wal")
    need = (v3_db.stat().st_size + (wal.stat().st_size if wal.exists() else 0)) * DISK_FACTOR
    # 곧 지울 앞 실행의 스테이징(같은 경로)은 여유로 친다 — 매일 같은 경로를 덮으므로
    stale = sum(Path(f"{out}{s}").stat().st_size for s in _SIDECARS if Path(f"{out}{s}").exists())
    free = shutil.disk_usage(out.parent).free + stale
    if free < need:
        raise CompatError(f"디스크 여유 부족: {out.parent} 여유 {free:,} bytes < 본 파일 크기 × "
                          f"{DISK_FACTOR} = {need:,} bytes — 스테이징을 뜨지 않는다(본 파일 무변경)")
    _clear(out)
    try:
        src, dst = _ro(v3_db), sqlite3.connect(str(out))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except BaseException:
        _clear(out)
        raise


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


def _tables_for(main_rows: list[dict], d_iso: str, basis: str,
                scores: bool = True) -> tuple[str, ...]:
    """반영 표 — 점수 없는 반영(T-38)은 7표. 아침 재반영은 같은 D 의 점수 두 표를 반영한 장 마감 ok 기록이 본
    파일에 있으면 점수 두 표를 뺀다(T-34)."""
    seven = tuple(t for t in TABLES if t not in SCORE_TABLES)
    if not scores:
        if basis != "evening":
            raise CompatError(f"점수 없는 반영은 evening 전용이다(받은 basis {basis}) — 아침 반영 표는 "
                              "T-34 가 정한다(T-38)")
        return seven
    if basis == "morning" and any(
            r.get("date") == d_iso and r.get("basis") == "evening" and r.get("status") == "ok"
            and set(SCORE_TABLES) <= set(json.loads(r["tables"])) for r in main_rows):
        return seven
    return TABLES


def _main_rows(v3_db: Path) -> list[dict]:
    con = _ro(v3_db)
    try:
        return _meta_rows(con)
    finally:
        con.close()


def tables_for(v3_db: Path, date: str, basis: str, scores: bool = True) -> tuple[str, ...]:
    """이번 반영 표 — 셸이 compat `--tables` 로 넘기는 목록(게이트·반영과 같은 판정)."""
    v3_db = Path(v3_db)
    _require(v3_db, "v3 quant.db")
    return _tables_for(_main_rows(v3_db), _iso(date), basis, scores)


def _newer(main_rows: list[dict], d_iso: str, basis: str) -> list[tuple[str, str]]:
    """T-35 — 본 파일 ok 반영 기록 중 이번(d_iso, basis)보다 순서가 뒤인 것."""
    mine = (d_iso, _BASIS_RANK[basis])
    return sorted({(str(r["date"]), str(r["basis"])) for r in main_rows
                   if r.get("status") == "ok" and r.get("basis") in _BASIS_RANK
                   and (str(r["date"]), _BASIS_RANK[str(r["basis"])]) > mine})


def gate(staging: Path, v3_db: Path, date: str, basis: str,
         allow_older: bool = False, scores: bool = True) -> GateReport:
    """스테이징을 읽어 게이트를 판정한다(쓰기 없음). 본 파일은 반영 기록 확인에만 읽는다."""
    staging, v3_db = Path(staging), Path(v3_db)
    _require(staging, "스테이징")
    _require(v3_db, "v3 quant.db")
    d_iso = _iso(date)
    main_rows = _main_rows(v3_db)
    tables = _tables_for(main_rows, d_iso, basis, scores)
    seen = {r["exported_at"] for r in main_rows}
    fails: list[str] = []
    newer = [] if allow_older else _newer(main_rows, d_iso, basis)
    if newer:
        fails.append(f"순서(T-35): 본 파일에 이번({d_iso} {basis})보다 나중 반영 기록 {newer[-1]} 이 "
                     "있다 — 옛 D 를 늦게 반영하면 확정값이 옛 값으로 돌아간다(재생은 --allow-older)")
    stg = _ro(staging)
    try:
        new = [r for r in _meta_rows(stg) if r["exported_at"] not in seen]
        if len(new) != 1:
            fails.append("compat 기록 없음 — 스테이징 _compat_meta 에 이번 실행 행이 없다(compat 이 안 "
                         "돌았다)" if not new else
                         f"compat 기록이 {len(new)}행이다(이번 실행 1행이어야 한다)")
            return GateReport(d_iso, basis, tables, failures=tuple(fails))
        meta = new[0]
        win = json.loads(meta["window"])
        window = (str(win["from_date"]), str(win["to_date"]))
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
        extra = sorted(set(written) - set(tables))
        if extra:
            fails.append(f"compat 이 반영 표 밖의 표 {extra} 를 썼다 — 기록이 본 파일에 옮긴 표와 달라진다"
                         "(다음 날 아침 T-34 판정이 기록의 표 목록을 본다)")
        counts: dict[str, int] = {}
        for table in tables:
            res = written.get(table)
            if res is None:
                fails.append(f"{table}: 이번 compat 실행에 없다 — 반영 표를 한 실행으로 반영해야 한다")
                continue
            if int(res["n_rows"]) <= 0:
                fails.append(f"{table}: compat 이 넣은 행 0")
            if int(res["n_skipped"]) != 0:
                fails.append(f"{table}: 필수 열(NOT NULL·PK)이 빈 행 {res['n_skipped']}건을 "
                             "건너뛰었다 — 제자리 반영은 0건이어야 한다(P1)")
            if table == "daily_prices":
                n_on = int((res.get("metrics") or {}).get("n_on_date", 0))
                if n_on < 1:
                    fails.append(f"신선도(T-31 ③): 이번 compat 이 daily_prices 에 쓴 {d_iso} 행 0 — "
                                 "07:00 브리핑이 D−1 장을 오늘로 보고한다(DEFECT-C02)")
            where, params = _scope(table, window, d_iso)
            n = int(stg.execute(f'SELECT count(*) FROM "{table}" WHERE {where}',
                                params).fetchone()[0])
            counts[table] = n
            if n == 0:
                fails.append(f"{table}: 점수 행 0(score_date={d_iso})" if table in SCORE_TABLES
                             else f"{table}: 반영 범위 행 0")
        return GateReport(d_iso, basis, tables, meta["exported_at"], window, counts, tuple(fails))
    finally:
        stg.close()


def _move(staging: Path, v3_db: Path, report: GateReport,
          commit_flag: Path | None = None) -> None:
    """반영 표를 본 파일에 한 트랜잭션으로 옮긴다 — 표별 범위 DELETE → 스테이징 범위 INSERT → 기록 1행.

    `commit_flag` 를 주면 COMMIT 직후 그 파일을 만든다 — 셸이 '본 파일 무변경' 과 'COMMIT 뒤 실패' 를 가른다.
    """
    assert report.window is not None and report.exported_at is not None
    con = sqlite3.connect(f"{v3_db.resolve().as_uri()}?mode=rw", uri=True,
                          isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        con.execute("ATTACH DATABASE ? AS stg", (f"{staging.resolve().as_uri()}?mode=ro",))
        con.execute("BEGIN IMMEDIATE")
        try:
            for table in report.tables:
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
            # 원래 원인을 남긴다 — 트랜잭션이 이미 풀렸는데 ROLLBACK 하면 그 오류가 원인을 덮는다
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        if commit_flag is not None:
            Path(commit_flag).touch()
    finally:
        con.close()


def apply(staging: Path, v3_db: Path, date: str, basis: str, shadow: bool = False,
          allow_older: bool = False, commit_flag: Path | None = None,
          scores: bool = True) -> GateReport:
    """게이트 → (그림자가 아니면) 한 트랜잭션 반영. 게이트 실패는 `V3PostGateError`(본 파일 무변경).
    `scores=False` 는 점수 없는 반영(T-38 — 7표)."""
    guard_paths(Path(v3_db), Path(staging))
    report = gate(staging, v3_db, date, basis, allow_older, scores)
    if not report.ok:
        raise V3PostGateError(report)
    if not shadow:
        _move(Path(staging), Path(v3_db), report, commit_flag)
    return report
