from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from quant_study_dev.plan_progress import (
    KST,
    PROFILES,
    PlanProfile,
    PlanResult,
    PlanStatus,
    aggregate_plan,
)

NOW = datetime(2026, 10, 16, 12, 34, 56, tzinfo=KST)
YAML_PROFILE = PROFILES["strategy-workbench-yaml-ui"]


def _fixture(
    parallel_ids: tuple[str, ...] = ("P6-01", "P6-02"),
    *,
    profile: PlanProfile = YAML_PROFILE,
) -> str:
    final_phase = profile.phases[-1][0]
    merged_rows = "\n".join(
        f"| [x] | `{phase}-01` | 완료 | 없음 | `MERGED` | — |"
        for phase, _ in profile.phases[:-1]
    )
    integrated = "integrated_prs: 0\n" if profile.supports_integration else ""
    return f"""---
plan_version: 2
project_status: APPROVED
current_phase: {final_phase}
current_pr: {final_phase}-01,{final_phase}-02
active_prs: [{final_phase}-01, {final_phase}-02]
parallel_window: [{", ".join(parallel_ids)}]
last_updated: 2026-09-06T00:00:00+09:00
planned_prs: 9
merged_prs: 7
{integrated}approved_prs: 8
progress_percent: 78
---

# 사람이 소유한 제목과 문맥

변경하면 안 되는 설명 문장입니다.

<!-- PLAN:SUMMARY:START -->
stale
<!-- PLAN:SUMMARY:END -->

<!-- PLAN:PHASES:START -->
stale
<!-- PLAN:PHASES:END -->

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
{merged_rows}
| [ ] | `{final_phase}-01` | 수정 필요 | 없음 | `CHANGES_REQUESTED` | — |
| [ ] | `{final_phase}-02` | 검토 완료 | 없음 | `APPROVED` | — |

사람이 소유한 끝 문장입니다.
"""


