"""PLAN tracker에서 집계만 파생하는 플랫폼 독립 정본.

상태를 판정해 tracker에 쓰지 않는다. 계약·사용법은 tools/PLAN_PROGRESS.md를 따른다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

KST = timezone(timedelta(hours=9))
ACTIVE_PRIORITY = (
    "CHANGES_REQUESTED",
    "IN_PROGRESS",
    "SELF_CHECK",
    "IN_REVIEW",
    "APPROVED",
)
ALLOWED_STATUSES = frozenset(
    (*ACTIVE_PRIORITY, "PLANNED", "READY", "WAITING", "MERGED", "PAUSED")
)


@dataclass(frozen=True)
class PlanProfile:
    phases: tuple[tuple[str, str], ...]
    pr_id_pattern: str
    supports_integration: bool = False


PROFILES: dict[str, PlanProfile] = {
    "ai-assistant": PlanProfile(
        (
            ("P0", "Planning package"),
            ("A", "Backend: ports, storage, HTTP, providers"),
            ("B", "Frontend: settings, entity, sidebar, e2e"),
            ("C", "Phase A/B audit follow-up"),
            ("D", "Backtest result explanation (US-DM-08)"),
        ),
        "(?:P\\d+(?:\\.\\d+)?|[A-Z])-\\d{2}",
        False,
    ),
    "columnar-tape": PlanProfile(
        (
            ("T0", "Planning package and prerequisite defect"),
            ("T1", "Parity harness"),
            ("T2", "Columnar raw panel port"),
            ("T3", "Columnar factor evaluator"),
            ("T4", "Pipeline switch"),
            ("T5", "Columnar compiler"),
            ("T6", "Consumer migration"),
            ("T7", "Legacy path removal"),
            ("T9", "Closeout review"),
        ),
        "T\\d+-\\d{2}",
        False,
    ),
    "run-worker-process": PlanProfile(
        (
            ("W0", "Planning package and benchmark"),
            ("W1", "Computation boundary and worker process"),
            ("W2", "Spare worker and memory guard"),
            ("W3", "Default switch and SoT revision"),
            ("W4", "Factor regression job"),
            ("W5", "Interactive path decision gate"),
            ("W9", "Closeout review"),
        ),
        "W\\d+-\\d{2}",
        False,
    ),
    "strategy-gui-editing": PlanProfile(
        (
            ("P1", "Backend schema 1.1"),
            ("P2", "Frontend 1.1 and upgrade UI"),
            ("P3", "Source transactions"),
            ("P4", "Form editing"),
            ("P5", "Graph editing"),
        ),
        "P\\d+(?:\\.\\d+)?-\\d{2}",
        False,
    ),
    "strategy-language-2-0": PlanProfile(
        (
            ("P0", "Planning package and contract docs"),
            ("P1", "In-screen friction removal on 1.1"),
            ("P2", "Backend schema 1.2 (environment split, 10 PRs)"),
            ("P3", "Frontend 1.2 adaptation"),
            ("P4", "Graph level 1: pipeline"),
            ("P5", "Graph level 2: recipe"),
            ("P6", "Graph level 3: node canvas"),
        ),
        "P\\d+(?:\\.\\d+)?-\\d{2}",
        True,
    ),
    "strategy-workbench-yaml-ui": PlanProfile(
        (
            ("P0", "Contract, product direction, tool choices"),
            ("P1", "Backend Authoring Contract"),
            ("P1.5", "Backtest Correctness Gate"),
            ("P2", "App Shell and visual foundation"),
            ("P3", "YAML Editor MVP"),
            ("P4", "Outline, Contract, Projections"),
            ("P5", "Truthful Trace UI"),
            ("P6", "Professional release and migration"),
        ),
        "P\\d+(?:\\.\\d+)?-\\d{2}",
        False,
    ),
    "validation-lab": PlanProfile(
        (
            ("V0", "Planning package"),
            ("V1", "Research window seal, run persistence, trial ledger"),
            ("V2", "Cost realism: sell tax, ADV participation, sqrt impact"),
            ("V3", "Experiment backend, async queue, walk-forward"),
            (
                "V4",
                "Validation statistics: PSR, DSR, plateau, capacity, factor regression",
            ),
            ("V5", "Screens (after lang2 merge signal)"),
            ("V6", "Holdout one-time opening"),
        ),
        "V\\d+-\\d{2}",
        False,
    ),
}


@dataclass(frozen=True)
class TrackerRow:
    pr_id: str
    phase: str
    checked: bool
    dependency: str
    status: str


@dataclass(frozen=True)
class PlanAggregation:
    text: str
    project_status: str
    total_count: int
    done_count: int
    merged_count: int
    integrated_count: int
    approved_count: int
    progress_percent: int


class PlanStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class PlanResult:
    """VALID는 집계만, INVALID는 재현 가능한 실패 사유만 가진다."""

    status: PlanStatus
    aggregation: PlanAggregation | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        valid_success = (
            self.status is PlanStatus.VALID
            and self.aggregation is not None
            and not self.detail
        )
        valid_failure = (
            self.status is PlanStatus.INVALID
            and self.aggregation is None
            and bool(self.detail.strip())
        )
        if valid_success or valid_failure:
            return
        raise ValueError(
            "invalid PlanResult combination — "
            f"status={self.status!r} aggregation_present={self.aggregation is not None} "
            f"detail={self.detail!r}; expected=VALID+aggregation or INVALID+nonempty detail"
        )


class _InvalidPlan(ValueError):
    pass


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:([^\r\n]*)", text, re.MULTILINE)
    if match is None:
        raise _InvalidPlan(f"frontmatter field missing — field={name}")
    return match.group(1).strip()


def _rows(text: str, profile: PlanProfile) -> tuple[TrackerRow, ...]:
    pattern = (
        rf"^\| \[(?P<checked>[ xX])\] \| `(?P<id>{profile.pr_id_pattern})` "
        r"\| (?P<title>.*?) \| (?P<dependency>.*?) \| `(?P<status>[A-Z_]+)` "
        r"\| (?P<review>.*?) \|[ \t\r]*$"
    )
    rows = tuple(
        TrackerRow(
            m["id"],
            m["id"].rsplit("-", 1)[0],
            m["checked"] in "xX",
            m["dependency"],
            m["status"],
        )
        for m in re.finditer(pattern, text, re.MULTILINE)
    )
    if not rows:
        raise _InvalidPlan(
            "no PR tracker rows — expected=checkbox/id/dependency/status table"
        )
    return rows


def _validate(
    rows: tuple[TrackerRow, ...], profile: PlanProfile, parallel: tuple[str, ...]
) -> None:
    by_id = {row.pr_id: row for row in rows}
    if len(by_id) != len(rows):
        raise _InvalidPlan(f"duplicate PR ids — ids={[row.pr_id for row in rows]}")
    done = {"MERGED", "INTEGRATED"} if profile.supports_integration else {"MERGED"}
    allowed = ALLOWED_STATUSES | done
    phases = dict(profile.phases)
    for row in rows:
        if row.status not in allowed:
            raise _InvalidPlan(
                f"unknown PR status — id={row.pr_id} status={row.status}"
            )
        if row.checked != (row.status in done):
            raise _InvalidPlan(
                f"checkbox/status mismatch — id={row.pr_id} status={row.status}"
            )
        if row.phase not in phases:
            raise _InvalidPlan(f"unknown phase — id={row.pr_id} phase={row.phase}")
        for dependency in dict.fromkeys(
            re.findall(profile.pr_id_pattern, row.dependency)
        ):
            if dependency not in by_id:
                raise _InvalidPlan(
                    f"unknown dependency — id={row.pr_id} dependency={dependency}"
                )
            if row.status == "READY" and by_id[dependency].status not in done:
                raise _InvalidPlan(
                    f"READY dependency not done — id={row.pr_id} dependency={dependency}"
                )
    active = tuple(row.pr_id for row in rows if row.status in ACTIVE_PRIORITY)
    if len(active) > 2 and len(parallel) < len(active):
        raise _InvalidPlan(
            f"active stack not declared — active={active} parallel_window={parallel}"
        )
    if len(active) > 1 and sorted(active) != sorted(parallel):
        raise _InvalidPlan(
            f"active ids differ from parallel_window — active={active} parallel={parallel}"
        )
    for phase in phases:
        if not any(row.phase == phase for row in rows):
            raise _InvalidPlan(f"phase has no tracker rows — phase={phase}")


def _status(
    rows: Sequence[TrackerRow], *, phase: bool = False, integrated: bool = False
) -> str:
    if all(row.status == "MERGED" for row in rows):
        return "MERGED" if phase else "COMPLETE"
    if phase and integrated and all(row.checked for row in rows):
        return "INTEGRATED"
    statuses = {row.status for row in rows}
    for status in (*ACTIVE_PRIORITY, "READY", "PAUSED"):
        if status in statuses:
            return status
    return "WAITING"


def _block(text: str, name: str, lines: Sequence[str], newline: str) -> str:
    start, end = f"<!-- PLAN:{name}:START -->", f"<!-- PLAN:{name}:END -->"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    if not pattern.search(text):
        raise _InvalidPlan(f"generated block missing — block={name}")
    return pattern.sub(lambda _: newline.join((start, *lines, end)), text, count=1)


def aggregate_plan(
    text: str, profile: PlanProfile, *, timestamp: datetime | None = None
) -> PlanResult:
    """명시 tracker 입력을 검증하고 집계한다. timestamp 생략 시 기존 시각으로 check 결과를 만든다."""
    try:
        return PlanResult(PlanStatus.VALID, _aggregate(text, profile, timestamp))
    except (ValueError, OverflowError) as error:
        return PlanResult(PlanStatus.INVALID, detail=str(error))


def _aggregate(
    text: str, profile: PlanProfile, timestamp: datetime | None
) -> PlanAggregation:
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = _rows(text, profile)
    raw_parallel = _field(text, "parallel_window")
    if not raw_parallel.startswith("[") or not raw_parallel.endswith("]"):
        raise _InvalidPlan(
            f"invalid parallel_window — expected=[ids] got={raw_parallel!r}"
        )
    parallel = tuple(
        value.strip().strip("\"'")
        for value in raw_parallel[1:-1].split(",")
        if value.strip()
    )
    _validate(rows, profile, parallel)
    active = tuple(row for row in rows if row.status in ACTIVE_PRIORITY)
    by_id = {row.pr_id: row for row in rows}
    ordered = tuple(
        by_id[identity]
        for identity in parallel
        if identity in by_id and by_id[identity] in active
    )
    done_statuses = (
        {"MERGED", "INTEGRATED"} if profile.supports_integration else {"MERGED"}
    )
    current = (
        ordered
        or active
        or tuple(row for row in rows if row.status == "READY")[:1]
        or tuple(row for row in rows if row.status not in done_statuses)[:1]
    )
    current_pr = ",".join(row.pr_id for row in current) or "none"
    current_phase = ",".join(dict.fromkeys(row.phase for row in current)) or "complete"
    total = len(rows)
    done = sum(row.checked for row in rows)
    merged = sum(row.status == "MERGED" for row in rows)
    integrated = sum(row.status == "INTEGRATED" for row in rows)
    approved = sum(row.status in done_statuses | {"APPROVED"} for row in rows)
    progress = round(done * 100.0 / total)
    project_status = _status(rows)
    old_timestamp = _field(text, "last_updated")
    instant = (
        timestamp if timestamp is not None else datetime.fromisoformat(old_timestamp)
    )
    iso_timestamp = (
        instant.isoformat(timespec="seconds")
        if timestamp is not None
        else old_timestamp
    )
    display_timestamp = instant.strftime("%Y-%m-%d %H:%M KST")
    values: dict[str, str | int] = {
        "project_status": project_status,
        "current_phase": current_phase,
        "current_pr": current_pr,
        "active_prs": "[" + ", ".join(row.pr_id for row in active) + "]",
        "last_updated": iso_timestamp,
        "planned_prs": total,
        "merged_prs": merged,
        "approved_prs": approved,
        "progress_percent": progress,
    }
    if profile.supports_integration:
        values["integrated_prs"] = integrated
    updated = text
    for name, value in values.items():
        _field(updated, name)
        updated = re.sub(
            rf"^{re.escape(name)}:[^\r\n]*",
            lambda _, n=name, v=value: f"{n}: {v}",
            updated,
            count=1,
            flags=re.MULTILINE,
        )
    active_text = (
        "`" + ", ".join(row.pr_id for row in active) + "`" if active else "none"
    )
    progress_text = (
        f"{done} / {total} done ({progress}%), main {merged}, integration {integrated}"
        if profile.supports_integration
        else f"{merged} / {total} merged ({progress}%)"
    )
    summary = (
        "| Field | Value |",
        "|---|---|",
        f"| Project status | `{project_status}` |",
        f"| Current phase | `{current_phase}` |",
        f"| Current/next PR | `{current_pr}` |",
        f"| Active PR | {active_text} |",
        f"| Progress | `{progress_text}` |",
        f"| Approved | `{approved} / {total}` |",
        f"| Aggregated at | `{display_timestamp}` |",
    )
    updated = _block(updated, "SUMMARY", summary, newline)
    phase_lines = (
        [
            "| Phase | Goal | PR | Main | Integrated | Status |",
            "|---|---|---:|---:|---:|---|",
        ]
        if profile.supports_integration
        else ["| Phase | Goal | PR | Merged | Status |", "|---|---|---:|---:|---|"]
    )
    for phase, goal in profile.phases:
        group = tuple(row for row in rows if row.phase == phase)
        phase_merged = sum(row.status == "MERGED" for row in group)
        phase_status = _status(
            group, phase=True, integrated=profile.supports_integration
        )
        counts = f"{len(group)} | {phase_merged}"
        if profile.supports_integration:
            counts += f" | {sum(row.status == 'INTEGRATED' for row in group)}"
        phase_lines.append(f"| {phase} | {goal} | {counts} | `{phase_status}` |")
    total_counts = f"**{total}** | **{merged}**"
    if profile.supports_integration:
        total_counts += f" | **{integrated}**"
    phase_lines.append(f"| **Total** |  | {total_counts} | **{progress}%** |")
    updated = _block(updated, "PHASES", phase_lines, newline)
    return PlanAggregation(
        updated, project_status, total, done, merged, integrated, approved, progress
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PLAN tracker 검증과 파생 집계 갱신")
    parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    parser.add_argument("--plan-path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    path = args.plan_path or root / "docs" / "planning" / args.profile / "PLAN.md"
    try:
        original = path.read_bytes().decode("utf-8-sig")
        result = aggregate_plan(
            original,
            PROFILES[args.profile],
            timestamp=None if args.check else datetime.now(KST),
        )
        if result.status == PlanStatus.INVALID or result.aggregation is None:
            print(
                f"PLAN invalid — path={path} profile={args.profile}: {result.detail}",
                file=sys.stderr,
            )
            return 1
        aggregation = result.aggregation
        if args.check and aggregation.text != original:
            print(
                f"PLAN aggregates stale — path={path}; rerun without --check",
                file=sys.stderr,
            )
            return 1
        if not args.check and aggregation.text != original:
            path.write_bytes(aggregation.text.encode("utf-8"))
        if PROFILES[args.profile].supports_integration:
            counts = (
                f"{aggregation.done_count}/{aggregation.total_count} done "
                f"({aggregation.merged_count} merged to main, {aggregation.integrated_count} integrated)"
            )
        else:
            counts = f"{aggregation.merged_count}/{aggregation.total_count} merged"
        if args.check:
            print(
                f"PLAN.md is consistent: {counts}, {aggregation.approved_count} approved."
            )
        else:
            print(
                f"Updated PLAN.md: {counts}, {aggregation.approved_count} approved, "
                f"status={aggregation.project_status}."
            )
        return 0
    except (OSError, UnicodeError) as error:
        print(
            f"PLAN I/O failure — path={path} profile={args.profile}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
