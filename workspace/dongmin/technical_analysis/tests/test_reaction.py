"""선 반응(닿음·꺾임·관통·미결) — 사전등록 3.4 절."""

import numpy as np

from technical_analysis import reaction as rx

N = 30
ATR = np.full(N, 2.0)  # 닿음 허용 0.5, 꺾임·관통 2


def series(closes: dict[int, float], lows: dict[int, float] | None = None, highs: dict[int, float] | None = None):
    close = np.full(N, 101.0)
    for i, v in closes.items():
        close[i] = v
    low = close - 0.1
    high = close + 0.1
    for i, v in (lows or {}).items():
        low[i] = v
    for i, v in (highs or {}).items():
        high[i] = v
    return close, high, low


def flat(*levels: float) -> np.ndarray:
    return np.tile(np.array(levels, dtype=float), (N, 1))


def run(close, high, low, lines, t0: int = 1, t1: int = N - 1, last: int = N - 1) -> rx.Events:
    return rx.reactions(close, high, low, ATR, lines, t0, t1, last)


def test_touch_from_above_then_bounce_is_a_turn() -> None:
    close, high, low = series({5: 100.8, 6: 102.1}, lows={5: 100.3})
    ev = run(close, high, low, flat(100))
    assert ev.day.tolist() == [5]
    assert ev.side.tolist() == [rx.FROM_ABOVE]
    assert ev.outcome.tolist() == [rx.TURN]
    assert ev.resolved.tolist() == [6]


def test_touch_from_above_then_close_far_below_is_a_pierce() -> None:
    close, high, low = series({5: 99.0, 6: 97.9, 7: 103}, lows={5: 98.9})
    ev = run(close, high, low, flat(100))
    assert ev.outcome.tolist()[0] == rx.PIERCE
    assert ev.resolved.tolist()[0] == 6


def test_no_decision_within_ten_days_is_undecided_and_search_resumes_after() -> None:
    close = np.full(N, 101.0)
    close[4] = 101.0
    close[5:15] = 100.4  # 10일 내내 98 < 종가 < 102
    close[15] = 101.0
    low = close - 0.1
    high = close + 0.1
    low[5] = 100.3
    ev = run(close, high, low, flat(100), t1=14)
    assert ev.outcome.tolist() == [rx.UNDECIDED]
    assert ev.resolved.tolist() == [14]


def test_event_whose_window_runs_past_last_day_is_dropped() -> None:
    close, high, low = series({27: 100.5, 28: 100.5, 29: 100.5}, lows={27: 100.3})
    ev = run(close, high, low, flat(100))
    assert ev.day.shape[0] == 0


def test_event_resolved_before_last_day_is_kept_even_if_window_is_short() -> None:
    close, high, low = series({27: 100.5, 28: 102.5}, lows={27: 100.3})
    ev = run(close, high, low, flat(100))
    assert ev.outcome.tolist() == [rx.TURN]


def test_touch_from_below_is_symmetric() -> None:
    close = np.full(N, 99.0)
    close[5] = 99.2
    close[6] = 97.9
    low = close - 0.1
    high = close + 0.1
    high[5] = 99.7
    ev = run(close, high, low, flat(100))
    assert ev.side.tolist() == [rx.FROM_BELOW]
    assert ev.outcome.tolist() == [rx.TURN]


def test_gap_through_several_lines_takes_the_first_line_met() -> None:
    close, high, low = series({5: 94.5}, lows={5: 94.0})
    ev = run(close, high, low, flat(95, 100), t1=5)
    # 위에서 처음 만나는 선은 100 → 종가 94.5 ≤ 98 → 관통
    assert ev.line.tolist() == [1]
    assert ev.outcome.tolist() == [rx.PIERCE]


def test_line_must_be_clear_of_previous_close() -> None:
    # 전날 종가 100.4 는 100 + 0.5 안이라 '위에서 닿음'이 아니다
    close, high, low = series({4: 100.4, 5: 100.2}, lows={5: 100.1})
    ev = run(close, high, low, flat(100), t0=5, t1=5)
    assert ev.day.shape[0] == 0


def test_events_do_not_overlap() -> None:
    close, high, low = series({5: 100.8, 6: 100.6, 7: 102.1}, lows={5: 100.3, 6: 100.2})
    ev = run(close, high, low, flat(100), t1=7)
    # 5일에 닿고 7일에 꺾임. 6일의 닿음은 진행 중이라 새 반응이 아니다
    assert ev.day.tolist() == [5]


def test_both_sides_same_day_takes_line_nearer_previous_close() -> None:
    close, high, low = series({4: 101.5, 5: 101.0, 6: 102.1}, lows={5: 100.3}, highs={5: 103.8})
    ev = run(close, high, low, flat(100, 104), t0=5, t1=5)
    assert ev.line.tolist() == [0]
    assert ev.side.tolist() == [rx.FROM_ABOVE]


def test_sloped_line_is_measured_against_its_value_each_day() -> None:
    close = np.full(N, 101.0)
    line = 100 + 0.5 * np.arange(N)  # 매일 0.5 오른다
    # 전날 종가도 '그날(d) 선 값'과 비교한다: close4 = line4 + 1.2 = line5 + 0.7 > line5 + 0.5
    close[:] = line + 1.2
    close[5] = line[5] + 0.2
    close[6] = line[6] + 2.1  # 그날 선 + 2(ATR) 이상 → 꺾임
    low = close - 0.1
    high = close + 0.1
    low[5] = line[5] + 0.3
    ev = run(close, high, low, line[:, None], t0=5, t1=6)
    assert ev.outcome.tolist() == [rx.TURN]


def test_nan_lines_are_ignored() -> None:
    close, high, low = series({5: 100.8, 6: 102.1}, lows={5: 100.3})
    lines = flat(100, np.nan)
    ev = run(close, high, low, lines)
    assert ev.line.tolist() == [0]


def test_counts() -> None:
    close, high, low = series({5: 100.8, 6: 102.1, 10: 99.0, 11: 97.9}, lows={5: 100.3, 10: 98.9})
    ev = run(close, high, low, flat(100), t1=11)
    c = ev.counts()
    assert (c.turn, c.pierce, c.undecided) == (1, 1, 0)
    assert c.turn_rate == 0.5