class PlanProgressAggregationTest(unittest.TestCase):
    def test_blocking_status_is_independent_of_parallel_window_order(self) -> None:
        for parallel_ids in (("P6-01", "P6-02"), ("P6-02", "P6-01")):
            with self.subTest(parallel_ids=parallel_ids):
                result = aggregate_plan(
                    _fixture(parallel_ids), YAML_PROFILE, timestamp=NOW
                )
                self.assertEqual(result.status, PlanStatus.VALID, result.detail)
                self.assertIsNotNone(result.aggregation)
                aggregation = result.aggregation
                if aggregation is None:
                    self.fail("valid fixture aggregation missing")
                self.assertEqual(aggregation.project_status, "CHANGES_REQUESTED")
                self.assertEqual(aggregation.merged_count, 7)
                self.assertEqual(aggregation.approved_count, 8)
                self.assertEqual(aggregation.progress_percent, 78)
                self.assertIn(f"current_pr: {','.join(parallel_ids)}", aggregation.text)
                self.assertRegex(
                    aggregation.text,
                    re.compile(
                        r"^\| P6 \| Professional release and migration \| 2 \| 0 "
                        r"\| `CHANGES_REQUESTED` \|$",
                        re.MULTILINE,
                    ),
                )

    def test_all_five_profiles_keep_their_phase_and_integration_contracts(self) -> None:
        for name, profile in PROFILES.items():
            final = profile.phases[-1][0]
            with self.subTest(profile=name):
                result = aggregate_plan(
                    _fixture((f"{final}-01", f"{final}-02"), profile=profile),
                    profile,
                    timestamp=NOW,
                )
                self.assertEqual(result.status, PlanStatus.VALID, result.detail)
                aggregation = result.aggregation
                if aggregation is None:
                    self.fail("valid fixture aggregation missing")
                self.assertEqual(aggregation.total_count, len(profile.phases) + 1)
                self.assertEqual(aggregation.merged_count, len(profile.phases) - 1)
                self.assertEqual(aggregation.approved_count, len(profile.phases))
                for phase, goal in profile.phases:
                    self.assertIn(f"| {phase} | {goal} |", aggregation.text)
                self.assertEqual(
                    "| Main | Integrated |" in aggregation.text,
                    profile.supports_integration,
                )

    def test_render_preserves_unowned_text_and_both_line_endings(self) -> None:
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                original = _fixture().replace("\n", newline)
                result = aggregate_plan(original, YAML_PROFILE, timestamp=NOW)
                aggregation = result.aggregation
                if aggregation is None:
                    self.fail(result.detail)
                for marker in (
                    "# 사람이 소유한 제목과 문맥",
                    "변경하면 안 되는 설명 문장입니다.",
                    "| 완료 | PR | 결과물 | Dependency | 상태 | Review |",
                    "사람이 소유한 끝 문장입니다.",
                ):
                    self.assertIn(marker + newline, aggregation.text)
                tracker = original.split("| 완료 | PR |", 1)[1]
                self.assertEqual(aggregation.text.split("| 완료 | PR |", 1)[1], tracker)
                self.assertEqual(
                    aggregation.text.count("\n"), aggregation.text.count(newline)
                )
                self.assertIn(
                    "last_updated: 2026-10-16T12:34:56+09:00", aggregation.text
                )
                self.assertIn("`2026-10-16 12:34 KST`", aggregation.text)
                again = aggregate_plan(aggregation.text, YAML_PROFILE)
                self.assertIsNotNone(again.aggregation)
                self.assertEqual(again.aggregation, aggregation)

    def test_unknown_status_checkbox_dependency_and_parallel_errors_are_values(
        self,
    ) -> None:
        original = _fixture()
        mutations = {
            "unknown PR status": original.replace("`CHANGES_REQUESTED`", "`BOGUS`"),
            "checkbox/status mismatch": original.replace(
                "[ ] | `P6-01`", "[x] | `P6-01`"
            ),
            "unknown dependency": original.replace(
                "수정 필요 | 없음", "수정 필요 | P9-99"
            ),
            "READY dependency not done": original.replace(
                "`CHANGES_REQUESTED`", "`READY`"
            ).replace("수정 필요 | 없음", "수정 필요 | P6-02"),
            "parallel_window": original.replace(
                "parallel_window: [P6-01, P6-02]", "parallel_window: []"
            ),
            "duplicate PR ids": original
            + "\n| [ ] | `P6-01` | 복제 | 없음 | `WAITING` | — |\n",
            "unknown phase": original
            + "\n| [ ] | `P9-01` | 추가 | 없음 | `WAITING` | — |\n",
            "phase has no tracker rows": original.replace(
                "| [x] | `P0-01` | 완료 | 없음 | `MERGED` | — |\n", ""
            ),
            "frontmatter field missing": original.replace("planned_prs: 9\n", ""),
            "generated block missing": original.replace(
                "<!-- PLAN:SUMMARY:START -->", ""
            ),
        }
        for detail, text in mutations.items():
            with self.subTest(detail=detail):
                result = aggregate_plan(text, YAML_PROFILE)
                self.assertEqual(result.status, PlanStatus.INVALID)
                self.assertIsNone(result.aggregation)
                self.assertIn(detail, result.detail)

    def test_integration_is_done_and_ready_dependency_only_for_lang2(self) -> None:
        profile = PROFILES["strategy-language-2-0"]
        original = _fixture(("P6-02",), profile=profile).replace(
            "`MERGED`", "`INTEGRATED`"
        )
        original = original.replace("`CHANGES_REQUESTED`", "`READY`").replace(
            "수정 필요 | 없음", "수정 필요 | P5-01"
        )
        result = aggregate_plan(original, profile)
        aggregation = result.aggregation
        if aggregation is None:
            self.fail(result.detail)
        self.assertEqual(aggregation.merged_count, 0)
        self.assertEqual(aggregation.integrated_count, 6)
        self.assertEqual(aggregation.done_count, 6)
        self.assertIn(
            "| P5 | Graph level 2: recipe | 1 | 0 | 1 | `INTEGRATED` |",
            aggregation.text,
        )
        wrong_profile = aggregate_plan(
            _fixture().replace("`MERGED`", "`INTEGRATED`"), YAML_PROFILE
        )
        self.assertEqual(wrong_profile.status, PlanStatus.INVALID)
        self.assertIn("unknown PR status", wrong_profile.detail)

    def test_complete_ready_paused_waiting_and_bankers_rounding(self) -> None:
        profile = PlanProfile((("P0", "fixture"),), YAML_PROFILE.pr_id_pattern)
        for status in ("MERGED", "READY", "PAUSED", "WAITING"):
            with self.subTest(status=status):
                text = _fixture(("P0-01", "P0-02"), profile=profile)
                text = text.replace("`CHANGES_REQUESTED`", f"`{status}`").replace(
                    "`APPROVED`", f"`{status}`"
                )
                if status == "MERGED":
                    text = text.replace("[ ]", "[x]")
                result = aggregate_plan(text, profile)
                aggregation = result.aggregation
                if aggregation is None:
                    self.fail(result.detail)
                self.assertEqual(
                    aggregation.project_status,
                    "COMPLETE" if status == "MERGED" else status,
                )
                if status == "MERGED":
                    self.assertIn("current_pr: none", aggregation.text)
        rows = "\n".join(
            f"| [{'x' if i < 1 else ' '}] | `P0-{i + 1:02}` | fixture | 없음 | "
            f"`{'MERGED' if i < 1 else 'WAITING'}` | — |"
            for i in range(8)
        )
        text = _fixture((), profile=profile).split("| 완료 | PR |", 1)[0] + rows + "\n"
        result = aggregate_plan(text, profile)
        self.assertIsNotNone(result.aggregation)
        if result.aggregation is not None:
            self.assertEqual(result.aggregation.progress_percent, 12)

    def test_three_active_pr_stack_must_be_fully_declared_and_preserves_severity(
        self,
    ) -> None:
        text = _fixture(("P6-03", "P6-02", "P6-01"))
        text += "\n| [ ] | `P6-03` | 작업 중 | 없음 | `IN_PROGRESS` | — |\n"
        result = aggregate_plan(text, YAML_PROFILE)
        if result.aggregation is None:
            self.fail(result.detail)
        self.assertEqual(result.aggregation.project_status, "CHANGES_REQUESTED")
        self.assertIn("current_pr: P6-03,P6-02,P6-01", result.aggregation.text)
        invalid = aggregate_plan(
            text.replace("P6-03, P6-02, P6-01", "P6-01, P6-02"), YAML_PROFILE
        )
        self.assertEqual(invalid.status, PlanStatus.INVALID)
        self.assertIn("active stack not declared", invalid.detail)

    def test_all_integrated_is_not_main_complete(self) -> None:
        profile = PlanProfile((("P0", "fixture"),), YAML_PROFILE.pr_id_pattern, True)
        text = _fixture((), profile=profile)
        text = text.replace("`CHANGES_REQUESTED`", "`INTEGRATED`")
        text = text.replace("`APPROVED`", "`INTEGRATED`").replace("[ ]", "[x]")
        result = aggregate_plan(text, profile)
        if result.aggregation is None:
            self.fail(result.detail)
        self.assertEqual(result.aggregation.project_status, "WAITING")
        self.assertEqual(result.aggregation.merged_count, 0)
        self.assertEqual(result.aggregation.integrated_count, 2)
        self.assertEqual(result.aggregation.progress_percent, 100)
        self.assertIn(
            "| P0 | fixture | 2 | 0 | 2 | `INTEGRATED` |", result.aggregation.text
        )
        self.assertIn("current_pr: none", result.aggregation.text)

    def test_result_cannot_mix_missing_success_or_successful_failure_payloads(
        self,
    ) -> None:
        result = aggregate_plan(_fixture(), YAML_PROFILE)
        value = result.aggregation
        if value is None:
            self.fail(result.detail)
        with self.assertRaisesRegex(ValueError, "invalid PlanResult combination"):
            PlanResult(PlanStatus.VALID)
        with self.assertRaisesRegex(ValueError, "invalid PlanResult combination"):
            PlanResult(PlanStatus.VALID, value, "성공에 실패 사유가 있음")
        with self.assertRaisesRegex(ValueError, "invalid PlanResult combination"):
            PlanResult(PlanStatus.INVALID, value, "실패에 집계가 있음")
        with self.assertRaisesRegex(ValueError, "invalid PlanResult combination"):
            PlanResult(PlanStatus.INVALID, detail="   ")
        self.assertEqual(
            PlanResult(PlanStatus.INVALID, detail="fixture 오류").status,
            PlanStatus.INVALID,
        )

    def test_cli_write_check_stale_and_invalid_are_portable_and_do_not_mutate_on_check(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "공백 있는 경로"
            directory.mkdir()
            plan = directory / "PLAN.md"
            plan.write_bytes(
                b"\xef\xbb\xbf" + _fixture().replace("\n", "\r\n").encode("utf-8")
            )
            command = [
                sys.executable,
                "-m",
                "quant_study_dev.plan_progress",
                "--profile",
                "strategy-workbench-yaml-ui",
                "--plan-path",
                str(plan),
            ]
            stale = subprocess.run(
                [*command, "--check"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(stale.returncode, 1)
            self.assertIn("stale", stale.stderr)
            before = plan.read_bytes()
            self.assertTrue(before.startswith(b"\xef\xbb\xbf"))
            written = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=True
            )
            self.assertIn(
                "Updated PLAN.md: 7/9 merged, 8 approved, status=CHANGES_REQUESTED.",
                written.stdout,
            )
            self.assertFalse(plan.read_bytes().startswith(b"\xef\xbb\xbf"))
            generated = plan.read_bytes()
            checked = subprocess.run(
                [*command, "--check"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn(
                "PLAN.md is consistent: 7/9 merged, 8 approved.", checked.stdout
            )
            self.assertEqual(plan.read_bytes(), generated)
            plan.write_bytes(
                generated.replace(
                    b"project_status: CHANGES_REQUESTED", b"project_status: READY"
                )
            )
            before = plan.read_bytes()
            self.assertEqual(
                subprocess.run(
                    [*command, "--check"], cwd=root, capture_output=True, check=False
                ).returncode,
                1,
            )
            self.assertEqual(plan.read_bytes(), before)
            plan.write_text("invalid", encoding="utf-8")
            invalid = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False
            )
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("no PR tracker rows", invalid.stderr)
            missing = subprocess.run(
                [*command, "--plan-path", str(directory / "missing.md")],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 1)
            self.assertIn("PLAN I/O failure", missing.stderr)


if __name__ == "__main__":
    unittest.main()
