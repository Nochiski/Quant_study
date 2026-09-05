#!/usr/bin/env python3
"""키움 ka10099 종목 마스터 일별 스냅샷 — kiwoom 원장.

하루 2콜(코스피·코스닥)로 전 종목의 상장주식수·관리상태(auditInfo)·증거금/신용
상태(state)·업종·규모구분을 받는다. auditInfo/state 는 DATA_CATALOG 가 소멸성으로
지목한 축이다 — 오늘 상태는 오늘만 관측 가능하므로 일별 append 로 이력을 만든다.
(2026-09-01 스파이크: 코스피 1콜 2,484행 · cont-yn=N · listCount 가 KRX 08-20
스냅샷보다 최신인 04-14 소각 반영값과 일치)

WISE 컨센서스 수집기의 유니버스가 이 테이블의 최신 스냅샷을 읽는다.
원장 원칙: 응답 필드 전부 TEXT 원문 그대로. 보통주 필터 등 해석은 읽는 쪽 몫.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api  # noqa: E402

DB = os.path.join(BASE, "data", "raw", "kiwoom.db")
MARKETS = (("0", "kospi"), ("10", "kosdaq"))
TBL = "ka10099_stock_master"


def main() -> None:
    now = datetime.now(timezone.utc)
    snap = (now + timedelta(hours=9)).strftime("%Y%m%d")
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")

    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")

    total = 0
    for mrkt, label in MARKETS:
        j, hdr = api.kiwoom("ka10099", "/api/dostk/stkinfo", {"mrkt_tp": mrkt})
        rc, rows = j.get("return_code"), j.get("list") or []
        if rc != 0 or not rows:
            print(f"  ✖ {label} 실패 — return_code={rc} msg={j.get('return_msg')} "
                  f"rows={len(rows)}. 이 시장은 적재하지 않는다")
            continue
        if hdr.get("cont-yn") == "Y":
            # 스파이크에서는 N 이었다. Y 가 오면 페이지네이션 구현이 필요하다는 신호다
            print(f"  ! {label} cont-yn=Y — 후속 페이지가 있다. 현재 수신 {len(rows)}행만 적재")

        cols = list(rows[0].keys())
        quoted = ", ".join(f'"{c}" TEXT' for c in cols)
        con.execute(f'CREATE TABLE IF NOT EXISTS {TBL} (snap_date TEXT NOT NULL, '
                    f'mrkt_tp TEXT NOT NULL, {quoted}, collected_at TEXT NOT NULL, '
                    f'PRIMARY KEY (snap_date, "code"))')
        have = {r[1] for r in con.execute(f"PRAGMA table_info({TBL})")}
        for c in cols:
            if c not in have:      # 응답 필드가 늘면 따라간다 (기존 원장 규약)
                con.execute(f'ALTER TABLE {TBL} ADD COLUMN "{c}" TEXT')
        ph = ",".join("?" * (len(cols) + 3))
        con.executemany(
            f'INSERT OR IGNORE INTO {TBL} (snap_date, mrkt_tp, '
            + ", ".join(f'"{c}"' for c in cols) + f", collected_at) VALUES ({ph})",
            [[snap, mrkt] + [str(r.get(c, "")) for c in cols] + [ts] for r in rows])
        con.commit()
        total += len(rows)
        print(f"  ✓ {label}  {len(rows):,}행")

    n = con.execute(f"SELECT COUNT(*) FROM {TBL} WHERE snap_date=?", (snap,)).fetchone()[0]
    print(f"  ── 스냅샷 {snap}  적재 {n:,}행 (수신 {total:,})")
    con.close()


if __name__ == "__main__":
    main()
