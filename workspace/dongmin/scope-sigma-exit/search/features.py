"""신호일 지표·유니버스·합성 순위(PROTOCOL.md "지표"·"유니버스" 절)."""

from __future__ import annotations

import numpy as np

YEAR, MONTH, VOL_WINDOW, VOL_MIN = 252, 21, 60, 40


def first_bar(adj_close: np.ndarray) -> np.ndarray:
    """종목별 첫 가격 행 세션 번호(없으면 세션 수)."""
    has = ~np.isnan(adj_close)
    return np.where(has.any(axis=0), np.argmax(has, axis=0), adj_close.shape[0])


def clean_index(adj_close: np.ndarray, jump: np.ndarray) -> np.ndarray:
    """가격 없는 날·미조정 점프일 수익률을 0 으로 둔 가격 지수. 첫 가격 행 = 1, 그 전은 nan."""
    ff = adj_close.copy()
    for i in range(1, ff.shape[0]):
        miss = np.isnan(ff[i])
        ff[i, miss] = ff[i - 1, miss]
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(ff[1:] / ff[:-1])
    lr[~np.isfinite(lr) | jump[1:]] = 0.0
    out = np.vstack([np.zeros((1, ff.shape[1])), np.cumsum(lr, axis=0)])
    start = first_bar(adj_close)
    base = out[np.minimum(start, ff.shape[0] - 1), np.arange(ff.shape[1])]
    out = np.exp(out - base)
    out[np.arange(ff.shape[0])[:, None] < start[None, :]] = np.nan
    return out


def vol60(adj_close: np.ndarray, jump: np.ndarray, i: int) -> np.ndarray:
    """i 까지 60세션 일간 로그수익률 표준편차. 두 날 모두 가격이 있고 점프일이 아닌 수익률만, 40개 미만이면 nan."""
    if i < VOL_WINDOW:
        return np.full(adj_close.shape[1], np.nan)
    w = adj_close[i - VOL_WINDOW : i + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(w[1:] / w[:-1])
    ok = np.isfinite(lr) & ~jump[i - VOL_WINDOW + 1 : i + 1]
    lr = np.where(ok, lr, np.nan)
    n = ok.sum(axis=0)
    with np.errstate(invalid="ignore"):
        sd = np.nanstd(lr, axis=0, ddof=1)
    return np.where(n >= VOL_MIN, sd, np.nan)


def price_features(p: np.ndarray, start: np.ndarray, i: int) -> dict[str, np.ndarray]:
    """MOM12_1·REV1M·H52. 상장 252세션 미만이면 nan. 판에 252세션 과거가 없으면 모두 nan."""
    if i < YEAR:
        empty = np.full(p.shape[1], np.nan)
        return {"MOM12_1": empty, "REV1M": empty.copy(), "H52": empty.copy()}
    old = start <= i - (YEAR - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mom = p[i - MONTH] / p[i - YEAR] - 1.0
        rev = -(p[i] / p[i - MONTH] - 1.0)
        h52 = p[i] / np.nanmax(p[i - (YEAR - 1) : i + 1], axis=0)
    return {k: np.where(old, v, np.nan) for k, v in (("MOM12_1", mom), ("REV1M", rev), ("H52", h52))}


def pct_rank(values: np.ndarray, members: np.ndarray) -> np.ndarray:
    """유니버스(members) 안 유효 값의 백분위 순위(0~1, 같은 값은 평균 순위). 그 밖은 nan."""
    out = np.full(values.shape, np.nan)
    ok = members & np.isfinite(values)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return out
    v = values[idx]
    _, inverse, counts = np.unique(v, return_inverse=True, return_counts=True)
    first = np.cumsum(counts) - counts
    ranks = (first + (counts - 1) / 2.0)[inverse]  # 같은 값 → 평균 순위(0부터)
    out[idx] = ranks / (len(v) - 1) if len(v) > 1 else 0.5
    return out


def composite(features: dict[str, np.ndarray], spec: tuple[tuple[str, int], ...], members: np.ndarray) -> np.ndarray:
    """(지표, 방향 +1/−1) 백분위 순위 평균. 성분이 모두 있는 유니버스 종목끼리 순위를 매기고, 하나라도 비면 nan."""
    complete = members & np.all([np.isfinite(features[name]) for name, _ in spec], axis=0)
    stacked = np.vstack([pct_rank(features[name] * sign, complete) for name, sign in spec])
    return np.where(complete, stacked.mean(axis=0), np.nan)


def top_by_cap(mktcap_row: np.ndarray, eligible: np.ndarray, n: int) -> np.ndarray:
    """eligible 중 시총 상위 n 의 bool 마스크."""
    idx = np.flatnonzero(eligible & np.isfinite(mktcap_row))
    keep = idx[np.argsort(-mktcap_row[idx], kind="mergesort")[:n]]
    out = np.zeros(mktcap_row.shape, dtype=bool)
    out[keep] = True
    return out


TILT, EXCLUDE_BELOW = 0.5, 0.2


def tilted_caps(cap: np.ndarray, r: np.ndarray, *, mode: str) -> np.ndarray:
    """F2 비중 원점수. cap: 시총 그대로 · tilt: 시총 × (1 + 0.5(2r − 1)), r 이 nan 이면 배수 1 · exclude: r < 0.2 는 nan."""
    if mode == "cap":
        return cap.copy()
    if mode == "tilt":
        return cap * (1.0 + TILT * (2.0 * np.where(np.isfinite(r), r, 0.5) - 1.0))
    if mode == "exclude":
        return np.where(np.isfinite(r) & (r < EXCLUDE_BELOW), np.nan, cap)
    raise ValueError(f"unknown mode — {mode}")


def core_satellite(
    cap: np.ndarray, members: np.ndarray, score: np.ndarray, *, prev: frozenset[int],
    n_core: int = 10, n_sat: int = 30, keep: int = 60,
) -> tuple[dict[int, float], frozenset[int]]:
    """F4 비중. 핵심 = members 중 시총 상위 n_core(시총 몫 그대로), 위성 = 나머지에서 score 상위 n_sat(남은 몫 균등).

    prev 위성 중 순위 keep 안은 유지하고 빈 자리만 순위대로 채운다. 반환 (종목 위치 → 비중, 새 위성).
    """
    core = top_by_cap(cap, members, n_core)
    total = float(np.nansum(np.where(members, cap, 0.0)))
    weights = {int(j): float(cap[j]) / total for j in np.flatnonzero(core)}
    rest = members & ~core & np.isfinite(score)
    order = [int(j) for j in sorted(np.flatnonzero(rest), key=lambda j: (-score[j], j))]
    rank = {j: k for k, j in enumerate(order, start=1)}
    sat = [j for j in sorted(prev) if rank.get(j, keep + 1) <= keep][:n_sat]
    for j in order:
        if len(sat) >= n_sat:
            break
        if j not in sat:
            sat.append(j)
    share = (1.0 - sum(weights.values())) / len(sat) if sat else 0.0
    weights |= {j: share for j in sat}
    return weights, frozenset(sat)
