"""종목 신호(A1~A15)와 점검표 상태(D1~D11) — 사전등록 4.1·4.2 절."""

import numpy as np

from technical_analysis import signals as sg


def test_fresh_needs_true_today_false_yesterday_and_both_defined() -> None:
    cond = np.array([0.0, 1, 1, 0, 1, np.nan, 1])
    np.testing.assert_array_equal(sg.fresh(cond), [False, True, False, False, True, False, False])


def test_cross_above_level_of_previous_day() -> None:
    close = np.array([9.0, 10, 11, 12])
    level = np.array([np.nan, 10.5, 10.5, 13])
    # t2: 종가 11 > 전날 수준 10.5 이고 전날 종가 10 ≤ 10.5 → 참
    np.testing.assert_array_equal(sg.cross_above_prev_level(close, level), [False, False, True, False])


def test_cross_below_level_of_previous_day() -> None:
    close = np.array([12.0, 11, 9, 8])
    level = np.array([np.nan, 10, 10, 7])
    np.testing.assert_array_equal(sg.cross_below_prev_level(close, level), [False, False, True, False])


def test_last_confirmed_level_steps_on_confirm_day() -> None:
    from technical_analysis import zigzag as zz

    p = zz.Pivots(np.array([1, -1, 1]), np.array([2, 4, 7]), np.array([20.0, 10, 25]), np.array([3, 6, 9]))
    lv = sg.last_confirmed(p, zz.HIGH, 11)
    np.testing.assert_array_equal(np.isnan(lv[:3]), [True, True, True])
    np.testing.assert_allclose(lv[3:9], [20] * 6)
    np.testing.assert_allclose(lv[9:], [25, 25])


def random_bars(n: int, seed: int) -> sg.Bars:
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = low + (high - low) * rng.uniform(0, 1, n)
    vol = rng.uniform(1e4, 1e5, n)
    return sg.Bars(open=open_, high=high, low=low, close=close, volume=vol)


def test_all_signals_and_states_use_only_data_up_to_each_day() -> None:
    full = random_bars(360, 11)
    f = sg.compute(full)
    fa, fd = sg.events(f), sg.states(f)
    for t in (230, 300, 359):
        part = sg.Bars(*(getattr(full, k)[: t + 1] for k in ("open", "high", "low", "close", "volume")))
        p = sg.compute(part)
        pa, pd = sg.events(p), sg.states(p)
        for name in sg.EVENT_IDS:
            assert pa[name][t] == fa[name][t], (name, t)
        for name in sg.STATE_IDS:
            a, b = pd[name][t], fd[name][t]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (name, t)


def test_states_are_nan_until_indicators_are_defined() -> None:
    d = sg.states(sg.compute(random_bars(260, 5)))
    assert np.isnan(d["D3"][198])  # SMA200 은 200번째 봉(0부터 199)부터
    assert not np.isnan(d["D3"][199])
    assert np.isnan(d["D11"][150])


def test_d11_counts_good_states_when_all_defined() -> None:
    d = sg.states(sg.compute(random_bars(400, 9)))
    t = 350
    expected = sum(d[f"D{i}"][t] for i in range(1, 11))
    assert d["D11"][t] == expected


def test_event_ids_and_state_ids() -> None:
    assert sg.EVENT_IDS == tuple(f"A{i}" for i in range(1, 16))
    assert sg.STATE_IDS == tuple(f"D{i}" for i in range(1, 12))
