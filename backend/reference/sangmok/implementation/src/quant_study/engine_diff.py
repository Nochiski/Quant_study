"""두 엔진의 equity curve를 세션 단위로 대조하는 순수 diff 로직."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BreachDetail:
    session: date
    left_equity: float
    right_equity: float
    rel_diff: float


@dataclass(frozen=True)
class EquityDiffReport:
    """left(커스텀 엔진) vs right(Zipline) 세션별 equity 비교 결과."""

    rel_tol: float
    matched_sessions: int
    left_only_sessions: int
    right_only_sessions: int
    max_rel_diff: float
    max_diff_session: date | None
    mean_rel_diff: float
    breach_count: int
    first_breach: BreachDetail | None

    @property
    def ok(self) -> bool:
        return self.breach_count == 0 and self.matched_sessions > 0


def diff_equity_curves(
    left: Mapping[date, float],
    right: Mapping[date, float],
    rel_tol: float,
) -> EquityDiffReport:
    """공통 세션의 equity를 상대 오차로 비교한다.

    상대 오차 = |left - right| / max(|right|, 1). 한쪽에만 있는 세션은
    비교에서 제외하되 개수를 보고한다 (캘린더 차이 진단용).
    """
    if rel_tol <= 0:
        raise ValueError(f"rel_tol must be > 0 — rel_tol={rel_tol}")

    common = sorted(set(left) & set(right))
    diffs: list[tuple[date, float]] = []
    breaches: list[BreachDetail] = []
    for session in common:
        rel_diff = abs(left[session] - right[session]) / max(abs(right[session]), 1.0)
        diffs.append((session, rel_diff))
        if rel_diff > rel_tol:
            breaches.append(
                BreachDetail(
                    session=session,
                    left_equity=left[session],
                    right_equity=right[session],
                    rel_diff=rel_diff,
                )
            )

    max_session: date | None = None
    max_diff = 0.0
    if diffs:
        max_session, max_diff = max(diffs, key=lambda item: item[1])

    return EquityDiffReport(
        rel_tol=rel_tol,
        matched_sessions=len(common),
        left_only_sessions=len(set(left) - set(right)),
        right_only_sessions=len(set(right) - set(left)),
        max_rel_diff=max_diff,
        max_diff_session=max_session,
        mean_rel_diff=sum(diff for _, diff in diffs) / len(diffs) if diffs else 0.0,
        breach_count=len(breaches),
        first_breach=breaches[0] if breaches else None,
    )


def format_report(name: str, report: EquityDiffReport) -> str:
    lines = [
        f"[{name}] {'OK' if report.ok else 'MISMATCH'}",
        f"  matched sessions   {report.matched_sessions} "
        f"(left-only {report.left_only_sessions}, right-only {report.right_only_sessions})",
        f"  max rel diff       {report.max_rel_diff:.3e} at {report.max_diff_session}",
        f"  mean rel diff      {report.mean_rel_diff:.3e}",
        f"  breaches (> {report.rel_tol:.0e})  {report.breach_count}",
    ]
    if report.first_breach is not None:
        breach = report.first_breach
        lines.append(
            f"  first breach       {breach.session} "
            f"engine={breach.left_equity:,.2f} zipline={breach.right_equity:,.2f} "
            f"rel={breach.rel_diff:.3e}"
        )
    return "\n".join(lines)
