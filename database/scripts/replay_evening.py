"""장 마감 판 재생 보조 — `scripts/replay.sh --basis evening` 이 부른다(컷오버 트랙 PR-8b).

`replay_tool.py`(표준 라이브러리만)와 달리 검증 대상 코드(`replay.sh --code` 의 src — PYTHONPATH)를
import 한다: 거래일은 판정 달력 `daily.calendar`, 재생 T 행 원장은 15:41 수집기 실물 함수
`daily.postclose`(대상 `resolve_targets` · 쓰기 `connect`·`insert_first`)로 만든다 — 실운영과 같은
대상·같은 원장 규약. 운영 파일은 읽기만 한다(키움 원장은 읽기 전용 URI).

  days --calendar-dir DIR --from T1 --to T2
      [T1, T2] 의 거래일 T 와 첫 T 의 직전 거래일 D'. 표준 출력 첫 줄 D', 다음 줄부터 T(한 줄에
      하나).
  postclose --kiwoom-db FILE --base ROOT --date T --d-prime D' --out FILE
      재생 T 행 원천(T-36 — 첫 수집 10-14 전 날짜에는 장 마감 원장이 없다). 21:05 키움 원장
      (`ka10060_investor_flows` dt=T)에서 수집기 대상 종목만 골라 OUT(새 파일)에 수집기와 같은
      함수로 쓴다. 대상 = `daily.postclose.resolve_targets(ROOT, D')` — ①
      ROOT/data/factor_inputs/_runs/<D'>_morning.json 의 eligible → ②
      ROOT/data/deliver/history/<D'>_morning.json 의 universe_daily D' 행 v3 유니버스 나머지.
      행마다 price_valid='1'(재생 T 행은 21:05 원장 값 — 종가는 애프터마켓 마지막 체결가일 수 있어
      두 판 대조 `--replay` 의 '종가 정의'), collected_at = 그 원장 행의 수집 시각, fetched_at = 이
      실행 시각(UTC). 대상인데 21:05 원장에 행이 없는 종목은 쓰지 않는다(장 마감 판에서 T 가격 없음
      — 실운영 16:00 컷오프 자리). 재생 표시는 같은 파일의 `replay_source` 표(T 하루 한 행).
rc: 0 정상 · 2 입력 오류(달력·원장·대상 판 없음, 그날 원장 행 0, 대상 0, OUT 이 이미 있음)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

REPLAY_TABLE = "replay_source"


class ToolError(Exception):
    """입력 오류 — rc 2."""


def _day(s: str) -> dt.date:
    try:
        if not (len(s) == 8 and s.isdigit()):
            raise ValueError(s)
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError as e:
        raise ToolError(f"날짜는 YYYYMMDD: {s!r}") from e


# ── days ────────────────────────────────────────────────────────────────────
def days(calendar_dir: Path, start: str, end: str) -> list[str]:
    """[D', T1, …, Tn] — 판정 달력으로 센다(주말·휴장 제외). 달력이 그 해를 덮지 못하면 멈춘다."""
    from daily import calendar
    a, b = _day(start), _day(end)
    if b < a:
        raise ToolError(f"--from {start} 이 --to {end} 보다 뒤다")
    try:
        cal = calendar.load(calendar_dir)
        ts: list[dt.date] = []
        cur = a
        while cur <= b:
            if cal.is_trading_day(cur):
                ts.append(cur)
            cur += dt.timedelta(days=1)
        if not ts:
            raise ToolError(f"[{start}, {end}] 에 거래일이 없다(daily.calendar)")
        first = cal.prev_trading_day(ts[0])
    except (calendar.CalendarUnavailable, KeyError) as e:
        raise ToolError(f"판정 달력을 읽지 못했다(calendar_dir={calendar_dir}): {e}") from e
    return [d.strftime("%Y%m%d") for d in (first, *ts)]


# ── postclose ───────────────────────────────────────────────────────────────
def postclose(kiwoom_db: Path, base: Path, t: str, d_prime: str, out: Path) -> str:
    """재생 T 행 원장을 쓰고 한 줄 요약을 돌려준다."""
    from daily import postclose as pc
    _day(t)
    _day(d_prime)
    if out.exists():
        raise ToolError(f"재생 원장이 이미 있다: {out} — 패스마다 새 경로를 쓴다")
    if not kiwoom_db.is_file():
        raise ToolError(f"키움 원장이 없다: {kiwoom_db}")
    tg = pc.resolve_targets(base, d_prime)
    if not tg.order:
        raise ToolError(f"수집 대상이 0 종목이다(D'={d_prime}): {'; '.join(tg.notes)}")
    ro = sqlite3.connect(kiwoom_db.absolute().as_uri() + "?mode=ro", uri=True, timeout=60)
    ro.row_factory = sqlite3.Row
    try:
        rows = {str(r["ticker"]): r for r in ro.execute(
            f'SELECT * FROM "{pc.TABLE}" WHERE dt = ?', (t,))}
    except sqlite3.Error as e:
        raise ToolError(f"키움 원장 {pc.TABLE} 을 읽지 못했다: {kiwoom_db} ({e})") from e
    finally:
        ro.close()
    if not rows:
        raise ToolError(f"21:05 키움 원장에 dt={t} 행이 없다: {kiwoom_db} {pc.TABLE}")
    fetched = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    con = pc.connect(out)
    n = 0
    missing: list[str] = []
    try:
        for ticker in tg.order:
            r = rows.get(ticker)
            if r is None:
                missing.append(ticker)
                continue
            keys = r.keys()
            row = {c: (r[c] if c in keys else None) for c in pc.COLS}
            n += pc.insert_first(con, pc.COLS, ticker, [row],
                                 collected_at=str(r["collected_at"] or fetched),
                                 fetched_at=fetched, price_valid=True)
        cand_missing = sum(1 for x in tg.candidates if x not in rows)
        con.execute(f'CREATE TABLE IF NOT EXISTS "{REPLAY_TABLE}" (dt TEXT PRIMARY KEY, '
                    "source TEXT, kiwoom_db TEXT, d_prime TEXT, n_targets INTEGER, "
                    "n_candidates INTEGER, n_rows INTEGER, n_missing INTEGER, "
                    "n_candidates_missing INTEGER, fi_build TEXT, universe_build TEXT, "
                    "created_at TEXT)")
        con.execute(f'INSERT INTO "{REPLAY_TABLE}" VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (t, f"재생 — 21:05 키움 원장 {pc.TABLE} dt={t}(price_valid='1', T-36)",
                     str(kiwoom_db), d_prime, len(tg.order), len(tg.candidates), n, len(missing),
                     cand_missing, tg.fi_build, tg.universe_build, fetched))
        con.commit()
    finally:
        con.close()
    notes = f" · 대상 판 메모 {'; '.join(tg.notes)}" if tg.notes else ""
    return (f"재생 장 마감 원장 T={t} D'={d_prime} — 대상 {len(tg.order)}"
            f"(후보 {len(tg.candidates)} · 나머지 {len(tg.rest)}) → 행 {n} · "
            f"21:05 원장에 없음 {len(missing)}"
            f"(후보 {cand_missing}) · 원장 밖 종목 {len(set(rows) - set(tg.order))} 은 버림 · "
            f"fi {tg.fi_build} · universe {tg.universe_build}{notes} → {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="replay_evening.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("days")
    d.add_argument("--calendar-dir", type=Path, required=True)
    d.add_argument("--from", dest="start", required=True)
    d.add_argument("--to", dest="end", required=True)
    p = sub.add_parser("postclose")
    p.add_argument("--kiwoom-db", type=Path, required=True)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--d-prime", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "days":
            print("\n".join(days(a.calendar_dir, a.start, a.end)))
        else:
            print(postclose(a.kiwoom_db, a.base, a.date, a.d_prime, a.out))
    except ToolError as e:
        print(f"replay_evening {a.cmd}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
