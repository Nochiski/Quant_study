"""키움 마스터(`ka10099_stock_master`) 기반 요청 유니버스 + 사라진 종목의 유예 규칙. 플랜 R5 / Task 1.1.

보통주 판정 = `upSizeName<>''`(거래소 규모구분은 보통주에만 붙는다) — `backfill_wise.py:182-201` 의
규칙과 같다(그쪽은 import 시점에 .env 를 요구하는 api 를 끌어오므로 여기서 SQL 한 줄을 반복한다).
첫 실행은 `data/jsonl/tickers.txt`(백필 유니버스 2,602)로 시드해 마스터에는 있으나 규모구분이 빈
≈40종목의 갭이 사라지지 않게 한다(리뷰 2차 #2). 마스터에서 사라진 종목은 `grace_days` 거래일 유예하되,
제외는 **그 종목의 `ka10008.max(dt)` 가 마지막 등장일 이상일 때만** — 거래정지 종목은 키움이
`rc=0`+0행을 주므로 시간 만료만으로 빼면 마지막 거래일이 영구 누락된다(A C-4).
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class MasterSnapshot:
    snap_date: str
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class RequestedUniverse:
    asof: str                        # 마스터 스냅샷 날짜
    tickers: tuple[str, ...]         # 오늘 요청할 종목(정렬)
    seeded_only: tuple[str, ...]     # 시드(tickers.txt)에만 있던 종목 — 로그·P6 결정용
    dropped: tuple[str, ...]         # 이번에 유예 만료로 제외한 종목


def kiwoom_common(con_kw: sqlite3.Connection, snap_date: str | None = None) -> MasterSnapshot:
    """최신(또는 지정) 스냅샷의 보통주. 스냅샷이 없으면 ValueError."""
    if snap_date is None:
        row = con_kw.execute("SELECT MAX(snap_date) FROM ka10099_stock_master").fetchone()
        snap_date = row[0] if row else None
    if not snap_date:
        raise ValueError("ka10099_stock_master has no snapshot — master_daily.py has not run")
    rows = con_kw.execute(
        "SELECT code FROM ka10099_stock_master WHERE snap_date=? AND upSizeName<>'' ORDER BY code",
        (snap_date,)).fetchall()
    return MasterSnapshot(str(snap_date), tuple(str(r[0]) for r in rows))


def _read_seed(seed_path: str | os.PathLike[str] | None) -> set[str]:
    if seed_path is None or not os.path.exists(seed_path):
        return set()
    with open(seed_path, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


def _load_state(state_path: str | os.PathLike[str]) -> dict[str, object]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def _max_dt(con_kw: sqlite3.Connection, ticker: str) -> str | None:
    row = con_kw.execute("SELECT MAX(dt) FROM ka10008_foreign_holdings WHERE ticker=?", (ticker,)).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def requested(con_kw: sqlite3.Connection, *, state_path: str | os.PathLike[str],
              seed_path: str | os.PathLike[str] | None, grace_days: int = 5) -> RequestedUniverse:
    """오늘 요청 유니버스를 만들고 상태 파일을 갱신한다.

    상태 파일 = {"asof": snap_date, "grace": {ticker: {"last_seen": 'YYYYMMDD', "missing_days": n}}}.
    grace 는 "마스터에서 사라졌지만 아직 요청 중인" 종목만 담는다.
    """
    snap = kiwoom_common(con_kw)
    today = set(snap.tickers)
    state = _load_state(state_path)
    grace_raw = state.get("grace")
    grace: dict[str, dict[str, object]] = dict(grace_raw) if isinstance(grace_raw, dict) else {}
    first_run = not state
    seeded_only: tuple[str, ...] = ()
    if first_run:
        seed = _read_seed(seed_path)
        extra = sorted(seed - today)
        seeded_only = tuple(extra)
        for t in extra:                      # 시드 전용 종목은 유예 종목으로 취급(마지막 등장일 = 오늘)
            grace[t] = {"last_seen": snap.snap_date, "missing_days": 0}
    # 오늘 다시 나타난 종목은 유예에서 제거
    for t in list(grace):
        if t in today:
            del grace[t]
    # 유예 진행: 카운트 증가, 만료 판정
    dropped: list[str] = []
    for t, g in list(grace.items()):
        prev_missing = g.get("missing_days", 0)
        missing = (int(prev_missing) if isinstance(prev_missing, int | str) else 0) + (0 if first_run else 1)
        g["missing_days"] = missing
        last_seen = str(g.get("last_seen", ""))
        if missing >= grace_days:
            mx = _max_dt(con_kw, t)
            if mx is not None and mx >= last_seen:   # 마지막 거래일 데이터까지 받았다 → 제외
                dropped.append(t)
                del grace[t]
    tickers = tuple(sorted(today | set(grace)))
    os.makedirs(os.path.dirname(os.fspath(state_path)) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"asof": snap.snap_date, "grace": grace}, f, ensure_ascii=False, indent=1)
    return RequestedUniverse(snap.snap_date, tickers, seeded_only, tuple(sorted(dropped)))
