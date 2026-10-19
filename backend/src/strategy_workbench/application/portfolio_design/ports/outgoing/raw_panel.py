"""열 지향 원시 관측 포트(columnar-tape spec D4·D5, T2-01).

`raw_observations.py` 와 같은 원시 관측을 행 객체가 아니라 열(numpy 배열)로 담는다. 원천 값·공개일
계약의 정본은 `raw_observations.py` 다. 두 포트는 같은 셀에 같은 값·공개일·종류를 답한다.

- 행 순서는 (as_of, security_id)다. `row_date` 와 `available` 은 일 서수(`date.toordinal()`)다.
  선언되지 않은 날짜도 담을 수 있어야 검증이 그 위반을 볼 수 있다.
- `security_id` 는 정렬된 사전(`securities`)의 코드다. 코드 순서가 Python 문자열 순서와 같아서
  행 키 비교를 코드 비교로 한다.
- 셀 생략(`present=False`)과 값 없음(`has_value=False`)을 따로 둔다. 숫자 값은 `value`, 문자열
  값은 `text`(범주 사전 `categories` 의 코드)에 담는다.
- `fields` 의 순서는 질의 `field_ids` 순서다. 원시 계약상 모든 관측의 fields 튜플이 이 순서의
  부분열이라, 첫 위반을 (행, 이 순서)로 고르면 객체 경로와 같다.
- 생성 시 계약을 검사하지 않는다. 소비 경계에서 `validate_contract` 를 한 번 부른다(spec
  DEFECT-CT-01 재검증 행). 생성 시에는 배열 길이·dtype·코드 범위 같은 구조만 본다.

검증 규칙이 `RawObservationSet.validate_contract`·`RawFieldValue.__post_init__` 와 두 벌인 것은 대조
기간에만 허용한다. T7-03 에서 객체 검증을 이 열 검증에서 파생해 한 벌로 줄인다. 그때까지 두 검증이
같은 첫 위반·예외 타입·메시지를 내는지는 `tests/application/test_raw_panel.py` 가 고정한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from strategy_workbench.application.portfolio_design.ports.outgoing.raw_observations import (
    RawFieldValue,
    RawObservation,
    RawObservationContractViolation,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind, DataLoadStatus

# `RawFieldColumn.kind` 코드는 이 튜플의 위치다. 셀이 없으면 -1 이다.
RAW_CELL_KINDS: tuple[CellKind, ...] = tuple(CellKind)
_VALUE_REQUIRED = np.array(
    [kind in (CellKind.OBSERVED, CellKind.SOURCE_OMITTED_ZERO) for kind in RAW_CELL_KINDS]
)
# 원소 단위 전체 연산은 이 행 수마다 checkpoint·progress 를 부른다(spec D7).
_CHUNK_ROWS = 1 << 18


def _noop_checkpoint() -> None:
    pass


def _noop_progress(fraction: float) -> None:
    pass


def _chunks(
    total: int,
    checkpoint: Callable[[], None],
    progress: Callable[[float], None],
    *,
    start: float,
    end: float,
) -> Iterator[slice]:
    """행 구간을 `_CHUNK_ROWS` 씩 나누고, 구간마다 [start, end) 진행을 보고한다."""
    for low in range(0, total, _CHUNK_ROWS):
        checkpoint()
        progress(start + (end - start) * low / total)
        yield slice(low, min(low + _CHUNK_ROWS, total))


def _first_hit(candidates: Sequence[NDArray[np.bool_]]) -> tuple[int, int] | None:
    """(행, 후보 위치) 사전순으로 처음 True 인 칸. 행이 같으면 앞 후보가 먼저다."""
    best: tuple[int, int] | None = None
    for position, hits in enumerate(candidates):
        rows = np.flatnonzero(hits)
        if rows.size and (best is None or rows[0] < best[0]):
            best = (int(rows[0]), position)
    return best


def _require_dtypes(owner: str, arrays: dict[str, tuple[NDArray[np.generic], type]]) -> None:
    """배열마다 dtype 이 정확히 기대 타입인지 본다. 다른 dtype 은 비교·비트 대조를 조용히 바꾼다."""
    wrong = {
        name: str(array.dtype)
        for name, (array, expected) in arrays.items()
        if array.dtype != np.dtype(expected)
    }
    if wrong:
        expected = {
            name: np.dtype(kind).name for name, (_, kind) in arrays.items() if name in wrong
        }
        raise ValueError(
            f"raw panel {owner} arrays have wrong dtypes — got={wrong} want={expected}"
        )


def _require_codes(name: str, codes: NDArray[np.integer], size: int, *, missing: bool) -> None:
    low = -1 if missing else 0
    if codes.size and (int(codes.min()) < low or int(codes.max()) >= size):
        raise ValueError(
            f"raw panel {name} code out of range — min={int(codes.min())} "
            f"max={int(codes.max())} allowed={low}..{size - 1} rows={codes.size}"
        )


@dataclass(frozen=True)
class RawCellPosition:
    """패널의 셀 하나. `row` 는 행 위치, `field` 는 `RawPanel.fields` 위치다."""

    row: int
    field: int


@dataclass(frozen=True, eq=False)
class RawFieldColumn:
    """한 필드의 셀 열. 모든 배열은 패널 행 수와 길이가 같다."""

    field_id: str
    present: NDArray[np.bool_]  # 셀 유무. False 면 생략(발표 전)이다
    has_value: NDArray[np.bool_]  # 셀은 있고 값이 None 이면 False
    value: NDArray[np.float64]  # 숫자 값. 숫자가 아닌 칸은 0.0
    text: NDArray[np.int32]  # 문자열 값의 `categories` 코드. 숫자·값 없음은 -1
    available: NDArray[np.int32]  # 공개일 일 서수. 셀이 없으면 0
    kind: NDArray[np.int8]  # `RAW_CELL_KINDS` 코드. 셀이 없으면 -1
    categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        rows = self.present.shape[0]
        arrays = (self.has_value, self.value, self.text, self.available, self.kind)
        if any(array.shape != (rows,) for array in arrays):
            raise ValueError(
                "raw panel field arrays must share one row length — "
                f"field_id={self.field_id!r} rows={rows} "
                f"shapes={[array.shape for array in arrays]}"
            )
        _require_dtypes(
            f"field {self.field_id!r}",
            {
                "present": (self.present, np.bool_),
                "has_value": (self.has_value, np.bool_),
                "value": (self.value, np.float64),
                "text": (self.text, np.int32),
                "available": (self.available, np.int32),
                "kind": (self.kind, np.int8),
            },
        )
        _require_codes("text", self.text, len(self.categories), missing=True)
        _require_codes("kind", self.kind, len(RAW_CELL_KINDS), missing=True)
        if bool((self.kind[self.present] < 0).any()):
            raise ValueError(f"raw panel present cell needs a kind — field_id={self.field_id!r}")
        # 일 서수는 1(0001-01-01)부터다. 0 은 "셀 없음" 자리라 present 셀의 날짜로 풀 수 없다.
        if bool((self.available[self.present] < 1).any()):
            raise ValueError(
                "raw panel present cell needs an available ordinal >= 1 — "
                f"field_id={self.field_id!r}"
            )


@dataclass(frozen=True, eq=False)
class RawPanel:
    """`RawObservationSet` 의 열 표현. 행 i 가 관측 i 다."""

    status: DataLoadStatus
    data_snapshot_id: str
    sessions: tuple[date, ...]
    history_sessions: tuple[date, ...]
    securities: tuple[str, ...]  # 엄격 오름차순 사전. `row_security` 가 위치를 가리킨다
    row_date: NDArray[np.int32]  # as_of 일 서수
    row_security: NDArray[np.int32]
    universe_member: NDArray[np.bool_]
    sector: NDArray[np.int32]  # `sectors` 코드. None 은 -1
    previous_weight: NDArray[np.float64]
    fields: tuple[RawFieldColumn, ...]
    sectors: tuple[str, ...] = ()
    detail: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        rows = self.row_date.shape[0]
        arrays = (self.row_security, self.universe_member, self.sector, self.previous_weight)
        shapes = [array.shape for array in arrays] + [c.present.shape for c in self.fields]
        if any(shape != (rows,) for shape in shapes):
            raise ValueError(
                f"raw panel arrays must share one row length — rows={rows} shapes={shapes}"
            )
        _require_dtypes(
            "row",
            {
                "row_date": (self.row_date, np.int32),
                "row_security": (self.row_security, np.int32),
                "universe_member": (self.universe_member, np.bool_),
                "sector": (self.sector, np.int32),
                "previous_weight": (self.previous_weight, np.float64),
            },
        )
        if rows and int(self.row_date.min()) < 1:
            raise ValueError(
                f"raw panel row_date ordinals must be >= 1 — min={self.row_date.min()}"
            )
        if list(self.securities) != sorted(set(self.securities)):
            raise ValueError(
                "raw panel securities must ascend without duplicates — "
                f"count={len(self.securities)} head={self.securities[:5]}"
            )
        field_ids = [column.field_id for column in self.fields]
        if len(set(field_ids)) != len(field_ids):
            raise ValueError(f"raw panel field_ids must be unique — field_ids={field_ids}")
        _require_codes("security", self.row_security, len(self.securities), missing=False)
        _require_codes("sector", self.sector, len(self.sectors), missing=True)

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK

    def _key(self, row: int) -> tuple[date, str]:
        return date.fromordinal(int(self.row_date[row])), self.securities[self.row_security[row]]

    def _cell_value(self, column: RawFieldColumn, row: int) -> float | str | None:
        if not column.has_value[row]:
            return None
        code = int(column.text[row])
        return column.categories[code] if code >= 0 else float(column.value[row])

    def validate_contract(
        self,
        *,
        checkpoint: Callable[[], None] = _noop_checkpoint,
        progress: Callable[[float], None] = _noop_progress,
    ) -> None:
        """`RawObservationSet` 생성·소비 경계 검증과 같은 첫 위반을 같은 예외·메시지로 낸다.

        순서: 셀 생성 시점 검사(`RawFieldValue.__post_init__`, 객체에서는 집합보다 먼저 만든다) →
        세션 → 정렬·유일 → 미선언 날짜 → 행별 previous_weight·필드. `progress` 는 0~1 을 받고
        1.0 으로 끝난다.
        """
        checkpoint()
        rows = self.row_date.shape[0]
        for part in _chunks(rows, checkpoint, progress, start=0.0, end=0.1):
            self._validate_cells(part)
        if list(self.sessions) != sorted(set(self.sessions)):
            raise RawObservationContractViolation(
                f"raw observation sessions must ascend — sessions={self.sessions}"
            )
        if list(self.history_sessions) != sorted(set(self.history_sessions)):
            raise RawObservationContractViolation(
                f"raw observation history must ascend — history={self.history_sessions}"
            )
        overlaps = bool(self.sessions and self.history_sessions)
        if overlaps and self.history_sessions[-1] >= self.sessions[0]:
            raise RawObservationContractViolation(
                "raw observation history must precede the requested range — "
                f"last_history={self.history_sessions[-1]} first_session={self.sessions[0]}"
            )
        for part in _chunks(rows, checkpoint, progress, start=0.1, end=0.3):
            # 구간 첫 행은 앞 구간 마지막 행과 비교한다.
            low = max(part.start, 1)
            day, code = self.row_date, self.row_security
            current, before = slice(low, part.stop), slice(low - 1, part.stop - 1)
            same_day = day[current] == day[before]
            unordered = (day[current] < day[before]) | (same_day & (code[current] <= code[before]))
            hits = np.flatnonzero(unordered)
            if hits.size:
                row = low + int(hits[0])
                raise RawObservationContractViolation(
                    "raw observations must be ordered by (as_of, security_id) and unique — "
                    f"count={rows} previous={self._key(row - 1)} key={self._key(row)}"
                )
        declared = np.array(
            [day.toordinal() for day in (*self.sessions, *self.history_sessions)], dtype=np.int32
        )
        stray: set[int] = set()
        for part in _chunks(rows, checkpoint, progress, start=0.3, end=0.4):
            days = self.row_date[part]
            stray.update(np.unique(days[~np.isin(days, declared)]).tolist())
        if stray:
            undeclared = [date.fromordinal(day) for day in sorted(stray)]
            raise RawObservationContractViolation(
                "raw observations carry dates that are neither a session nor warm-up history — "
                f"undeclared={undeclared[:5]} undeclared_count={len(undeclared)} "
                f"declared_sessions={len(self.sessions)} "
                f"declared_history={len(self.history_sessions)} "
                f"snapshot={self.data_snapshot_id!r}"
            )
        for part in _chunks(rows, checkpoint, progress, start=0.4, end=1.0):
            # 행 안에서는 previous_weight 가 필드보다 먼저다.
            candidates = [~np.isfinite(self.previous_weight[part])]
            candidates.extend(
                column.present[part]
                & column.has_value[part]
                & (column.text[part] < 0)
                & ~np.isfinite(column.value[part])
                for column in self.fields
            )
            hit = _first_hit(candidates)
            if hit is None:
                continue
            row, position = part.start + hit[0], hit[1]
            as_of, security_id = self._key(row)
            if position == 0:
                raise RawObservationContractViolation(
                    "raw observation previous_weight must be finite — "
                    f"as_of={as_of} security_id={security_id!r} "
                    f"value={float(self.previous_weight[row])!r} "
                    f"snapshot={self.data_snapshot_id!r}"
                )
            column = self.fields[position - 1]
            raise RawObservationContractViolation(
                "raw numeric field value must be finite — "
                f"as_of={as_of} security_id={security_id!r} "
                f"field_id={column.field_id!r} value={float(column.value[row])!r} "
                f"snapshot={self.data_snapshot_id!r}"
            )
        progress(1.0)

    def _validate_cells(self, part: slice) -> None:
        """`RawFieldValue.__post_init__` 의 칸 검사(빈 field_id, 종류와 값 유무의 정합)."""
        candidates: list[NDArray[np.bool_]] = []
        for column in self.fields:
            present = column.present[part]
            if not column.field_id.strip():
                candidates.append(present)
                continue
            required = _VALUE_REQUIRED[column.kind[part]]  # 셀이 없는 칸(-1)은 present 가 거른다
            candidates.append(present & (required != column.has_value[part]))
        hit = _first_hit(candidates)
        if hit is None:
            return
        row, column = part.start + hit[0], self.fields[hit[1]]
        available = date.fromordinal(int(column.available[row]))
        kind = RAW_CELL_KINDS[column.kind[row]]
        if not column.field_id.strip():
            raise RawObservationContractViolation(
                "raw field_id must not be blank — "
                f"field_id={column.field_id!r} available_date={available}"
            )
        if column.has_value[row]:
            raise RawObservationContractViolation(
                "unavailable raw field must not carry a value — "
                f"field_id={column.field_id!r} available_date={available} "
                f"kind={kind.value!r} value={self._cell_value(column, row)!r}"
            )
        raise RawObservationContractViolation(
            "observed raw field requires a value — "
            f"field_id={column.field_id!r} available_date={available} "
            f"kind={kind.value!r}"
        )

    def first_look_ahead(
        self, *, checkpoint: Callable[[], None] = _noop_checkpoint
    ) -> RawCellPosition | None:
        """공개일이 관측일보다 늦은 첫 셀의 (행, 필드) 위치. 없으면 None 이다.

        객체 경로는 소비 경계(`_service._to_factor_observation`)에서 `LookAheadViolationError` 를
        낸다. 예외와 문장은 application 서비스가 소유하므로(`_service._look_ahead_violation`)
        여기서는 위치만 돌려준다. 이 검사는 `validate_contract` 에 넣지 않는다. 넣으면 관문
        순서(spec D6)가 바뀐다.
        """
        rows = self.row_date.shape[0]
        for part in _chunks(rows, checkpoint, _noop_progress, start=0.0, end=1.0):
            days = self.row_date[part]
            hit = _first_hit(
                [column.present[part] & (column.available[part] > days) for column in self.fields]
            )
            if hit is not None:
                return RawCellPosition(row=part.start + hit[0], field=hit[1])
        return None

    def observations(self, rows: Sequence[int] | None = None) -> tuple[RawObservation, ...]:
        """`rows`(없으면 전체) 행을 `RawObservation` 으로 만든다.

        numpy 스칼라는 Python float·bool·int·str 로 바꾼다(spec D3 해시 payload 행).
        """
        index = (
            np.arange(self.row_date.shape[0]) if rows is None else np.asarray(rows, dtype=np.intp)
        )
        dates: dict[int, date] = {}

        def day(ordinal: int) -> date:
            found = dates.get(ordinal)
            if found is None:
                found = dates[ordinal] = date.fromordinal(ordinal)
            return found

        # tolist() 는 원소를 Python float·bool·int 로 바꾼다. float 비트(±0 포함)는 그대로다.
        columns = [
            (
                column,
                column.present[index].tolist(),
                column.has_value[index].tolist(),
                column.value[index].tolist(),
                column.text[index].tolist(),
                column.available[index].tolist(),
                column.kind[index].tolist(),
            )
            for column in self.fields
        ]
        result: list[RawObservation] = []
        for position, (ordinal, security, member, sector, weight) in enumerate(
            zip(
                self.row_date[index].tolist(),
                self.row_security[index].tolist(),
                self.universe_member[index].tolist(),
                self.sector[index].tolist(),
                self.previous_weight[index].tolist(),
                strict=True,
            )
        ):
            fields: list[RawFieldValue] = []
            for column, present, has_value, value, text, available, kind in columns:
                if not present[position]:
                    continue
                cell: float | str | None = None
                if has_value[position]:
                    code = text[position]
                    cell = column.categories[code] if code >= 0 else value[position]
                fields.append(
                    RawFieldValue(
                        column.field_id,
                        cell,
                        day(available[position]),
                        RAW_CELL_KINDS[kind[position]],
                    )
                )
            result.append(
                RawObservation(
                    as_of=day(ordinal),
                    security_id=self.securities[security],
                    universe_member=member,
                    fields=tuple(fields),
                    sector_id=None if sector < 0 else self.sectors[sector],
                    previous_weight=weight,
                )
            )
        return tuple(result)

    @classmethod
    def from_observation_set(cls, raw: RawObservationSet, *, field_ids: Sequence[str]) -> RawPanel:
        """객체 관측을 같은 셀의 열로 옮긴다(구조 변환, 계약은 검사하지 않는다).

        열 순서는 질의 `field_ids` 순서다. 한 번도 나오지 않은 필드는 present 가 모두 False 인
        열이다.
        열에 담을 수 없는 관측은 `validate_contract` 와 같은 계약 위반 예외로 거절한다. 한 관측 안의
        중복 field_id, float·str·None 이 아닌 값(bool·int 포함), `field_ids` 의 부분열이 아닌
        fields(원시 계약의 순서 조항)가 그렇다.

        이 거절들은 열에 담기 위한 방어용 검사다. 위반이 여럿 섞인 집합에서는 첫 위반이 객체 경로
        (`RawObservationSet` 생성 → `validate_contract`)와 다른 순서로 걸릴 수 있다. 위반이 하나뿐인
        집합에서는 같은 메시지다.
        """
        observations = raw.observations
        rows = len(observations)
        order = tuple(field_ids)
        securities = tuple(sorted({item.security_id for item in observations}))
        security_code = {security_id: code for code, security_id in enumerate(securities)}
        sector_code: dict[str, int] = {}
        position = {field_id: index for index, field_id in enumerate(order)}
        present = [[False] * rows for _ in order]
        has_value = [[False] * rows for _ in order]
        value = [[0.0] * rows for _ in order]
        text = [[-1] * rows for _ in order]
        available = [[0] * rows for _ in order]
        kind = [[-1] * rows for _ in order]
        categories: list[dict[str, int]] = [{} for _ in order]
        kind_code = {cell_kind: code for code, cell_kind in enumerate(RAW_CELL_KINDS)}
        sectors: list[int] = []
        for row, item in enumerate(observations):
            sectors.append(
                -1
                if item.sector_id is None
                else sector_code.setdefault(item.sector_id, len(sector_code))
            )
            previous = -1
            for field in item.fields:
                column = position.get(field.field_id, -1)
                cell = field.value
                if column >= 0 and present[column][row]:
                    raise RawObservationContractViolation(
                        "raw observation field_ids must be unique — "
                        f"as_of={item.as_of} security_id={item.security_id!r} "
                        f"field_id={field.field_id!r} snapshot={raw.data_snapshot_id!r}"
                    )
                if column <= previous:
                    raise RawObservationContractViolation(
                        "raw observation fields must follow the query field_ids order — "
                        f"as_of={item.as_of} security_id={item.security_id!r} "
                        f"field_id={field.field_id!r} "
                        f"fields={tuple(f.field_id for f in item.fields)} field_ids={order} "
                        f"snapshot={raw.data_snapshot_id!r}"
                    )
                previous = column
                if cell is not None and not isinstance(cell, float | str):
                    raise RawObservationContractViolation(
                        "raw field value must be float, str or None — "
                        f"as_of={item.as_of} security_id={item.security_id!r} "
                        f"field_id={field.field_id!r} value={cell!r} "
                        f"snapshot={raw.data_snapshot_id!r}"
                    )
                present[column][row] = True
                available[column][row] = field.available_date.toordinal()
                kind[column][row] = kind_code[field.kind]
                if cell is None:
                    continue
                has_value[column][row] = True
                if isinstance(cell, str):
                    text[column][row] = categories[column].setdefault(cell, len(categories[column]))
                else:
                    value[column][row] = cell
        return cls(
            status=raw.status,
            data_snapshot_id=raw.data_snapshot_id,
            sessions=raw.sessions,
            history_sessions=raw.history_sessions,
            securities=securities,
            row_date=np.array([item.as_of.toordinal() for item in observations], dtype=np.int32),
            row_security=np.array(
                [security_code[item.security_id] for item in observations], dtype=np.int32
            ),
            universe_member=np.array(
                [item.universe_member for item in observations], dtype=np.bool_
            ),
            sector=np.array(sectors, dtype=np.int32),
            previous_weight=np.array(
                [item.previous_weight for item in observations], dtype=np.float64
            ),
            fields=tuple(
                RawFieldColumn(
                    field_id=field_id,
                    present=np.array(present[index], dtype=np.bool_),
                    has_value=np.array(has_value[index], dtype=np.bool_),
                    value=np.array(value[index], dtype=np.float64),
                    text=np.array(text[index], dtype=np.int32),
                    available=np.array(available[index], dtype=np.int32),
                    kind=np.array(kind[index], dtype=np.int8),
                    categories=tuple(categories[index]),
                )
                for index, field_id in enumerate(order)
            ),
            sectors=tuple(sector_code),
            detail=raw.detail,
            warnings=raw.warnings,
        )


@runtime_checkable
class ColumnarRawObservationPort(Protocol):
    """선택 능력: 원시 관측을 열로 바로 낸다(T2-02 에서 duckdb 가 구현).

    결과는 `RawPanel.from_observation_set(load_raw_observations(query), field_ids=query.field_ids)`
    와 셀마다 같아야 한다(spec 5절 L0). 능력이 없는 어댑터는 application 이 그 구조 변환을 쓴다.
    `checkpoint`·`progress` 규칙은 `ProgressReportingRawObservationPort` 와 같다.
    """

    def load_raw_panel(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
        progress: Callable[[float], None],
    ) -> RawPanel: ...
