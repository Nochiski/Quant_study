"""기술적 점수 → 20거래일 뒤 상승·하락 확률. 예측 모델이 아니라 과거에 같은 상태였던 경우의 실제 비율이다.

- 유니버스 안 종목: 가격 기반 종합(F2) 백분위의 10분위별 빈도표(연구 폴더 results/prob_table.json, 2010–15·2020–26 단면).
- 지수·ETF·유니버스 밖 종목: 자기 과거에서 같은 추세 상태(200일선 위/아래 × EMA20/50)였던 날의 빈도.
홀드아웃(2016–2019)을 덮는 창은 세지 않는다.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HORIZON = 20
HOLDOUT = (np.datetime64("2016-01-01"), np.datetime64("2019-12-31"))
STATE_TEXT = {
    0: "하락 추세(200일선 아래 · EMA20<50)",
    1: "장기 하락 · 단기 반등(200일선 아래 · EMA20>50)",
    2: "장기 상승 · 단기 조정(200일선 위 · EMA20<50)",
    3: "상승 추세(200일선 위 · EMA20>50)",
}


@dataclass(frozen=True)
class Odds:
    basis: str  # "점수 구간" | "추세 상태"
    label: str
    n: int
    up: float
    down: float
    beat: float | None  # 코스피보다 나은 비율(코스피 자신이면 None)
    mean_ret: float
    base_up: float
    base_down: float
    base_beat: float | None
    base_mean: float
    sample: str


def table_path(scorecard: Path) -> Path:
    return scorecard.with_name("prob_table.json")


def load_table(path: Path) -> dict[str, object] | None:
    return json.loads(path.read_text()) if path.exists() else None


def decile_odds(table: dict[str, object], score: float) -> Odds | None:
    """score = 유니버스 안 가격 기반 종합 백분위(0~1)."""
    if not np.isfinite(score):
        return None
    deciles = table["deciles"]
    base = table["all"]
    if not isinstance(deciles, list) or not isinstance(base, dict):
        raise ValueError(f"빈도표 형식이 다르다: keys={list(table)}")
    d = deciles[min(int(score * 10), 9)]
    return Odds(
        basis="점수 구간",
        label=f"{round(d['lo'] * 100)}~{round(d['hi'] * 100)}",
        n=int(d["n"]),
        up=float(d["up"]),
        down=float(d["down"]),
        beat=float(d["beat_kospi"]),
        mean_ret=float(d["mean_ret"]),
        base_up=float(base["up"]),
        base_down=float(base["down"]),
        base_beat=float(base["beat_kospi"]),
        base_mean=float(base["mean_ret"]),
        sample="2010–15·2020–26 유니버스 종목, 20거래일마다",
    )


def trend_state(close: np.ndarray, ema20: np.ndarray, ema50: np.ndarray, sma200: np.ndarray) -> np.ndarray:
    """0 하락 추세 · 1 장기 하락·단기 반등 · 2 장기 상승·단기 조정 · 3 상승 추세 · −1 정의 안 됨(200일 전)."""
    ok = ~(np.isnan(close) | np.isnan(ema20) | np.isnan(ema50) | np.isnan(sma200))
    with np.errstate(invalid="ignore"):
        s = 2 * (close > sma200).astype(int) + (ema20 > ema50).astype(int)
    return np.where(ok, s, -1)


def state_odds(
    dates: np.ndarray, close: np.ndarray, state: np.ndarray, bench: np.ndarray | None, horizon: int = HORIZON
) -> Odds | None:
    """마지막 날과 같은 상태였던 과거 날 t 의 종가 t → t+horizon 빈도(겹치는 창 — n 은 날 수)."""
    now = int(state[-1])
    t = np.arange(close.shape[0] - horizon)
    if now < 0 or t.size == 0:
        return None
    end = t + horizon
    clear = (dates[t] > HOLDOUT[1]) | (dates[end] < HOLDOUT[0])
    ret = close[end] / close[t] - 1.0
    ok = clear & np.isfinite(ret) & (state[t] >= 0)
    same = ok & (state[t] == now)
    if not same.any():
        return None

    def beat(m: np.ndarray) -> float | None:
        if bench is None:
            return None
        b = bench[end] / bench[t] - 1.0
        mm = m & np.isfinite(b)
        return float((ret[mm] > b[mm]).mean()) if mm.any() else None

    return Odds(
        basis="추세 상태",
        label=STATE_TEXT[now],
        n=int(same.sum()),
        up=float((ret[same] > 0).mean()),
        down=float((ret[same] < 0).mean()),
        beat=beat(same),
        mean_ret=float(ret[same].mean()),
        base_up=float((ret[ok] > 0).mean()),
        base_down=float((ret[ok] < 0).mean()),
        base_beat=beat(ok),
        base_mean=float(ret[ok].mean()),
        sample=f"자기 과거 {str(dates[0])[:4]}–{str(dates[-1])[:4]}(2016–19 제외), 매일(겹치는 창)",
    )
