"""종목·지수 한 장 — 차트에 그릴 자료와 오른쪽 상태 요약을 만든다(화면 그리기는 render.py)."""

import json
import math
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .. import data
from .. import fib_channel as fc
from .. import market as mk
from .. import pivot_points as pp
from .. import signals as sg
from .. import volume_profile as vp
from .. import zigzag as zz
from . import channels as chs
from . import summary as sm

FUTURE_BARS = 80  # 채널·추세선을 오른쪽으로 늘려 그릴 미래 봉 수
LINE_SAMPLE_BARS = 20  # 로그 눈금에서도 선이 맞게 보이도록 이 간격마다 점을 찍는다
MAJOR_PIVOT_BARS = 42  # 앞뒤 2개월 이상 극값인 피봇만 차트에 표시
AUTO_TOP = 3
PROFILE_WINDOWS = (60, 120, 250)

EVENT_TEXT = {
    "A1": "RSI14 가 30 아래로",
    "A2": "RSI14 가 70 위로",
    "A3": "MACD 골든(시그널 상향 교차)",
    "A4": "MACD 데드(시그널 하향 교차)",
    "A5": "종가가 일목 구름 위로",
    "A6": "일목 전환선이 기준선 상향 교차",
    "A7": "OBV 20일 신고(가격은 아님)",
    "A8": "종가가 볼린저 하단 아래로",
    "A9": "종가가 볼린저 상단 위로",
    "A10": "스토캐스틱 %K>%D 교차(20 아래)",
    "A11": "EMA20 이 EMA50 상향 교차",
    "A12": "SMA50 이 SMA200 상향 교차(골든크로스)",
    "A13": "종가가 120일 매물대 POC 위로",
    "A14": "전고점(지그재그) 돌파",
    "A15": "전저점(지그재그) 이탈",
}
STATE_TEXT = {
    "D1": "종가 > EMA20",
    "D2": "EMA20 > EMA50",
    "D3": "종가 > SMA200",
    "D4": "MACD > 시그널",
    "D5": "종가 > 일목 구름",
    "D6": "RSI14 40~70",
    "D7": "OBV > OBV 20일 평균",
    "D8": "볼린저 중심선 위·상단 안",
    "D9": "스토캐스틱 %K>%D·80 미만",
    "D10": "위쪽 매물 30% 미만",
}


def scorecard_path() -> Path:
    return Path(
        os.environ.get("TA_SCORECARD", "~/quant-data/research/2026-10-27-technical-analysis/results/scorecard.json")
    ).expanduser()


