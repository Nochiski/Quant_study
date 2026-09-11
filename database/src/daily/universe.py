"""키움 마스터(`ka10099_stock_master`) 기반 요청 유니버스 + 사라진 종목의 유예 규칙. 플랜 R5 / Task 1.1.

보통주 판정 = `upSizeName<>''`(거래소 규모구분은 보통주에만 붙는다, `backfill_wise.py:182-201` 규칙)
**또는** `marketName IN ('거래소','코스닥') AND 코드 6번째 자리 = '0'`(우선주는 5·7 등, ETF·ETN 은 marketName 이
다르다). 두 번째 조건이 없으면 **신규 상장 종목이 빠진다** — 규모구분은 상장 몇 주 뒤에야 붙는다
(09-09 실측: 스카이랩스 386380 상장 09-04·해치텍 0155E0 08-25·니어스랩 417030 08-24 전부 upSizeName 공백).
첫 실행은 `data/jsonl/tickers.txt`(백필 유니버스 2,602)로 시드해 마스터에는 있으나 규모구분이 빈
≈40종목의 갭이 사라지지 않게 한다(리뷰 2차 #2). 마스터에서 사라진 종목(직전 스냅샷에는 있고 오늘 없는
종목)은 `grace_days` 거래일 동안 계속 요청한 뒤 제외한다. 유예 일수는 **마스터 스냅샷 날짜가 바뀔 때만**
센다 — kw_daily 와 kis_daily 가 같은 상태 파일을 하루 두 번 읽는다(검수 B F-4). 제외 시점에 그 종목의
`ka10008.max(dt)` 가 마지막 등장일보다 앞이면 `tail_missing` 으로 알린다. 폐지 종목은 키움이 `rc=0`+0행을
주므로 더 기다려도 꼬리는 오지 않는다(검수 B F-1) — 조건부 무기한 연장은 4종목을 영구 고착시켰다.
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
    tail_missing: tuple[str, ...] = ()   # dropped 중 마지막 등장일 데이터를 끝내 못 받은 종목


def kiwoom_common(con_kw: sqlite3.Connection, snap_date: str | None = None) -> MasterSnapshot:
    """최신(또는 지정) 스냅샷의 보통주. 스냅샷이 없으면 ValueError."""
    if snap_date is None:
        row = con_kw.execute("SELECT MAX(snap_date) FROM ka10099_stock_master").fetchone()
        snap_date = row[0] if row else None
    if not snap_date:
        raise ValueError("ka10099_stock_master has no snapshot — master_daily.py has not run")
    rows = con_kw.execute(
        "SELECT code FROM ka10099_stock_master WHERE snap_date=? AND ("
        " upSizeName<>'' OR (marketName IN ('거래소','코스닥') AND substr(code,6,1)='0')) ORDER BY code",
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

    상태 파일 = {"asof": snap_date, "n_requested": 요청 종목 수, "grace": {ticker: {"last_seen", "missing_days"}}}.
    grace 는 "마스터에서 사라졌지만 아직 요청 중인" 종목만 담는다. `n_requested` 는 ledger_health 가
    종목축 테이블의 비율 게이트 분모로 읽는다. `missing_days` 는 마스터 스냅샷 날짜(`asof`)가 바뀐
    호출에서만 1 오른다.
    """
    snap = kiwoom_common(con_kw)
    today = set(snap.tickers)
    state = _load_state(state_path)
    grace_raw = state.get("grace")
    grace: dict[str, dict[str, object]] = dict(grace_raw) if isinstance(grace_raw, dict) else {}
    first_run = not state
    prev_asof = str(state.get("asof") or "")
    new_day = first_run or prev_asof != snap.snap_date
    seeded_only: tuple[str, ...] = ()
    if first_run:
        seed = _read_seed(seed_path)
        extra = sorted(seed - today)
        seeded_only = tuple(extra)
        for t in extra:                      # 시드 전용 종목은 유예 종목으로 취급(마지막 등장일 = 오늘)
            grace[t] = {"last_seen": snap.snap_date, "missing_days": 0}
    elif new_day and prev_asof:
        # 직전 스냅샷에는 있었는데 오늘 없는 종목 → 유예 진입. 마지막 등장일 = 직전 스냅샷 날짜.
        # (직전 스냅샷 행이 GC 로 없으면 빈 집합 — 그날 이탈은 잡지 못하고 다음 날부터 잡힌다)
        gone = set(kiwoom_common(con_kw, prev_asof).tickers) - today
        for t in sorted(gone):
            if t not in grace:
                grace[t] = {"last_seen": prev_asof, "missing_days": 0}
    # 오늘 다시 나타난 종목은 유예에서 제거
    for t in list(grace):
        if t in today:
            del grace[t]
    # 유예 진행: 새 스냅샷 날짜일 때만 카운트 증가, 만료면 제외(꼬리 수신 여부는 알림만)
    dropped: list[str] = []
    tail_missing: list[str] = []
    for t, g in list(grace.items()):
        prev_missing = g.get("missing_days", 0)
        missing = (int(prev_missing) if isinstance(prev_missing, int | str) else 0) + (1 if new_day and not first_run else 0)
        g["missing_days"] = missing
        last_seen = str(g.get("last_seen", ""))
        if missing >= grace_days:
            mx = _max_dt(con_kw, t)
            if mx is None or mx < last_seen:         # 마지막 등장일 데이터를 못 받은 채 만료
                tail_missing.append(t)
            dropped.append(t)
            del grace[t]
    tickers = tuple(sorted(today | set(grace)))
    os.makedirs(os.path.dirname(os.fspath(state_path)) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"asof": snap.snap_date, "n_requested": len(tickers), "grace": grace}, f, ensure_ascii=False, indent=1)
    return RequestedUniverse(snap.snap_date, tickers, seeded_only, tuple(sorted(dropped)), tuple(sorted(tail_missing)))
