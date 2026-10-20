"""열 평가기의 횡단면·그룹 노드와 결측 채움(columnar-tape spec D3·D5, T3-05).

객체 평가기 `_cross_sectional`·`_group_transform`·`_NodeEvaluator._filled` 와 같은 의미를 동료 묶음
배치(`PeerLayout`) 위에서 계산한다. 공식(rank·zscore·winsorize·demean·중앙값)은 `_statistics.py` 의
구간 커널이 소유하고, 이 모듈은 묶음을 만들고 결과를 행 자리로 돌려놓기만 한다.

동료(`_peer_indices`): 횡단면 노드와 그 입력 채움·그래프 출력 채움은 같은 (as_of, universe_member),
그룹 노드와 그 입력 채움은 같은 (as_of, universe_member, 그룹)이다. 그룹 값이 문자열이 아닌 행(코드
−1)은 어느 그룹에도 들지 않아 그룹 노드 값이 없고 그룹 입력 채움도 받지 않는다. 묶음 안 원소 순서는
객체 경로 동료 목록과 같은 행 순서다 — 정렬의 동점(±0 포함)이 이 순서를 따른다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ._nodes import CrossSectionalOperator, GroupOperator, MissingPolicy
from ._statistics import (
    segment_demean,
    segment_median,
    segment_rank,
    segment_winsorize,
    segment_zscore,
)

# 묶음 배치를 날짜 경계에서 자른 조각마다 정렬하는 행 수. 조각마다 checkpoint 를 부른다(spec D7)
_LAYOUT_CHUNK = 1 << 18

_Floats = NDArray[np.float64]
_Bools = NDArray[np.bool_]
_Indices = NDArray[np.intp]


@dataclass(frozen=True, eq=False)
class PeerLayout:
    """동료 묶음 배치. `rows` 는 동료 키가 있는 행을 (묶음, 행) 순으로 담고, `segment` 는 행마다
    그 묶음 번호다."""

    rows: _Indices
    segment: _Indices
    segments: int


@dataclass(frozen=True, eq=False)
class _Members:
    """값이 있는 동료. `rows` 는 (묶음, 행) 순, `offsets` 는 값이 하나라도 있는 묶음의 경계,
    `segments` 는 그 묶음들의 `PeerLayout` 번호다."""

    rows: _Indices
    offsets: _Indices
    segments: _Indices


def peer_layout(
    row_date: NDArray[np.int32],
    universe_member: _Bools,
    group_codes: NDArray[np.int32] | None,
    checkpoint: Callable[[], None],
) -> PeerLayout:
    """(as_of, universe_member[, 그룹]) 묶음 배치. 묶음 안은 행 순서다(`_peer_indices` 와 같다).

    키가 날짜 우선이라 날짜 조각마다 키로 안정 정렬해 이으면 전체가 키 순이다. 행이 정본 순서
    ((as_of, security_id))면 날짜 정렬은 건너뛴다.
    """
    key = row_date.astype(np.int64) * 2 + universe_member
    if group_codes is None:
        rows = np.arange(row_date.shape[0], dtype=np.intp)
    else:
        key = key * (int(group_codes.max(initial=-1)) + 1) + group_codes
        rows = np.flatnonzero(group_codes >= 0).astype(np.intp)
    checkpoint()
    dates = row_date[rows]
    if bool(np.any(dates[1:] < dates[:-1])):
        # 정본 순서가 아닌 패널(대조용 경계 함수 입력)은 한 번 정렬한다. 같은 날짜 안의 행 순서는
        # 안정 정렬이 지킨다
        rows = rows[np.argsort(dates, kind="stable")]
        dates = row_date[rows]
        checkpoint()
    starts = np.flatnonzero(np.diff(dates)) + 1
    ordered = np.empty_like(rows)
    total = rows.shape[0]
    low = 0
    while low < total:
        checkpoint()
        high = _date_cut(starts, low, total)
        part = rows[low:high]
        ordered[low:high] = part[np.argsort(key[part], kind="stable")]
        low = high
    checkpoint()
    keys = key[ordered]
    fresh = np.ones(ordered.shape[0], bool)
    fresh[1:] = keys[1:] != keys[:-1]
    segment = (np.cumsum(fresh) - 1).astype(np.intp)
    return PeerLayout(ordered, segment, int(segment[-1]) + 1 if segment.size else 0)


def _date_cut(starts: NDArray[np.intp], low: int, total: int) -> int:
    """`low` 에서 시작하는 조각의 끝. `_LAYOUT_CHUNK` 행 안의 마지막 날짜 경계이고, 날짜 하나가
    조각보다 길면 그 날짜의 끝이다. `starts` 는 날짜가 바뀌는 자리(오름차순)다."""
    if low + _LAYOUT_CHUNK >= total:
        return total
    inside = int(np.searchsorted(starts, low + _LAYOUT_CHUNK, side="right")) - 1
    if inside >= 0 and starts[inside] > low:
        return int(starts[inside])
    following = int(np.searchsorted(starts, low, side="right"))
    return int(starts[following]) if following < starts.shape[0] else total


def _members(layout: PeerLayout, valid: _Bools, checkpoint: Callable[[], None]) -> _Members:
    take = valid[layout.rows]
    rows = layout.rows[take]
    checkpoint()
    counts = np.bincount(layout.segment[take], minlength=layout.segments)
    segments = np.flatnonzero(counts).astype(np.intp)
    offsets = np.concatenate(([0], np.cumsum(counts[segments]))).astype(np.intp)
    checkpoint()
    return _Members(rows, offsets, segments)


def _scatter(
    rows: int, members: _Members, computed: _Floats, checkpoint: Callable[[], None]
) -> tuple[_Floats, _Bools]:
    values = np.zeros(rows, np.float64)
    valid = np.zeros(rows, bool)
    values[members.rows] = computed
    valid[members.rows] = True
    checkpoint()
    return values, valid


def cross_sectional(
    operator: CrossSectionalOperator,
    quantiles: tuple[float, float],
    values: _Floats,
    valid: _Bools,
    layout: PeerLayout,
    checkpoint: Callable[[], None],
) -> tuple[_Floats, _Bools]:
    """횡단면 노드(`_cross_sectional`). 동료에 숫자가 없는 칸은 None 이다."""
    members = _members(layout, valid, checkpoint)
    sample = values[members.rows]
    if operator is CrossSectionalOperator.DEMEAN:
        computed = segment_demean(sample, members.offsets, checkpoint=checkpoint)
    elif operator is CrossSectionalOperator.RANK:
        computed = segment_rank(sample, members.offsets, checkpoint=checkpoint)
    elif operator is CrossSectionalOperator.ZSCORE:
        computed = segment_zscore(sample, members.offsets, checkpoint=checkpoint)
    else:
        computed = segment_winsorize(sample, members.offsets, *quantiles, checkpoint=checkpoint)
    return _scatter(values.shape[0], members, computed, checkpoint)


def group_transform(
    operator: GroupOperator,
    values: _Floats,
    valid: _Bools,
    layout: PeerLayout,
    checkpoint: Callable[[], None],
) -> tuple[_Floats, _Bools]:
    """그룹 노드(`_group_transform`). 그룹이 없는 행과 숫자가 아닌 칸은 None 이다."""
    members = _members(layout, valid, checkpoint)
    sample = values[members.rows]
    if operator is GroupOperator.NEUTRALIZE:
        computed = segment_demean(sample, members.offsets, checkpoint=checkpoint)
    else:
        computed = segment_rank(sample, members.offsets, checkpoint=checkpoint)
    return _scatter(values.shape[0], members, computed, checkpoint)


def filled(
    missing: MissingPolicy,
    values: _Floats,
    valid: _Bools,
    keep: _Bools,
    layout: PeerLayout,
    checkpoint: Callable[[], None],
) -> tuple[_Floats, _Bools]:
    """채움 정책(`zero`·`cross_sectional_median`)으로 빈칸을 채운다(`_NodeEvaluator._filled`).

    묶음에 숫자 값이 하나라도 있을 때만, 묶음에 든 행 가운데 값이 없고 `keep`(가린 칸) 이 아닌 칸을
    채운다. 채움 값은 `zero` 면 0.0, `cross_sectional_median` 이면 그 묶음 숫자 값의
    `statistics.median` 이다. 호출자는 채움 정책일 때만 부른다.
    """
    members = _members(layout, valid, checkpoint)
    fill = np.zeros(layout.segments, np.float64)
    numbered = np.zeros(layout.segments, bool)
    numbered[members.segments] = True
    if missing is MissingPolicy.CROSS_SECTIONAL_MEDIAN:
        fill[members.segments] = segment_median(
            values[members.rows], members.offsets, checkpoint=checkpoint
        )
    targets = layout.rows
    wanted = numbered[layout.segment] & ~valid[targets] & ~keep[targets]
    chosen = targets[wanted]
    checkpoint()
    result = values.copy()
    result[chosen] = fill[layout.segment[wanted]]
    present = valid.copy()
    present[chosen] = True
    checkpoint()
    return result, present
