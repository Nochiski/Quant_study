"""피해야 할 상태 — 연구 6차에서 2010–15 에 찾고 2020–26 에서 한 번 검증해 남은 회피 신호(한 종목 봉열, 날마다 참/거짓).

정의는 연구 폴더 explore/A·B·C·D 후보 함수와 같다(가격 있는 봉 기준). D06 의 시장폭은 단면이라 market.py 가 준다.
"""

import numpy as np

NAMES = {
    "A06": "고점 80% 아래에서 20일 신고가",
    "D06": "시장폭 약한 날 20일 신고가",
    "C03": "하락 폭량",
    "B06": "하락 갭",
}
CARD = {"A06": "R6_A06", "D06": "R6_D06", "C03": "R6_C03", "B06": "R6_B06"}
BREADTH_WEAK = 0.5  # D06: 유니버스 중 200일선 위 종목 비율이 이 값 이하인 날


def _prev(x: np.ndarray) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    out[1:] = x[:-1]
    return out


def _roll_max(x: np.ndarray, n: int) -> np.ndarray:
    """오늘 포함 n 봉 최댓값(n 봉이 안 되면 NaN)."""
    out = np.full(x.shape, np.nan)
    for t in range(n - 1, x.shape[0]):
        out[t] = np.nanmax(x[t - n + 1 : t + 1])
    return out


def _roll_mean(x: np.ndarray, n: int, minp: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    for t in range(n - 1, x.shape[0]):
        w = x[t - n + 1 : t + 1]
        if np.isfinite(w).sum() >= minp:
            out[t] = np.nanmean(w)
    return out


def breakout20(close: np.ndarray) -> np.ndarray:
    """종가가 직전 20봉 종가 최고를 넘은 날."""
    with np.errstate(invalid="ignore"):
        return close > _prev(_roll_max(close, 20))


def flags(open_: np.ndarray, close: np.ndarray, volume: np.ndarray) -> dict[str, np.ndarray]:
    """A06·C03·B06 날마다 참/거짓(D06 은 시장폭이 필요해 breakout20 만 여기서)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        prev_c = _prev(close)
        a06 = breakout20(close) & (close < 0.8 * _roll_max(close, 250))
        vr = volume / _prev(_roll_mean(volume, 20, 16))
        c03 = (vr >= 3) & (close / prev_c - 1 <= -0.05)
        b06 = open_ / prev_c - 1 <= -0.03
    return {"A06": a06, "C03": c03, "B06": b06}