def load_scorecard(path: Path) -> dict[str, dict[str, str]]:
    """연구 결과 성적표. 없으면 빈 사전(화면엔 '미측정')."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: {"verdict": str(v.get("verdict", "")), "summary": str(v.get("summary", ""))} for k, v in raw.items()}


def _num(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _future_dates(last: np.datetime64, n: int) -> list[str]:
    out: list[str] = []
    d = last
    while len(out) < n:
        d = d + np.timedelta64(1, "D")
        if int((d.astype("datetime64[D]").astype(np.int64) + 3) % 7) < 5:  # 월~금
            out.append(str(d))
    return out


def _line_points(
    values_at: Callable[[int], object], x_first: int, x_last: int, times: list[str]
) -> list[dict[str, object]]:
    xs = sorted(set(list(range(x_first, x_last + 1, LINE_SAMPLE_BARS)) + [x_last]))
    return [{"time": times[x], "value": round(float(values_at(x)), 4)} for x in xs]


def _channel_block(
    kind: str,
    label: str,
    ch: fc.Channel,
    scored: fc.Scored | None,
    times: list[str],
    low: np.ndarray,
    high: np.ndarray,
) -> dict[str, object]:
    # 첫 점 250봉 앞부터 미래 봉까지 그린다. 선 번호는 그 구간 양 끝에서 모두 가격 범위를 덮게 고른다
    x_end = len(times) - 1
    x_start = max(0, ch.xa - 250)
    lo, hi = float(np.nanmin(low[x_start:])), float(np.nanmax(high[x_start:]))
    grid = fc.grid_lines(ch, np.array([x_start, x_end]), lo, hi)
    ratios = [float(r) for r in grid.ratios]
    grid_lines = [
        {"ratio": r, "points": _line_points(lambda x, r=r: ch.level(r, x), x_start, x_end, times)} for r in ratios
    ]
    fib_lines = [
        {"ratio": r, "points": _line_points(lambda x, r=r: ch.level(r, x), x_start, x_end, times)}
        for r in fc.FIB_RATIOS
    ]
    evidence = "세 번째 점 뒤 자료가 60봉 미만이라 점수 없음"
    if scored is not None:
        last_turn = (
            f" · 마지막 꺾임 {times[scored.last_turn_day]}, {scored.last_turn_ratio:g}선"
            if scored.last_turn_day >= 0
            else ""
        )
        real_rate = scored.turns / (scored.turns + scored.pierces) * 100 if scored.turns + scored.pierces else math.nan
        rand_rate = (
            scored.expected_turns / (scored.expected_turns + scored.expected_pierces) * 100
            if scored.expected_turns + scored.expected_pierces
            else math.nan
        )
        evidence = (
            f"점수 {scored.score:+.1f}(무작위 대비 표준편차 배수) · "
            f"세 번째 점 뒤 꺾임 {scored.turns}회(무작위 위치 평균 {scored.expected_turns:.1f}회)"
            f" · 관통 {scored.pierces}회 · 꺾임률 {real_rate:.0f}%(무작위 {rand_rate:.0f}%){last_turn}"
        )
    return {
        "kind": kind,
        "label": label,
        "direction": "상승" if ch.direction == fc.UP else "하락",
        "points": [
            {"time": times[ch.xa], "value": ch.ya},
            {"time": times[ch.xb], "value": ch.yb},
            {"time": times[ch.xc], "value": ch.yc},
        ],
        "score": None if scored is None else round(scored.score, 2),
        "evidence": evidence,
        "grid": grid_lines,
        "fib": fib_lines,
        "channel": [ch.xa, ch.ya, ch.xb, ch.yb, ch.xc, ch.yc],
    }


def build(root: Path, match: data.Match, stored: chs.StoredChannel | None, scorecard: dict[str, dict[str, str]]) -> dict[str, object]:
    """화면 한 장에 필요한 자료(JSON 으로 바로 쓸 수 있는 사전)."""
    s = data.load(root, match)
    n = s.dates.shape[0]
    if n < 30:
        raise ValueError(f"일봉이 너무 적다: code={match.code} name={match.name} rows={n}")
    k = sg.compute(s.bars)
    b = s.bars
    last = n - 1
    times = [str(d) for d in s.dates] + _future_dates(s.dates[-1], FUTURE_BARS)
    is_index = match.kind == "index"
    digits = 2 if is_index else 0

    # 차트 자료
    candles = [
        {"time": times[i], "open": _num(b.open[i]), "high": _num(b.high[i]), "low": _num(b.low[i]), "close": _num(b.close[i])}
        for i in range(n)
        if not math.isnan(b.close[i])
    ]
    volume = [{"time": times[i], "value": _num(b.volume[i]), "up": bool(b.close[i] >= b.open[i])} for i in range(n)]
    ema = {
        "ema20": [{"time": times[i], "value": round(float(v), 4)} for i, v in enumerate(k.ema20) if not math.isnan(v)],
        "ema50": [{"time": times[i], "value": round(float(v), 4)} for i, v in enumerate(k.ema50) if not math.isnan(v)],
    }
    span = zz.extreme_span(k.pivots, b.high, b.low, last)
    pivots = []
    for j in range(k.pivots.idx.shape[0]):
        major = span.back[j] >= MAJOR_PIVOT_BARS and (span.forward[j] >= MAJOR_PIVOT_BARS or span.forward_open[j])
        pivots.append(
            {
                "time": times[int(k.pivots.idx[j])],
                "kind": "high" if k.pivots.kind[j] == zz.HIGH else "low",
                "price": float(k.pivots.price[j]),
                "confirmed": times[int(k.pivots.confirm_idx[j])],
                "back": "처음부터" if span.back_open[j] else sm.span_label(int(span.back[j]), False),
                "forward": sm.span_label(int(span.forward[j]), bool(span.forward_open[j])),
                "major": bool(major),
            }
        )

    # 채널: 지정 + 자동 상위
    channel_blocks: list[dict[str, object]] = []
    user_note = ""
    user_ch: fc.Channel | None = None
    if stored is not None:
        res = chs.resolve(stored.points, s.dates, b.high, b.low)
        if res.ok and res.channel is not None:
            user_ch = res.channel
            sc = fc.score(user_ch, b.close, b.high, b.low, k.atr14, last, n_shift=20, seed=0)
            channel_blocks.append(_channel_block("user", "지정 채널", user_ch, sc, times, b.low, b.high))
        else:
            user_note = f"지정 채널을 그리지 못함: {res.detail}"
    auto: list[fc.Scored] = []
    for direction in (fc.UP, fc.DOWN):
        auto += fc.auto_select(b.close, b.high, b.low, k.atr14, k.pivots, last, direction, n_shift=20, seed=0)
    auto = fc.rank(auto)[:AUTO_TOP]
    for i, a in enumerate(auto, start=1):
        channel_blocks.append(_channel_block("auto", f"자동 {i}", a.channel, a, times, b.low, b.high))

    # 추세선(마지막 확정 저점 2개·고점 2개)
    trend = []
    usable = k.pivots.usable(last)
    for kind, label in ((zz.LOW, "지지 추세선"), (zz.HIGH, "저항 추세선")):
        idx = [j for j in range(usable.idx.shape[0]) if usable.kind[j] == kind][-2:]
        if len(idx) < 2:
            continue
        x1, x2 = int(usable.idx[idx[0]]), int(usable.idx[idx[1]])
        y1, y2 = float(usable.price[idx[0]]), float(usable.price[idx[1]])
        slope = (y2 - y1) / (x2 - x1)
        now = y1 + slope * (last - x1)
        broken = (b.close[last] < now) if kind == zz.LOW else (b.close[last] > now)
        trend.append(
            {
                "label": label,
                "points": [{"time": times[x], "value": round(y1 + slope * (x - x1), 4)} for x in (x1, len(times) - 1)],
                "now": now,
                "broken": bool(broken),
            }
        )

    # 피봇 포인트: 다음 거래일(오늘 봉), 이번 주(직전 주), 이번 달(직전 달)
    pv_next = pp.classic(b.high[last], b.low[last], b.close[last])
    pv_w = pp.period_levels(s.dates, b.high, b.low, b.close, "W")
    pv_m = pp.period_levels(s.dates, b.high, b.low, b.close, "M")

    def pv_dict(lv: pp.Levels, i: int | None) -> dict[str, float | None]:
        names = ("s3", "s2", "s1", "p", "r1", "r2", "r3")
        return {nm: _num(float(np.asarray(getattr(lv, nm)) if i is None else getattr(lv, nm)[i])) for nm in names}

    pivot_levels = {"next_day": pv_dict(pv_next, None), "week": pv_dict(pv_w, last), "month": pv_dict(pv_m, last)}

    profiles = {}
    for w in PROFILE_WINDOWS:
        if n >= w:
            prof = vp.profile(b.high[n - w :], b.low[n - w :], b.volume[n - w :], 50)
            profiles[str(w)] = {"edges": prof.edges.round(4).tolist(), "volume": prof.volume.tolist(), "poc": prof.poc}

    chart = {
        "candles": candles,
        "whitespace": times[n:],
        "volume": volume,
        "ema": ema,
        "pivots": pivots,
        "channels": channel_blocks,
        "trend": trend,
        "pivot_levels": pivot_levels,
        "profiles": profiles,
        "digits": digits,
    }

    # 오른쪽 상태 요약
    close = float(b.close[last])
    atr = float(k.atr14[last])
    levels: list[sm.Level] = []
    for blk in channel_blocks[:2]:  # 지정 + 자동 1위(지정이 없으면 자동 1·2위)
        below, above = sm.grid_neighbours(fc.Channel(*blk["channel"]), last, close, k=2)
        levels += [sm.Level(v, f"{blk['label']} {r:g}선") for r, v in below + above]
    for key, title in (("next_day", "일"), ("week", "주"), ("month", "월")):
        for nm, v in pivot_levels[key].items():
            if v is not None:
                levels.append(sm.Level(v, f"{title} {nm.upper()}"))
    if "120" in profiles:
        levels.append(sm.Level(float(profiles["120"]["poc"]), "매물대 POC(120일)"))
    levels += [
        sm.Level(float(k.last_high[last]), "전고점(지그재그)"),
        sm.Level(float(k.last_low[last]), "전저점(지그재그)"),
        sm.Level(float(k.ema20[last]), "EMA20"),
        sm.Level(float(k.ema50[last]), "EMA50"),
        sm.Level(float(k.sma200[last]), "SMA200"),
    ]
    for t in trend:
        levels.append(sm.Level(float(t["now"]), str(t["label"])))
    near = sm.nearest(levels, close, atr, k=3)

    positions = []
    for blk in channel_blocks:
        ch = fc.Channel(*blk["channel"])
        pos = sm.channel_position(ch, last, close)
        positions.append(
            {
                "label": blk["label"],
                "ratio": round(pos.ratio, 2),
                "below": f"{pos.below_ratio:g}선 {pos.below_value:,.{digits}f}",
                "above": f"{pos.above_ratio:g}선 {pos.above_value:,.{digits}f}",
                "to_above_pct": (pos.above_value / close - 1) * 100,
                "to_below_pct": (pos.below_value / close - 1) * 100,
            }
        )

    logret = np.diff(np.log(b.close))
    rv20 = float(np.nanstd(logret[-20:]) * math.sqrt(252) * 100) if n > 21 else float("nan")
    atr_pct = atr / close * 100
    hist = (k.atr14 / b.close)[max(0, n - 250) :]
    hist = hist[~np.isnan(hist)]
    atr_pctile = float((hist <= atr / close).mean() * 100) if hist.size else float("nan")

    ev = sg.events(k)
    st = sg.states(k)
    fired = [
        {"id": a, "text": EVENT_TEXT[a], "score": scorecard.get(a)}
        for a in sg.EVENT_IDS
        if bool(ev[a][last])
    ]
    checklist = []
    values = _state_values(k, last, digits)
    for d in (f"D{i}" for i in range(1, 11)):
        v = st[d][last]
        checklist.append(
            {
                "id": d,
                "text": STATE_TEXT[d],
                "value": values[d],
                "status": "정의 안 됨" if math.isnan(v) else ("양호" if v == 1.0 else "주의"),
                "score": scorecard.get(d),
            }
        )
    verified = [c for c in checklist if c["score"] and c["score"]["verdict"] == "유효"]
    habit = [c for c in checklist if c not in verified]

    def good(rows: list[dict[str, object]]) -> int:
        return sum(1 for c in rows if c["status"] == "양호")

    if verified:
        checklist_summary = f"근거 있는 지표 기준 양호 {good(verified)}/{len(verified)} · 습관 지표 양호 {good(habit)}/{len(habit)}"
    elif scorecard:
        checklist_summary = f"근거 있는 지표 없음(과거 실측에서 '유효' 0개) · 습관 지표 양호 {good(habit)}/{len(habit)}"
    else:
        checklist_summary = f"미검증 지표 기준 양호 {good(habit)}/{len(habit)} (연구 결과 전 — 근거 있는 지표 없음)"

    extras_block = _extras_block(root, match, s.dates)
    combo_block = _combo_block(root, match, b, scorecard)

    panel = {
        "positions": positions,
        "user_note": user_note,
        "near_above": [_near(x, digits) for x in near.above],
        "near_below": [_near(x, digits) for x in near.below],
        "volatility": {"atr_pct": atr_pct, "rv20": rv20, "atr_pctile": atr_pctile},
        "checklist": checklist,
        "checklist_summary": checklist_summary,
        "d11": _num(float(st["D11"][last])),
        "d11_score": scorecard.get("D11"),
        "fired": fired,
        "extras": extras_block,
        "combo": combo_block,
        "trend": [{"label": t["label"], "now": f"{t['now']:,.{digits}f}", "broken": t["broken"]} for t in trend],
    }
    prev = float(b.close[last - 1])
    meta = {
        "code": match.code,
        "name": match.name,
        "kind": match.kind,
        "date": times[last],
        "close": close,
        "change_pct": (close / prev - 1) * 100,
        "digits": digits,
        "rows": n,
        "scorecard": bool(scorecard),
    }
    return {"meta": meta, "chart": chart, "panel": panel}


def _combo_block(
    root: Path, match: data.Match, b: sg.Bars, scorecard: dict[str, dict[str, str]]
) -> dict[str, object]:
    """묶음 지표 — 눌림목 점수(종목만)와 시장 투매 비율(PREREG2 P1·P2 정의)."""
    snap = mk.snapshot(root)
    out: dict[str, object] = {
        "date": snap.date,
        "panic": {
            "fraction": snap.panic,
            "n": len(snap.panic_tickers()),
            "universe": int(snap.in_universe.sum()),
            "is_panic": bool(snap.panic >= mk.PANIC_FRACTION),
            "score": scorecard.get("P2"),
        },
    }
    if match.kind != "stock":
        return out
    c, h = b.close, b.high
    p52 = float(c[-1] / np.nanmax(h[-250:])) if c.shape[0] >= 250 else float("nan")
    r20 = float(c[-1] / c[-21] - 1.0) if c.shape[0] > 20 else float("nan")
    hit = np.flatnonzero(snap.tickers == match.code)
    in_u = bool(hit.size and snap.in_universe[hit[0]])
    score = float(snap.score[hit[0]]) if in_u else mk.place(p52, r20, snap.p52, snap.r20, snap.in_universe)
    out["pullback"] = {
        "score": score,
        "label": mk.pullback_label(score),
        "p52": p52,
        "r20": r20,
        "in_universe": in_u,
        "score_card": scorecard.get("P1"),
    }
    if in_u:
        i = int(hit[0])
        hi52 = float(mk.pct_rank(snap.p52, snap.in_universe)[i])
        comp = float(snap.composite[i])
        out["high52"] = {"score": hi52, "label": mk.quintile_label(hi52), "score_card": scorecard.get("E11")}
        out["composite"] = {"score": comp, "label": mk.quintile_label(comp), "score_card": scorecard.get("F2")}
    return out


def _near(x: sm.NearLevel, digits: int) -> dict[str, object]:
    return {
        "value": f"{x.value:,.{digits}f}",
        "labels": " · ".join(x.labels),
        "pct": round(x.distance_pct, 2),
        "atr": round(x.distance_atr, 2),
    }


def _state_values(k: sg.Computed, t: int, digits: int) -> dict[str, str]:
    c = float(k.bars.close[t])

    def rel(a: float, b: float) -> str:
        return "—" if math.isnan(b) else f"{(a / b - 1) * 100:+.1f}%"

    return {
        "D1": f"EMA20 대비 {rel(c, float(k.ema20[t]))}",
        "D2": f"EMA20/EMA50 {rel(float(k.ema20[t]), float(k.ema50[t]))}",
        "D3": f"SMA200 대비 {rel(c, float(k.sma200[t]))}",
        "D4": f"MACD−시그널 {float(k.macd.line[t] - k.macd.signal[t]):+,.{max(digits, 2)}f}",
        "D5": f"구름 상단 대비 {rel(c, float(k.ichimoku.cloud_top[t]))}",
        "D6": f"RSI {float(k.rsi14[t]):.1f}",
        "D7": f"OBV 20일 평균 대비 {rel(float(k.obv[t]), float(k.obv_sma20[t])) if k.obv_sma20[t] > 0 else '—'}",
        "D8": f"%B {(c - float(k.bollinger.lower[t])) / float(k.bollinger.upper[t] - k.bollinger.lower[t]) * 100:.0f}",
        "D9": f"%K {float(k.stochastic.k[t]):.0f} / %D {float(k.stochastic.d[t]):.0f}",
        "D10": f"위쪽 매물 {float(k.share_above120[t]) * 100:.0f}%",
    }


def _last_valid(dates: np.ndarray, x: np.ndarray) -> tuple[str, float] | None:
    ok = np.flatnonzero(~np.isnan(x))
    if ok.size == 0:
        return None
    i = int(ok[-1])
    return str(dates[i]), float(x[i])


def _extras_block(root: Path, match: data.Match, dates: np.ndarray) -> dict[str, object] | None:
    since = str(dates[max(0, dates.shape[0] - 45)])
    e = data.load_extras(root, match, since)
    if e is None or e.dates.shape[0] == 0:
        return None
    cap = _last_valid(e.dates, e.mktcap_krw)

    def flow(x: np.ndarray, days: int) -> dict[str, object]:
        tail = x[-days:]
        total = float(np.nansum(tail))
        pct = total / cap[1] * 100 if cap and cap[1] > 0 else float("nan")
        return {"krw": total, "pct_cap": pct, "days": int((~np.isnan(tail)).sum())}

    def change(x: np.ndarray, days: int) -> dict[str, object] | None:
        ok = np.flatnonzero(~np.isnan(x))
        if ok.size < 2:
            return None
        i = int(ok[-1])
        j = max(0, i - days)
        base = x[j]
        if math.isnan(base) or base == 0:
            return None
        return {"date": str(e.dates[i]), "value": float(x[i]), "chg_pct": (float(x[i]) / float(base) - 1) * 100}

    out: dict[str, object] = {
        "scope": e.scope,
        "last_date": str(e.dates[-1]),
        "foreign": {"d5": flow(e.foreign_krw, 5), "d20": flow(e.foreign_krw, 20)},
        "institution": {"d5": flow(e.institution_krw, 5), "d20": flow(e.institution_krw, 20)},
    }
    sw = _last_valid(e.dates, e.short_weight_pct)
    out["short_weight"] = None if sw is None else {"date": sw[0], "value": sw[1]}
    out["lending"] = change(e.lending_shr, 20)
    cr = _last_valid(e.dates, e.credit_rate_pct)
    out["credit_rate"] = None if cr is None else {"date": cr[0], "value": cr[1]}
    out["credit"] = change(e.credit_shr, 20)
    return out
