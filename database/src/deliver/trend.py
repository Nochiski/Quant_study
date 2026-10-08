"""주 모델 순위 흐름 — Δ순위 1W·1M 과 셀 안 꺾은선(엑셀 스파크라인) 원자료.

2026-10-06 사용자 결정 N-16: 1일 Δ순위는 노이즈라 빼고, 1M 순위 변화를 숫자와 셀 안 꺾은선으로
보인다(1W 는 같은 날 '없애도 되겠다'로 뺐다 — 계산 함수는 '1W' 도 받는다).
그래프는 모양만 알아보면 된다.

  비교 판   1W = D−7일, 1M = D−1개월(달력. 같은 날이 없으면 그달 말일) **이하**의 마지막 성공 판
            (휴장이면 직전 거래일 판 — Q.Pack 의 −1W 표기와 같은 달력 기준).
  Δ순위     비교 판 순위 − 오늘 순위(양수 = 상승). 어느 한쪽에 순위가 없으면 빈칸.
  흐름      1M 비교 판(없으면 D−1개월 다음 날)부터 D 까지 성공 판의 일별 **원순위 × −1**
            (그래프 위 = 상승 — 스파크라인은 세로축을 뒤집지 못해 부호를 바꿔 싣는다).
            선의 처음(비교 판) → 끝(D) 차이가 곧 Δ순위라 선 방향·선 색(Δ 부호)·Δ 숫자가 늘
            같은 말을 한다.
            10-07 까지는 백분위 100·(N−순위)/(N−1)였다 — 유니버스가 커지는 날 순위가 밀려도 백분위가
            올라 '선은 오르는데 빨강'이 생겼다(E-06, N-25 Q8 '원순위 선').
  판 파일이 지워졌거나(keep) 그 판에 주 모델이 없으면 그날은 흐름에서 빠진다.
"""
from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook

from . import qpack
from .reader import DeliverError, ModelRun, previous_run, read_scores, runs_between
from .view import rank_of, ticker_of

SHEET = "순위 흐름"          # 그래프 원자료(숨김 시트). 행 번호를 점수 시트와 맞춘다
WINDOWS = ("1M",)             # 엑셀에 싣는 창(1W 는 10-06 사용자 결정으로 뺐다)
LINE_UP, LINE_DOWN, LINE_FLAT = "1E8C45", "D0312D", "8C8C8C"   # 상승 초록 · 하락 빨강 · 같음 회색


def month_back(d: date) -> date:
    """한 달 전 같은 날(없으면 그달 말일)."""
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def window_start(d: date, which: str) -> date:
    return d - timedelta(days=7) if which == "1W" else month_back(d)


def _on_or_before(model_root: Path, d: date, basis: str) -> ModelRun | None:
    return previous_run(model_root, d + timedelta(days=1), basis)


@dataclass(frozen=True)
class Trend:
    dates: tuple[str, ...]                        # 흐름에 든 판 날짜(오름차순, 마지막 = D)
    line: Mapping[str, tuple[int | None, ...]]    # 종목 → dates 순 −순위(위 = 상승)
    base: Mapping[str, str | None]                # '1W'·'1M' → 비교 판 날짜(없으면 None)
    base_rank: Mapping[str, Mapping[str, int | None]]   # '1W'·'1M' → 비교 판 순위

    def delta(self, which: str, ticker: str, rank_now: int | None) -> int | None:
        then = self.base_rank[which].get(ticker)
        return None if then is None or rank_now is None else then - rank_now

    def first_index(self, which: str, d: date) -> int:
        """그 창의 흐름이 시작하는 dates 위치 — 비교 판, 없으면 창 시작 다음 날 이후 첫 판."""
        base = self.base[which]
        after = window_start(d, which) + timedelta(days=1)
        start = base if base is not None else after.isoformat()
        return next((i for i, x in enumerate(self.dates) if x >= start), len(self.dates))


def _ranks(model_root: Path, run: ModelRun, spec_id: str) -> dict[str, int | None] | None:
    if spec_id not in run.specs:
        return None
    try:
        return {ticker_of(r): rank_of(r) for r in read_scores(model_root, run, spec_id)}
    except DeliverError:
        return None


