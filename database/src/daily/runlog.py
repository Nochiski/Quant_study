"""러너 실행 기록 — `data/raw/daily_run.db`. 건전성 판정·알림이 이 표를 읽는다. 플랜 Task 1.1."""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from dataclasses import dataclass

_DDL = """CREATE TABLE IF NOT EXISTS run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, source TEXT NOT NULL,
  started TEXT NOT NULL, ended TEXT,
  n_calls INTEGER, n_rows INTEGER, status TEXT NOT NULL, detail TEXT)"""


@dataclass(frozen=True)
class Run:
    run_id: int
    date: str
    source: str
    started: str
    ended: str | None
    n_calls: int | None
    n_rows: int | None
    status: str
    detail: str | None


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(db: str | os.PathLike[str]) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.fspath(db)) or ".", exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(_DDL)
    return con


def start(db: str | os.PathLike[str], *, date: str, source: str) -> int:
    con = _open(db)
    try:
        cur = con.execute("INSERT INTO run (date, source, started, status) VALUES (?,?,?,'running')",
                          (date, source, _now()))
        con.commit()
        rid = cur.lastrowid
        if rid is None:
            raise RuntimeError(f"runlog insert returned no rowid: db={db} date={date} source={source}")
        return int(rid)
    finally:
        con.close()


def finish(db: str | os.PathLike[str], run_id: int, *, status: str, n_calls: int | None = None,
           n_rows: int | None = None, detail: str | None = None) -> None:
    con = _open(db)
    try:
        n = con.execute("UPDATE run SET ended=?, status=?, n_calls=?, n_rows=?, detail=? WHERE run_id=?",
                        (_now(), status, n_calls, n_rows, detail, run_id)).rowcount
        con.commit()
        if n != 1:
            raise ValueError(f"runlog finish matched {n} rows: db={db} run_id={run_id}")
    finally:
        con.close()


def recent(db: str | os.PathLike[str], *, source: str | None = None, limit: int = 20) -> list[Run]:
    con = _open(db)
    try:
        sql = "SELECT run_id,date,source,started,ended,n_calls,n_rows,status,detail FROM run"
        args: tuple[object, ...] = ()
        if source is not None:
            sql += " WHERE source=?"
            args = (source,)
        sql += " ORDER BY run_id DESC LIMIT ?"
        rows = con.execute(sql, (*args, limit)).fetchall()
        return [Run(*r) for r in rows]
    finally:
        con.close()
