"""동결된 창별 선택의 시점과 실행 정책 신원. 실제 연속 실행은 후속 책임이다."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from strategy_workbench.domain.canonical.facade.encoding import canonical_json_hash

from ._walk_forward import WalkForwardWindow

SELECTION_SCHEDULE_POLICY_VERSION = "selection-schedule-v1"


class SelectionScheduleStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class SelectionScheduleBlockReason(StrEnum):
    NO_CELL = "no_cell"
    SELECTION_TOO_LATE = "selection_too_late"
    NO_EXECUTABLE_SESSION = "no_executable_session"


@dataclass(frozen=True, kw_only=True)
class WindowSelection:
    """학습만으로 동결한 후보. known_as_of는 해당 날짜 종가 이후를 뜻한다.

    candidate_id는 외부에서 동결한 불변 신원이다. 이 타입은 후보 내용이나 학습 출처를
    증명하지 않는다. WindowPick.created_at은 실행 당시 시각이므로 이를 대신할 수 없다.
    """

    window_index: int
    window: WalkForwardWindow
    candidate_id: str | None
    known_as_of: date


@dataclass(frozen=True, kw_only=True)
class SelectionTransition:
    window_index: int
    candidate_id: str
    signal_as_of: date
    execution_on: date
    candidate_changed: bool


@dataclass(frozen=True, kw_only=True)
class SelectionScheduleBlock:
    window_index: int
    reason: SelectionScheduleBlockReason


@dataclass(frozen=True, kw_only=True)
class SelectionSchedule:
    """전체 일정을 검증하고 신원을 동결한다. 차단되면 부분 실행을 허용하지 않는다.

    sessions는 전체 검증 구간의 공통 측정 달력이다. 거래일 완전성은 호출자 책임이며
    종목 bar로 달력을 복원하지 않는다. 첫 신호는 test_start와 known_as_of 이상인 세션,
    적용은 바로 다음 세션이다. 두 세션 모두 해당 검증 창 안에 있어야 한다.

    NEXT_OPEN·DAY·REPLACE는 기존 커널 정책의 명시적 계약이다. 이 값은 실행기나
    후보별 tape 결합을 구현하지 않는다. 실제 보유 교체·잔량 만료는 커널 책임이고,
    동일 후보의 cadence 유지 및 변경 후보의 cadence 적용은 후속 실행기 책임이다.
    """

    selections: tuple[WindowSelection, ...]
    sessions: tuple[date, ...]
    policy_version: str = field(init=False, default=SELECTION_SCHEDULE_POLICY_VERSION)
    execution_timing: str = field(init=False, default="next_open")
    time_in_force: str = field(init=False, default="day")
    target_scope: str = field(init=False, default="replace")
    status: SelectionScheduleStatus = field(init=False)
    blocks: tuple[SelectionScheduleBlock, ...] = field(init=False)
    transitions: tuple[SelectionTransition, ...] = field(init=False)
    schedule_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_inputs(self.selections, self.sessions)
        transitions: list[SelectionTransition] = []
        blocks: list[SelectionScheduleBlock] = []
        previous_candidate: str | None = None
        for selection in self.selections:
            window = selection.window
            signal_index = bisect_left(self.sessions, max(window.test_start, selection.known_as_of))
            reason: SelectionScheduleBlockReason | None = None
            if selection.candidate_id is None:
                reason = SelectionScheduleBlockReason.NO_CELL
            elif selection.known_as_of > window.test_end:
                reason = SelectionScheduleBlockReason.SELECTION_TOO_LATE
            elif (
                signal_index + 1 >= len(self.sessions)
                or self.sessions[signal_index + 1] > window.test_end
            ):
                reason = SelectionScheduleBlockReason.NO_EXECUTABLE_SESSION
            if reason is not None:
                blocks.append(
                    SelectionScheduleBlock(window_index=selection.window_index, reason=reason)
                )
            else:
                candidate_id = selection.candidate_id
                if candidate_id is None:
                    raise RuntimeError(
                        "ready selection has no candidate: "
                        f"window_index={selection.window_index} candidate_id={candidate_id!r}"
                    )
                transitions.append(
                    SelectionTransition(
                        window_index=selection.window_index,
                        candidate_id=candidate_id,
                        signal_as_of=self.sessions[signal_index],
                        execution_on=self.sessions[signal_index + 1],
                        candidate_changed=candidate_id != previous_candidate,
                    )
                )
            previous_candidate = selection.candidate_id
        object.__setattr__(self, "blocks", tuple(blocks))
        object.__setattr__(self, "transitions", () if blocks else tuple(transitions))
        object.__setattr__(
            self,
            "status",
            SelectionScheduleStatus.BLOCKED if blocks else SelectionScheduleStatus.READY,
        )
        object.__setattr__(
            self,
            "schedule_hash",
            canonical_json_hash(
                {
                    "policy_version": self.policy_version,
                    "execution_timing": self.execution_timing,
                    "time_in_force": self.time_in_force,
                    "target_scope": self.target_scope,
                    "selections": [asdict(item) for item in self.selections],
                    "sessions": self.sessions,
                    "status": self.status,
                    "blocks": [asdict(item) for item in self.blocks],
                    "transitions": [asdict(item) for item in self.transitions],
                }
            ),
        )

    def candidate_at(self, session: date) -> str | None:
        """실행 세션부터 유효한 후보를 답한다. 신호 종가만으로 활성화하지 않는다.

        측정 달력 밖 조회는 거절한다. 창 사이 첫 적용 전까지는 직전 후보가
        유효하지만 이 조회가 그 후보의 추가 리밸런싱이나 주문을 허가하지는 않는다.
        """
        if self.status is SelectionScheduleStatus.BLOCKED:
            raise ValueError(
                "blocked selection schedule cannot resolve an active candidate: "
                f"schedule_hash={self.schedule_hash} status={self.status} "
                f"blocked_windows={tuple(item.window_index for item in self.blocks)}"
            )
        _require_date(session, context="candidate_at.session")
        if session not in self.sessions:
            raise ValueError(
                f"candidate session is outside the measurement calendar: session={session}"
            )
        index = bisect_right(tuple(item.execution_on for item in self.transitions), session) - 1
        return None if index < 0 else self.transitions[index].candidate_id


def _require_date(value: date, *, context: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"selection schedule requires a date: {context} value={value!r}")


def _validate_inputs(selections: tuple[WindowSelection, ...], sessions: tuple[date, ...]) -> None:
    if not isinstance(selections, tuple) or not selections:
        raise ValueError("selection schedule requires a nonempty immutable selections tuple")
    if not isinstance(sessions, tuple):
        raise ValueError("selection schedule requires an immutable sessions tuple")
    for index, session in enumerate(sessions):
        _require_date(session, context=f"sessions[{index}]")
        if index and session <= sessions[index - 1]:
            raise ValueError(
                f"sessions must be strictly increasing: index={index} session={session}"
            )
    for index, selection in enumerate(selections):
        if not isinstance(selection, WindowSelection):
            raise ValueError(
                "selections must contain immutable WindowSelection values: "
                f"index={index} type={type(selection).__name__}"
            )
        context = f"window_index={selection.window_index}"
        if not isinstance(selection.window, WalkForwardWindow):
            raise ValueError(
                f"selection requires a WalkForwardWindow: {context} "
                f"type={type(selection.window).__name__}"
            )
        if (
            isinstance(selection.window_index, bool)
            or not isinstance(selection.window_index, int)
            or selection.window_index != index
        ):
            raise ValueError(
                f"window indices must start at zero and be contiguous: expected={index} {context}"
            )
        window = selection.window
        for name in ("train_start", "train_end", "test_start", "test_end"):
            _require_date(getattr(window, name), context=f"{context} {name}")
        _require_date(selection.known_as_of, context=f"{context} known_as_of")
        if not window.train_start <= window.train_end < window.test_start <= window.test_end:
            raise ValueError(f"invalid training/test boundaries: {context} window={window}")
        if selection.known_as_of < window.train_end:
            raise ValueError(
                f"selection is known before training ends: {context} "
                f"known_as_of={selection.known_as_of} train_end={window.train_end}"
            )
        candidate_id = selection.candidate_id
        if candidate_id is not None and (
            not isinstance(candidate_id, str)
            or not candidate_id
            or any(c.isspace() for c in candidate_id)
        ):
            raise ValueError(
                f"candidate_id must be nonempty and contain no whitespace: {context} "
                f"candidate_id={candidate_id!r}"
            )
        if index and window.test_start != selections[index - 1].window.test_end + timedelta(days=1):
            raise ValueError(
                f"test windows must be adjacent without overlaps or gaps: {context} "
                f"test_start={window.test_start} "
                f"expected={selections[index - 1].window.test_end + timedelta(days=1)}"
            )
    if sessions and (
        sessions[0] < selections[0].window.test_start
        or sessions[-1] > selections[-1].window.test_end
    ):
        raise ValueError(
            "measurement sessions must be inside the complete test range: "
            f"sessions_start={sessions[0]} sessions_end={sessions[-1]} "
            f"test_start={selections[0].window.test_start} "
            f"test_end={selections[-1].window.test_end}"
        )
