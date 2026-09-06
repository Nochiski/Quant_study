from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PHASES = ("P0", "P1", "P1.5", "P2", "P3", "P4", "P5")


def _fixture(parallel_ids: tuple[str, str]) -> str:
    merged_rows = "\n".join(
        f"| [x] | `{phase}-01` | complete | 없음 | `MERGED` | — |"
        for phase in PHASES
    )
    return f"""---
plan_version: 2
project_status: APPROVED
current_phase: P6
current_pr: P6-01,P6-02
active_prs: [P6-01, P6-02]
parallel_window: [{parallel_ids[0]}, {parallel_ids[1]}]
last_updated: 2026-09-06T00:00:00+09:00
planned_prs: 9
merged_prs: 7
approved_prs: 8
progress_percent: 78
---

<!-- PLAN:SUMMARY:START -->
stale
<!-- PLAN:SUMMARY:END -->

<!-- PLAN:PHASES:START -->
stale
<!-- PLAN:PHASES:END -->

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
{merged_rows}
| [ ] | `P6-01` | needs fixes | 없음 | `CHANGES_REQUESTED` | — |
| [ ] | `P6-02` | reviewed | 없음 | `APPROVED` | — |
"""


class PlanProgressAggregationTest(unittest.TestCase):
    def test_blocking_status_is_independent_of_parallel_window_order(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script = (
            root
            / "docs"
            / "planning"
            / "strategy-workbench-yaml-ui"
            / "tools"
            / "update-plan-progress.ps1"
        )
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(powershell, "PowerShell is required by the PLAN contract")

        for parallel_ids in (("P6-01", "P6-02"), ("P6-02", "P6-01")):
            with self.subTest(parallel_ids=parallel_ids), tempfile.TemporaryDirectory() as temp:
                plan = Path(temp) / "PLAN.md"
                plan.write_text(_fixture(parallel_ids), encoding="utf-8")
                command = [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-PlanPath",
                    str(plan),
                ]
                subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
                content = plan.read_text(encoding="utf-8")
                self.assertRegex(content, r"(?m)^project_status: CHANGES_REQUESTED$")
                self.assertRegex(
                    content,
                    re.compile(
                        r"^\| P6 \| Professional release and migration \| 2 \| 0 "
                        r"\| `CHANGES_REQUESTED` \|$",
                        re.MULTILINE,
                    ),
                )
                subprocess.run(
                    [*command, "-Check"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
