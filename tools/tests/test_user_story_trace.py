"""유저 스토리 추적성 검사기(`quant_study_dev.user_story_trace`)의 동작 테스트.

테스트마다 임시 디렉터리에 스토리 파일·e2e spec·traceability.md를 직접 만들어 넣는다. 저장소의 실제
스토리 파일은 읽지 않는다(그 검사는 CI의 `user-story-harness` job이 도구를 직접 돌려 한다).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_study_dev.user_story_trace import (
    TRACE_END,
    TRACE_START,
    TraceReport,
    run,
)

SPEC_PATH = "frontend/e2e/stories/dm.sample.spec.ts"


def _story(
    story_id: str,
    *,
    status: str = "구현됨-e2e",
    owners: str = "없음",
    e2e: tuple[str, ...] = (),
    extra: str = "",
) -> str:
    if e2e:
        e2e_block = "- e2e:\n" + "".join(f"  - `{SPEC_PATH}` :: {title}\n" for title in e2e)
    else:
        e2e_block = "- e2e: 없음\n"
    return (
        f"### {story_id} 샘플 스토리\n\n"
        "> 정동민으로서 샘플을 하고 싶다. 그래야 검사할 수 있다.\n\n"
        f"- 상태: `{status}`\n"
        f"- 담당 PR: {owners}\n"
        "- 기능 영역: 샘플\n"
        f"{e2e_block}\n"
        "수용 기준\n\n- Given 샘플, Then 통과한다.\n"
        f"{extra}\n"
    )


def _test_call(title: str, tags: tuple[str, ...]) -> str:
    rendered = ", ".join(f'"{tag}"' for tag in tags)
    return f'test(\n  "{title}",\n  {{ tag: [{rendered}] }},\n  async ({{ page }}) => {{\n    void page;\n  }},\n);\n'


class UserStoryTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "docs/product/user-stories/stories").mkdir(parents=True)
        (self.root / "frontend/e2e/stories").mkdir(parents=True)
        self.write_traceability()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write_stories(self, *stories: str, name: str = "dm", persona: str = "정동민") -> None:
        path = self.root / f"docs/product/user-stories/stories/{name}.md"
        path.write_text(f"# {persona} 유저 스토리\n\n소개.\n\n" + "\n".join(stories), encoding="utf-8")

    def write_spec(self, body: str, path: str = SPEC_PATH) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('import { test } from "@playwright/test";\n\n' + body, encoding="utf-8")

    def write_traceability(self, body: str = "") -> None:
        path = self.root / "docs/product/user-stories/traceability.md"
        path.write_text(f"# 표\n\n{TRACE_START}\n{body}\n{TRACE_END}\n", encoding="utf-8")

    def codes(self, report: TraceReport) -> list[str]:
        return sorted(finding.code for finding in report.findings)

    def test_consistent_harness_passes_after_write(self) -> None:
        title = "US-DM-01 샘플이 보인다"
        self.write_stories(
            _story("US-DM-01", e2e=(title,)),
            _story("US-DM-02", status="예정", owners="P3-02"),
            _story("US-DM-03", status="미계획", extra="제품 결정 필요: 계획이 없다."),
        )
        self.write_spec(_test_call(title, ("@story", "@US-DM-01")))

        written = run(self.root, write=True)
        checked = run(self.root)

        self.assertEqual(self.codes(written), [])
        self.assertTrue(checked.ok, [finding.render() for finding in checked.findings])
        table = (self.root / "docs/product/user-stories/traceability.md").read_text(encoding="utf-8")
        self.assertIn(f"| US-DM-01 | 정동민 | 샘플 스토리 | `구현됨-e2e` | — | `{SPEC_PATH}` :: {title} |", table)
        self.assertIn("| US-DM-02 | 정동민 | 샘플 스토리 | `예정` | P3-02 | — |", table)
        self.assertIn("| 정동민 | 1 | 0 | 1 | 1 | 3 |", table)

    def test_implemented_story_without_tagged_test_fails(self) -> None:
        self.write_stories(_story("US-DM-01", e2e=("US-DM-01 샘플",)))
        self.write_spec(_test_call("US-DM-01 샘플", ("@story",)))

        report = run(self.root, write=True)

        self.assertIn("story.untested", self.codes(report))
        self.assertIn("story.e2e_not_tagged", self.codes(report))

    def test_tag_for_unknown_story_fails(self) -> None:
        self.write_stories(_story("US-DM-01", e2e=("US-DM-01 샘플",)))
        self.write_spec(
            _test_call("US-DM-01 샘플", ("@story", "@US-DM-01"))
            + _test_call("유령 스토리", ("@story", "@US-DM-09"))
        )

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["e2e.unknown_story"])
        self.assertIn("@US-DM-09", report.findings[0].message)

    def test_tagged_test_missing_from_story_list_fails(self) -> None:
        self.write_stories(_story("US-DM-01", e2e=("US-DM-01 샘플",)))
        self.write_spec(
            _test_call("US-DM-01 샘플", ("@story", "@US-DM-01"))
            + _test_call("다른 테스트", ("@story", "@US-DM-01"))
        )

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["story.e2e_unlisted"])

    def test_planned_story_with_tagged_test_must_be_promoted(self) -> None:
        self.write_stories(_story("US-DM-01", status="예정", owners="P3-02"))
        self.write_spec(_test_call("US-DM-01 샘플", ("@story", "@US-DM-01")))

        report = run(self.root, write=True)

        self.assertIn("story.status_mismatch", self.codes(report))

    def test_planned_story_requires_owner_and_unplanned_requires_reason(self) -> None:
        self.write_stories(
            _story("US-DM-01", status="예정"),
            _story("US-DM-02", status="미계획"),
            _story("US-DM-03", status="미계획", owners="P1-03", extra="제품 결정 필요: 사유."),
        )

        report = run(self.root, write=True)

        self.assertEqual(
            self.codes(report),
            ["story.planned_owner", "story.unplanned_owner", "story.unplanned_reason"],
        )

    def test_invalid_status_owner_and_persona_prefix_are_reported(self) -> None:
        self.write_stories(
            _story("US-DM-01", status="완료"),
            _story("US-DM-02", status="예정", owners="다음 분기"),
            _story("US-SM-01", status="미계획", extra="제품 결정 필요: 사유."),
        )

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["story.owner", "story.persona", "story.status"])

    def test_duplicate_story_id_across_files_fails(self) -> None:
        self.write_stories(_story("US-DM-01", status="예정", owners="P3-02"))
        self.write_stories(
            _story("US-DM-01", status="예정", owners="P3-02"),
            name="sm",
            persona="한상목",
        )

        report = run(self.root, write=True)

        self.assertIn("story.duplicate", self.codes(report))

    def test_story_tag_outside_test_call_is_rejected(self) -> None:
        self.write_stories(_story("US-DM-01", e2e=("US-DM-01 샘플",)))
        self.write_spec(
            'test.describe("묶음", { tag: ["@US-DM-01"] }, () => {\n'
            + _test_call("US-DM-01 샘플", ("@story", "@US-DM-01"))
            + "});\n"
        )

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["e2e.tag_position"])

    def test_story_tag_requires_story_marker(self) -> None:
        self.write_stories(_story("US-DM-01", e2e=("US-DM-01 샘플",)))
        self.write_spec(_test_call("US-DM-01 샘플", ("@US-DM-01",)))

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["e2e.story_tag"])

    def test_inline_tag_on_existing_test_call_is_recognised(self) -> None:
        title = "기존 테스트, 제목은 그대로"
        self.write_stories(_story("US-DM-01", e2e=(title,)))
        self.write_spec(
            f'test("{title}", {{ tag: ["@story", "@US-DM-01"] }}, async ({{\n  page,\n}}) => {{\n  void page;\n}});\n'
        )

        report = run(self.root, write=True)

        self.assertTrue(report.ok, [finding.render() for finding in report.findings])

    def test_stale_traceability_fails_without_write(self) -> None:
        self.write_stories(_story("US-DM-01", status="예정", owners="P3-02"))
        self.write_traceability("| 손으로 고친 표 |")

        report = run(self.root)

        self.assertEqual(self.codes(report), ["trace.stale"])
        table = (self.root / "docs/product/user-stories/traceability.md").read_text(encoding="utf-8")
        self.assertIn("손으로 고친 표", table)

    def test_missing_markers_fail(self) -> None:
        self.write_stories(_story("US-DM-01", status="예정", owners="P3-02"))
        (self.root / "docs/product/user-stories/traceability.md").write_text("# 표\n", encoding="utf-8")

        report = run(self.root, write=True)

        self.assertEqual(self.codes(report), ["trace.markers"])

    def test_screenshot_directory_named_like_spec_is_ignored(self) -> None:
        self.write_stories(_story("US-DM-01", status="예정", owners="P3-02"))
        (self.root / "frontend/e2e/__screenshots__/sample.spec.ts").mkdir(parents=True)

        report = run(self.root, write=True)

        self.assertTrue(report.ok, [finding.render() for finding in report.findings])


if __name__ == "__main__":
    unittest.main()