def load_trend(model_root: Path, run: ModelRun, spec_id: str, basis: str) -> Trend:
    d = date.fromisoformat(run.date)
    bases = {w: _on_or_before(model_root, window_start(d, w), basis) for w in WINDOWS}
    b1m = bases["1M"]
    start = b1m.date if b1m is not None else (month_back(d) + timedelta(days=1)).isoformat()
    by_date: dict[str, dict[str, int | None]] = {}
    for r in runs_between(model_root, start, run.date, basis):
        got = _ranks(model_root, r, spec_id)
        if got is not None:
            by_date[r.date] = got
    dates = tuple(sorted(by_date))
    base: dict[str, str | None] = {}
    base_rank: dict[str, Mapping[str, int | None]] = {}
    for w, b in bases.items():
        got = None if b is None else by_date.get(b.date) or _ranks(model_root, b, spec_id)
        base[w] = None if got is None or b is None else b.date
        base_rank[w] = got or {}
    line: dict[str, list[int | None]] = {}
    for i, day in enumerate(dates):
        for t, rk in by_date[day].items():
            row = line.setdefault(t, [None] * len(dates))
            row[i] = None if rk is None else -rk
    return Trend(dates, {t: tuple(v) for t, v in line.items()}, base, base_rank)


def line_color(delta: int | None) -> str:
    if delta is None or delta == 0:
        return LINE_FLAT
    return LINE_UP if delta > 0 else LINE_DOWN


def write_sheet(wb: Workbook, tickers: Sequence[str], trend: Trend) -> None:
    """숨김 원자료 시트 — 7행 머리(코드·날짜), 8행부터 점수 시트와 같은 행 순서. 값 = −순위."""
    ws = wb.create_sheet(SHEET)
    qpack.put(ws, 7, 1, "코드").font = qpack.font(bold=True)
    for j, day in enumerate(trend.dates, start=2):
        qpack.put(ws, 7, j, day[5:]).font = qpack.font(bold=True)
    for i, t in enumerate(tickers, start=qpack.FIRST_DATA_ROW):
        qpack.put(ws, i, 1, t).font = qpack.font()
        for j, v in enumerate(trend.line.get(t, ()), start=2):
            if v is not None:
                cell = qpack.put(ws, i, j, v)
                cell.font = qpack.font()
                cell.alignment = Alignment(horizontal="right")
    qpack.tighten_rows(ws)
    ws.sheet_state = "hidden"


def spark_groups(tickers: Sequence[str], trend: Trend, d: date,
                 cols: Mapping[str, str], deltas: Mapping[str, Mapping[str, int | None]],
                 ) -> list[qpack.SparkGroup]:
    """창(1W·1M)마다 선 색(Δ 부호)별 묶음. 흐름 판이 2개 미만이면 그 창은 그리지 않는다.

    `cols` = 창 → 점수 시트의 그래프 열 글자, `deltas` = 창 → 종목 → Δ순위."""
    out: list[qpack.SparkGroup] = []
    last = len(trend.dates) + 1                    # 원자료 시트 마지막 날짜 열(1 = 코드)
    for w in WINDOWS:
        first = trend.first_index(w, d) + 2
        if last - first + 1 < 2 or w not in cols:
            continue
        ref_cols = f"{get_column_letter(first)}{{r}}:{get_column_letter(last)}{{r}}"
        by_color: dict[str, list[tuple[str, str]]] = {}
        for i, t in enumerate(tickers, start=qpack.FIRST_DATA_ROW):
            if t not in trend.line:
                continue
            ref = f"'{SHEET}'!" + ref_cols.format(r=i)
            by_color.setdefault(line_color(deltas[w].get(t)), []).append((ref, f"{cols[w]}{i}"))
        out += [qpack.SparkGroup(c, tuple(cells)) for c, cells in sorted(by_color.items())]
    return out


__all__ = ["SHEET", "Trend", "line_color", "load_trend", "month_back", "spark_groups",
           "window_start", "write_sheet"]
