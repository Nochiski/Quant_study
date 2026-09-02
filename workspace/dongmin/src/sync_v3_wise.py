#!/usr/bin/env python3
"""kael-system-v3 컨센서스 4종을 wisereport.db 로 병합 (v3_ 접두, 일 1회).

v3 에만 있는 것: 2026-04-03~ 일별 컨센서스(우리 수집 시작 전 구간)와
analyst_count·opinion_score 필드. v3 원본은 읽기 전용으로만 연다.

날짜 PK 가 이미 있는 테이블은 그대로 append 미러(INSERT OR IGNORE).
날짜 축이 없는 스냅샷 테이블(consensus_annual·compare)은 sync_date 를 붙여
"변경분만" 쌓는다 — v3 가 매일 덮어써 소실시키는 이력이 여기서 살아난다.
"""
import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V3   = "/home/kael/kael-system-v3/data/quant.db"
DB   = os.path.join(BASE, "data", "raw", "wisereport.db")

MIRROR = [  # (원본, 사본, PK) — 날짜가 PK 에 이미 있어 그대로 append
    ("consensus_revision_daily", "v3_consensus_revision_daily",
     ["stock_code", "base_date"]),
    ("analyst_opinions", "v3_analyst_opinions",
     ["stock_code", "snapshot_date"]),
]
SNAP = [    # 날짜 축이 없는 스냅샷 — sync_date 를 붙여 변경분만 적재
    ("consensus_annual", "v3_consensus_annual",
     ["stock_code", "period", "period_type"]),
    ("consensus_revision_compare", "v3_consensus_revision_compare",
     ["stock_code"]),
]


def cols_of(con: sqlite3.Connection, tbl: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({tbl})")]


def row_hash(vals: tuple) -> str:
    return hashlib.sha256("|".join("" if v is None else str(v) for v in vals)
                          .encode()).hexdigest()[:24]


def main() -> None:
    today = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")
    src = sqlite3.connect(f"file:{V3}?mode=ro", uri=True)
    dst = sqlite3.connect(DB, timeout=60)
    dst.execute("PRAGMA busy_timeout=60000")

    for s_tbl, d_tbl, pk in MIRROR:
        cols = cols_of(src, s_tbl)
        ddl = ", ".join(f'"{c}"' for c in cols)
        dst.execute(f'CREATE TABLE IF NOT EXISTS {d_tbl} ({ddl}, copied_at TEXT, '
                    f'PRIMARY KEY ({", ".join(pk)}))')
        rows = src.execute(f"SELECT {ddl} FROM {s_tbl}").fetchall()
        ph = ",".join("?" * (len(cols) + 1))
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        before = dst.execute(f"SELECT COUNT(*) FROM {d_tbl}").fetchone()[0]
        dst.executemany(f"INSERT OR IGNORE INTO {d_tbl} VALUES ({ph})",
                        [list(r) + [ts] for r in rows])
        dst.commit()
        after = dst.execute(f"SELECT COUNT(*) FROM {d_tbl}").fetchone()[0]
        print(f"  {d_tbl:<32} 원본 {len(rows):,} → 신규 {after - before:,} (누적 {after:,})")

    for s_tbl, d_tbl, nk in SNAP:
        cols = cols_of(src, s_tbl)
        ddl = ", ".join(f'"{c}"' for c in cols)
        dst.execute(f'CREATE TABLE IF NOT EXISTS {d_tbl} (sync_date TEXT NOT NULL, {ddl}, '
                    f'row_hash TEXT NOT NULL, copied_at TEXT, '
                    f'PRIMARY KEY (sync_date, {", ".join(nk)}))')
        rows = src.execute(f"SELECT {ddl} FROM {s_tbl}").fetchall()
        # 자연키별 최신 해시 — 같으면 오늘자 적재를 건너뛴다 (변경분만 쌓는 원칙)
        nk_idx = [cols.index(c) for c in nk]
        latest: dict[tuple, str] = {}
        for sd, *rest in dst.execute(
                f'SELECT sync_date, {", ".join(nk)}, row_hash FROM {d_tbl} ORDER BY sync_date'):
            latest[tuple(rest[:-1])] = rest[-1]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        ins, ph = [], ",".join("?" * (len(cols) + 3))
        for r in rows:
            h = row_hash(r)
            if latest.get(tuple(r[i] for i in nk_idx)) == h:
                continue
            ins.append([today] + list(r) + [h, ts])
        dst.executemany(f"INSERT OR REPLACE INTO {d_tbl} VALUES ({ph})", ins)
        dst.commit()
        total = dst.execute(f"SELECT COUNT(*) FROM {d_tbl}").fetchone()[0]
        print(f"  {d_tbl:<32} 원본 {len(rows):,} → 변경분 {len(ins):,} (누적 {total:,})")

    src.close()
    dst.close()


if __name__ == "__main__":
    main()
