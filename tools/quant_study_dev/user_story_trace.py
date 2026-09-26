"""유저 스토리 하네스 추적성 검사기.

스토리 파일(`docs/product/user-stories/stories/*.md`), Playwright e2e의 스토리 태그
(`frontend/e2e/**/*.spec.ts`), 색인 표(`docs/product/user-stories/traceability.md`)가 서로 맞는지 본다.

소유 관계는 이렇다.

- 스토리 문구·상태·담당 PR은 스토리 파일이 소유한다.
- 어느 테스트가 어느 스토리를 지키는지는 e2e의 `tag` 옵션이 소유한다. 스토리의 `e2e` 목록은
  사람이 읽기 위한 사본이라 태그와 같아야 한다.
- traceability 표는 위 둘에서 만든 파생물이다. 손으로 고치지 않고 `--write`로 다시 쓴다.

형식과 규칙의 사람용 설명은 `docs/product/user-stories/README.md`에 있다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STORIES_DIR = Path("docs/product/user-stories/stories")
TRACEABILITY_PATH = Path("docs/product/user-stories/traceability.md")
E2E_DIR = Path("frontend/e2e")

TRACE_START = "<!-- USER-STORY-TRACE:START -->"
TRACE_END = "<!-- USER-STORY-TRACE:END -->"
STORY_TAG = "@story"
UNPLANNED_MARKER = "제품 결정 필요"
NONE_VALUE = "없음"

STORY_ID = re.compile(r"US-[A-Z]{2}-\d{2}")
STORY_HEADING = re.compile(r"^### (?P<id>\S+) (?P<title>.+)$", re.MULTILINE)
PERSONA_HEADING = re.compile(r"^# (?P<name>\S+) 유저 스토리$", re.MULTILINE)
STATUS_LINE = re.compile(r"^- 상태: `(?P<status>[^`]+)`$", re.MULTILINE)
OWNER_LINE = re.compile(r"^- 담당 PR: (?P<owners>.+)$", re.MULTILINE)
AREA_LINE = re.compile(r"^- 기능 영역: (?P<area>.+)$", re.MULTILINE)
E2E_NONE_LINE = re.compile(r"^- e2e: 없음$", re.MULTILINE)
E2E_LIST_LINE = re.compile(r"^- e2e:$", re.MULTILINE)
E2E_ITEM_LINE = re.compile(r"^  - `(?P<path>[^`]+)` :: (?P<title>.+)$")
E2E_OWNER_LINE = re.compile(r"^- e2e 담당: (?P<owners>.+)$", re.MULTILINE)
ACCEPTANCE_SECTION = re.compile(r"^수용 기준\s*\n\s*\n- \S", re.MULTILINE)
# lang2 WORKFLOW 같은 계획 문서의 PR ID(`P3-02`)나 GitHub 번호(`#123`).
OWNER_ID = re.compile(r"^(?:[A-Z][A-Z0-9]*-\d{2}|#\d+)$")

# `test("제목", { …, tag: [...] }, …)`. 제목은 따옴표 세 종류를 받되 템플릿 치환(`${`)은 거부한다.
# 옵션 객체 안에서 `tag` 앞에 다른 키(`annotation: { … }` 한 겹까지)가 와도 읽는다.
TEST_WITH_TAGS = re.compile(
    r"""\btest\(\s*(?P<quote>["'`])(?P<title>(?:\\.|(?!(?P=quote)).)*?)(?P=quote)\s*,"""
    r"""\s*\{(?:[^{}]|\{[^{}]*\})*?\btag:\s*(?P<tags>\[[^\]]*\]|["'][^"']*["'])""",
    re.DOTALL,
)
TAG_LITERAL = re.compile(r"""["'](?P<tag>@[^"']+)["']""")
STORY_TAG_TOKEN = re.compile(r"@US-[A-Za-z0-9-]+")
# 스토리 태그가 있는 파일에서 테스트를 건너뛰거나(skip·fixme), 실패를 기대하거나(fail), 나머지를
# 끄는(only) 호출. 본문 안 `test.skip()`도 정적으로 잡는다.
NON_RUNNING_CALL = re.compile(r"\btest(?:\.describe)?\.(?P<kind>skip|fixme|fail|only)\(")
# 릴리스 게이트에 들지 않는 opt-in spec(`E2E_REAL_EQUITY_ROOT`가 있을 때만 수집된다).
OPT_IN_SPEC = re.compile(r"\.real-equity\.spec\.ts$")


class StoryStatus(StrEnum):
    """스토리 상태. 값은 스토리 파일에 적는 글자 그대로다."""

    IMPLEMENTED_WITH_E2E = "구현됨-e2e"
    IMPLEMENTED_WITHOUT_E2E = "구현됨-e2e없음"
    PLANNED = "예정"
    UNPLANNED = "미계획"


@dataclass(frozen=True, order=True)
class TestRef:
    """e2e 테스트 하나를 가리키는 값. 경로는 저장소 루트 기준 POSIX 표기다."""

    path: str
    title: str

    def render(self) -> str:
        return f"`{self.path}` :: {self.title}"


@dataclass(frozen=True)
class Finding:
    """검사 실패 하나. `location`은 사람이 열어 볼 파일(과 줄)이다."""

    code: str
    location: str
    message: str

    def render(self) -> str:
        return f"[{self.code}] {self.location}: {self.message}"


@dataclass(frozen=True)
class Story:
    story_id: str
    title: str
    persona: str
    status: StoryStatus | None
    owners: tuple[str, ...]
    e2e_owners: tuple[str, ...]
    area: str
    e2e: tuple[TestRef, ...]
    source: str
    has_unplanned_reason: bool
    has_acceptance: bool


@dataclass(frozen=True)
class TaggedTest:
    ref: TestRef
    tags: tuple[str, ...]

    @property
    def story_ids(self) -> tuple[str, ...]:
        return tuple(tag.removeprefix("@") for tag in self.tags if tag.startswith("@US-"))


@dataclass
class TraceReport:
    """검사 결과. `findings`가 비어 있으면 통과다."""

    stories: list[Story] = field(default_factory=list)
    tests: list[TaggedTest] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_owners(raw: str) -> tuple[str, ...]:
    value = raw.strip()
    if value == NONE_VALUE:
        return ()
    return tuple(part.strip() for part in value.split(","))


def _parse_e2e(block: str) -> tuple[tuple[TestRef, ...], str | None]:
    """스토리 블록의 `e2e` 항목을 읽는다. 형식이 틀리면 두 번째 값에 이유를 담는다."""
    if E2E_NONE_LINE.search(block):
        return (), None
    header = E2E_LIST_LINE.search(block)
    if header is None:
        return (), "`- e2e: 없음` 또는 `- e2e:` 목록이 없다"
    refs: list[TestRef] = []
    for line in block[header.end() :].splitlines()[1:]:
        if not line.startswith("  - "):
            break
        item = E2E_ITEM_LINE.match(line)
        if item is None:
            return tuple(refs), f"e2e 항목 형식이 `  - `경로` :: 제목`이 아니다: {line!r}"
        refs.append(TestRef(item["path"], item["title"].strip()))
    if not refs:
        return (), "`- e2e:` 아래에 항목이 없다. 없으면 `- e2e: 없음`이라고 적는다"
    return tuple(refs), None


def parse_stories(root: Path, report: TraceReport) -> None:
    """스토리 파일을 모두 읽어 `report.stories`에 채우고 형식 오류를 기록한다."""
    directory = root / STORIES_DIR
    files = sorted(directory.glob("*.md")) if directory.is_dir() else []
    if not files:
        report.findings.append(
            Finding("stories.missing", STORIES_DIR.as_posix(), "스토리 파일(*.md)이 하나도 없다")
        )
        return
    seen: dict[str, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        location = _relative(path, root)
        prefix = path.stem.upper()
        persona_match = PERSONA_HEADING.search(text)
        if persona_match is None:
            report.findings.append(
                Finding("stories.persona", location, "첫 제목이 `# <이름> 유저 스토리` 형식이 아니다")
            )
            persona = prefix
        else:
            persona = persona_match["name"]
        headings = list(STORY_HEADING.finditer(text))
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            block = text[heading.start() : end]
            story_location = f"{location}:{_line_of(text, heading.start())}"
            story_id = heading["id"]
            if STORY_ID.fullmatch(story_id) is None:
                report.findings.append(
                    Finding(
                        "story.id",
                        story_location,
                        f"스토리 ID 형식이 `US-<약자>-<두 자리>`가 아니다: got={story_id!r}",
                    )
                )
                continue
            if story_id.split("-")[1] != prefix:
                report.findings.append(
                    Finding(
                        "story.persona",
                        story_location,
                        f"{story_id}는 `{prefix.lower()}.md`에 둘 수 없다: expected prefix=US-{prefix}-",
                    )
                )
            if story_id in seen:
                report.findings.append(
                    Finding("story.duplicate", story_location, f"{story_id}가 {seen[story_id]}에도 있다")
                )
                continue
            seen[story_id] = story_location
            report.stories.append(_parse_story(story_id, heading["title"], persona, block, story_location, report))


def _parse_story(
    story_id: str,
    title: str,
    persona: str,
    block: str,
    location: str,
    report: TraceReport,
) -> Story:
    status_match = STATUS_LINE.search(block)
    status: StoryStatus | None = None
    if status_match is None:
        report.findings.append(Finding("story.status", location, f"{story_id}에 `- 상태: `…`` 줄이 없다"))
    else:
        try:
            status = StoryStatus(status_match["status"])
        except ValueError:
            allowed = ", ".join(item.value for item in StoryStatus)
            report.findings.append(
                Finding(
                    "story.status",
                    location,
                    f"{story_id}의 상태가 허용값이 아니다: got={status_match['status']!r} allowed=[{allowed}]",
                )
            )
    owners = _owner_field(OWNER_LINE, "담당 PR", "story.owner", story_id, block, location, report)
    e2e_owners = _owner_field(
        E2E_OWNER_LINE, "e2e 담당", "story.e2e_owner", story_id, block, location, report
    )
    area_match = AREA_LINE.search(block)
    if area_match is None:
        report.findings.append(Finding("story.area", location, f"{story_id}에 `- 기능 영역:` 줄이 없다"))
    e2e, e2e_error = _parse_e2e(block)
    if e2e_error is not None:
        report.findings.append(Finding("story.e2e", location, f"{story_id}: {e2e_error}"))
    return Story(
        story_id=story_id,
        title=title.strip(),
        persona=persona,
        status=status,
        owners=owners,
        e2e_owners=e2e_owners,
        area=area_match["area"].strip() if area_match else "",
        e2e=e2e,
        source=location,
        has_unplanned_reason=UNPLANNED_MARKER in block,
        has_acceptance=ACCEPTANCE_SECTION.search(block) is not None,
    )


def _owner_field(
    pattern: re.Pattern[str],
    label: str,
    code: str,
    story_id: str,
    block: str,
    location: str,
    report: TraceReport,
) -> tuple[str, ...]:
    """`- 담당 PR:`·`- e2e 담당:` 같은 PR ID 목록 줄을 읽는다. 줄이 없거나 ID 형식이 틀리면 기록한다."""
    match = pattern.search(block)
    if match is None:
        report.findings.append(Finding(code, location, f"{story_id}에 `- {label}:` 줄이 없다"))
        return ()
    owners = _parse_owners(match["owners"])
    invalid = [owner for owner in owners if OWNER_ID.fullmatch(owner) is None]
    if invalid:
        report.findings.append(
            Finding(
                code,
                location,
                f"{story_id}의 {label} ID 형식이 틀렸다: got={invalid} expected=`P3-02` 또는 `#123`",
            )
        )
    return owners


def _spec_files(root: Path) -> Iterable[Path]:
    directory = root / E2E_DIR
    if not directory.is_dir():
        return ()
    # 스크린샷 기준선 폴더도 `<spec 이름>.spec.ts`라는 디렉터리라 파일만 고른다.
    return sorted(
        path
        for path in directory.rglob("*.spec.ts")
        if path.is_file() and "node_modules" not in path.parts
    )


def scan_tests(root: Path, report: TraceReport) -> None:
    """e2e spec에서 `tag` 옵션이 붙은 테스트를 읽어 `report.tests`에 채운다."""
    for path in _spec_files(root):
        text = path.read_text(encoding="utf-8")
        location = _relative(path, root)
        parsed_story_tags = 0
        titles: dict[str, int] = {}
        for match in TEST_WITH_TAGS.finditer(text):
            title = match["title"]
            line = _line_of(text, match.start())
            tags = tuple(tag["tag"] for tag in TAG_LITERAL.finditer(match["tags"]))
            story_tags = [tag for tag in tags if tag.startswith("@US-")]
            parsed_story_tags += len(story_tags)
            if not story_tags:
                continue
            if "${" in title:
                report.findings.append(
                    Finding("e2e.title", f"{location}:{line}", "스토리 태그가 붙은 테스트 제목에 템플릿 치환이 있다")
                )
                continue
            if title in titles:
                report.findings.append(
                    Finding(
                        "e2e.duplicate_title",
                        f"{location}:{line}",
                        f"같은 파일에 같은 제목의 스토리 테스트가 {titles[title]}줄에도 있다: {title!r}",
                    )
                )
            titles[title] = line
            if STORY_TAG not in tags:
                report.findings.append(
                    Finding(
                        "e2e.story_tag",
                        f"{location}:{line}",
                        f"스토리 태그 {story_tags}가 있는데 `{STORY_TAG}` 태그가 없다",
                    )
                )
            report.tests.append(TaggedTest(TestRef(location, title), tags))
        total_story_tags = len(STORY_TAG_TOKEN.findall(text))
        if total_story_tags != parsed_story_tags:
            report.findings.append(
                Finding(
                    "e2e.tag_position",
                    location,
                    "스토리 태그를 `test(제목, { tag: [...] }, …)` 밖에서 찾았다"
                    f" (파일 전체 {total_story_tags}개, test tag 옵션 안 {parsed_story_tags}개)."
                    " `test.describe`나 주석이 아니라 테스트 호출의 tag 옵션에만 둔다",
                )
            )
        if total_story_tags == 0:
            continue
        if OPT_IN_SPEC.search(path.name):
            report.findings.append(
                Finding(
                    "e2e.opt_in_spec",
                    location,
                    "opt-in spec은 릴리스 게이트에서 돌지 않으므로 스토리 태그를 달 수 없다",
                )
            )
        for call in NON_RUNNING_CALL.finditer(text):
            report.findings.append(
                Finding(
                    "e2e.non_running",
                    f"{location}:{_line_of(text, call.start())}",
                    f"스토리 태그가 있는 파일에서 `{call.group(0)}…)`를 쓴다."
                    " 건너뛰거나 실패를 기대하는 테스트로는 스토리를 지킬 수 없다",
                )
            )


def check_links(report: TraceReport) -> None:
    """스토리 상태·e2e 목록과 실제 태그를 맞춰 본다."""
    known = {story.story_id for story in report.stories}
    tagged: dict[str, set[TestRef]] = {}
    for test in report.tests:
        for story_id in test.story_ids:
            if story_id not in known:
                report.findings.append(
                    Finding(
                        "e2e.unknown_story",
                        test.ref.path,
                        f"{test.ref.title!r}의 태그 @{story_id}에 해당하는 스토리가 없다",
                    )
                )
                continue
            tagged.setdefault(story_id, set()).add(test.ref)
    for story in report.stories:
        actual = tagged.get(story.story_id, set())
        listed = set(story.e2e)
        status = story.status
        if status is StoryStatus.IMPLEMENTED_WITH_E2E and not actual:
            report.findings.append(
                Finding(
                    "story.untested",
                    story.source,
                    f"{story.story_id}는 `{status.value}`인데 @{story.story_id} 태그가 붙은 e2e가 없다",
                )
            )
        if status is not None and status is not StoryStatus.IMPLEMENTED_WITH_E2E and actual:
            report.findings.append(
                Finding(
                    "story.status_mismatch",
                    story.source,
                    f"{story.story_id}는 `{status.value}`인데 태그가 붙은 e2e가 {len(actual)}개 있다."
                    f" 상태를 `{StoryStatus.IMPLEMENTED_WITH_E2E.value}`로 올린다",
                )
            )
        for missing in sorted(listed - actual):
            report.findings.append(
                Finding(
                    "story.e2e_not_tagged",
                    story.source,
                    f"{story.story_id}가 적은 e2e에 @{story.story_id} 태그가 없다: {missing.render()}",
                )
            )
        for extra in sorted(actual - listed):
            report.findings.append(
                Finding(
                    "story.e2e_unlisted",
                    story.source,
                    f"@{story.story_id} 태그가 붙은 e2e가 스토리 e2e 목록에 없다: {extra.render()}",
                )
            )
        if status is StoryStatus.PLANNED and not story.owners:
            report.findings.append(
                Finding("story.planned_owner", story.source, f"{story.story_id}는 `예정`인데 담당 PR이 없다")
            )
        if status is StoryStatus.PLANNED and not story.e2e_owners:
            report.findings.append(
                Finding(
                    "story.planned_e2e_owner",
                    story.source,
                    f"{story.story_id}는 `예정`인데 e2e를 쓸 PR(`- e2e 담당:`)이 없다",
                )
            )
        stray_e2e_owners = [owner for owner in story.e2e_owners if owner not in story.owners]
        if status is StoryStatus.PLANNED and stray_e2e_owners:
            report.findings.append(
                Finding(
                    "story.e2e_owner_not_listed",
                    story.source,
                    f"{story.story_id}의 e2e 담당 {stray_e2e_owners}가 담당 PR {list(story.owners)}에 없다",
                )
            )
        if (
            status is StoryStatus.IMPLEMENTED_WITH_E2E or status is StoryStatus.UNPLANNED
        ) and story.e2e_owners:
            report.findings.append(
                Finding(
                    "story.e2e_owner_status",
                    story.source,
                    f"{story.story_id}는 `{status.value}`라 e2e 담당을 `없음`으로 둔다:"
                    f" got={list(story.e2e_owners)}",
                )
            )
        if status is StoryStatus.IMPLEMENTED_WITH_E2E and story.owners:
            report.findings.append(
                Finding(
                    "story.implemented_owner",
                    story.source,
                    f"{story.story_id}는 `{status.value}`인데 담당 PR {list(story.owners)}이 남아 있다."
                    " 구현이 끝났으면 `없음`으로 바꾼다",
                )
            )
        if not story.has_acceptance:
            report.findings.append(
                Finding(
                    "story.acceptance",
                    story.source,
                    f"{story.story_id}에 `수용 기준` 절과 그 아래 항목(`- …`)이 없다",
                )
            )
        if status is StoryStatus.UNPLANNED:
            if story.owners:
                report.findings.append(
                    Finding(
                        "story.unplanned_owner",
                        story.source,
                        f"{story.story_id}는 `미계획`인데 담당 PR {list(story.owners)}이 있다. `예정`으로 바꾼다",
                    )
                )
            if not story.has_unplanned_reason:
                report.findings.append(
                    Finding(
                        "story.unplanned_reason",
                        story.source,
                        f"{story.story_id}는 `미계획`인데 본문에 '{UNPLANNED_MARKER}'와 사유가 없다",
                    )
                )


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_traceability(report: TraceReport) -> str:
    """traceability.md 마커 사이에 들어갈 표를 만든다. 같은 입력이면 같은 글자를 낸다."""
    stories = sorted(report.stories, key=lambda story: story.story_id)
    personas: list[str] = []
    for story in stories:
        if story.persona not in personas:
            personas.append(story.persona)
    statuses = list(StoryStatus)
    lines = [
        "### 상태 요약",
        "",
        "| 페르소나 | " + " | ".join(f"`{status.value}`" for status in statuses) + " | 합계 |",
        "|---|" + "---:|" * (len(statuses) + 1),
    ]
    for persona in [*personas, "합계"]:
        members = [story for story in stories if persona in ("합계", story.persona)]
        counts = [sum(1 for story in members if story.status is status) for status in statuses]
        lines.append(f"| {persona} | " + " | ".join(str(count) for count in counts) + f" | {len(members)} |")
    lines += [
        "",
        "### 스토리별 추적",
        "",
        "| 스토리 | 페르소나 | 제목 | 상태 | 담당 PR | e2e 담당 | e2e (파일 :: 테스트) |",
        "|---|---|---|---|---|---|---|",
    ]
    tagged: dict[str, list[TestRef]] = {}
    for test in report.tests:
        for story_id in test.story_ids:
            tagged.setdefault(story_id, []).append(test.ref)
    for story in stories:
        refs = sorted(set(tagged.get(story.story_id, [])))
        e2e = "<br>".join(_cell(ref.render()) for ref in refs) if refs else "—"
        status = f"`{story.status.value}`" if story.status is not None else "?"
        owners = ", ".join(story.owners) if story.owners else "—"
        e2e_owners = ", ".join(story.e2e_owners) if story.e2e_owners else "—"
        lines.append(
            f"| {story.story_id} | {story.persona} | {_cell(story.title)} | {status} | {owners}"
            f" | {e2e_owners} | {e2e} |"
        )
    return "\n".join(lines)


def _replace_block(text: str, body: str) -> str | None:
    start = text.find(TRACE_START)
    end = text.find(TRACE_END)
    if start < 0 or end < start:
        return None
    return f"{text[: start + len(TRACE_START)]}\n{body}\n{text[end:]}"


def check_traceability(root: Path, report: TraceReport, *, write: bool) -> None:
    """traceability.md 표가 스토리와 태그에서 만든 표와 같은지 본다. `write`면 먼저 다시 쓴다."""
    path = root / TRACEABILITY_PATH
    location = TRACEABILITY_PATH.as_posix()
    if not path.is_file():
        report.findings.append(Finding("trace.missing", location, "traceability.md가 없다"))
        return
    text = path.read_text(encoding="utf-8")
    expected = _replace_block(text, render_traceability(report))
    if expected is None:
        report.findings.append(
            Finding("trace.markers", location, f"`{TRACE_START}`와 `{TRACE_END}` 마커 쌍이 없다")
        )
        return
    if write and expected != text:
        path.write_text(expected, encoding="utf-8", newline="\n")
        text = expected
    if expected != text:
        report.findings.append(
            Finding(
                "trace.stale",
                location,
                "표가 스토리 파일·e2e 태그와 다르다."
                " `uv run python -m quant_study_dev.user_story_trace --write`로 다시 쓴다",
            )
        )


@dataclass(frozen=True)
class ListedTest:
    """`playwright test --list --reporter=json`이 알려 준 테스트 한 벌(project 하나)."""

    ref: TestRef
    story_ids: tuple[str, ...]
    project: str
    expected_status: str


def _text(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def _listed_tests(payload: object) -> list[ListedTest]:
    """Playwright JSON reporter 출력의 suite 트리를 평평하게 편다. 파일 경로는 `frontend/e2e/` 기준이다."""
    listed: list[ListedTest] = []
    pending: list[object] = [payload]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        suites = node.get("suites")
        if isinstance(suites, list):
            pending.extend(suites)
        specs = node.get("specs")
        if not isinstance(specs, list):
            continue
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            file = _text(spec, "file")
            title = _text(spec, "title")
            tests = spec.get("tests")
            if file is None or title is None or not isinstance(tests, list):
                continue
            tags = spec.get("tags")
            story_ids = tuple(
                tag.removeprefix("@")
                for tag in (tags if isinstance(tags, list) else [])
                if isinstance(tag, str) and tag.removeprefix("@").startswith("US-")
            )
            ref = TestRef(f"{E2E_DIR.as_posix()}/{file.replace(chr(92), '/')}", title)
            for test in tests:
                if isinstance(test, dict):
                    listed.append(
                        ListedTest(
                            ref,
                            story_ids,
                            _text(test, "projectName") or "",
                            _text(test, "expectedStatus") or "",
                        )
                    )
    return listed


def check_playwright_list(report: TraceReport, list_path: Path) -> None:
    """스토리 태그가 붙은 테스트가 릴리스 게이트 실행에서 실제로 수집되는지 본다.

    입력은 `E2E_REAL_EQUITY_ROOT` 없이 러너로 만든 목록이다(`node e2e/run-playwright.mjs --list
    --reporter=json`). 정적 검사는 어느 project가 어느 파일을 모으는지 모르므로, 모으지 않는 위치에
    둔 spec이나 `test.skip(제목, …)` 같은 선언형 건너뛰기는 이 목록이 있어야 잡힌다.
    """
    location = list_path.as_posix()
    try:
        payload: object = json.loads(list_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        report.findings.append(Finding("list.read", location, f"Playwright 목록을 읽지 못했다: {error!r}"))
        return
    listed = _listed_tests(payload)
    if not listed:
        report.findings.append(Finding("list.empty", location, "Playwright 목록에 테스트가 하나도 없다"))
        return
    if any(item.project == "real-equity" for item in listed):
        report.findings.append(
            Finding(
                "list.opt_in",
                location,
                "목록에 opt-in project `real-equity`가 있다. `E2E_REAL_EQUITY_ROOT` 없이 다시 만든다",
            )
        )
    by_ref: dict[TestRef, list[ListedTest]] = {}
    for item in listed:
        by_ref.setdefault(item.ref, []).append(item)
    static = {test.ref: test for test in report.tests}
    for ref, test in sorted(static.items()):
        runs = by_ref.get(ref, [])
        running = [item for item in runs if item.expected_status == "passed"]
        if not running:
            report.findings.append(
                Finding(
                    "e2e.not_in_gate",
                    ref.path,
                    f"스토리 테스트 {ref.title!r}가 릴리스 게이트 목록에서 실행 대상이 아니다"
                    f" (수집된 project {[item.project for item in runs]},"
                    f" expectedStatus {[item.expected_status for item in runs]})",
                )
            )
            continue
        listed_ids = set(running[0].story_ids)
        if listed_ids != set(test.story_ids):
            report.findings.append(
                Finding(
                    "e2e.list_mismatch",
                    ref.path,
                    f"{ref.title!r}의 스토리 태그가 정적 해석 {sorted(test.story_ids)}와"
                    f" Playwright 목록 {sorted(listed_ids)}에서 다르다",
                )
            )
    for ref, runs in sorted(by_ref.items()):
        if ref not in static and any(item.story_ids for item in runs):
            report.findings.append(
                Finding(
                    "e2e.list_unparsed",
                    ref.path,
                    f"Playwright는 {ref.title!r}에 스토리 태그가 있다는데 정적 검사가 읽지 못했다",
                )
            )


def run(root: Path, *, write: bool = False, playwright_list: Path | None = None) -> TraceReport:
    """저장소 `root`를 검사한 보고서를 돌려준다.

    `write`면 traceability 표를 먼저 갱신한다. `playwright_list`가 있으면 스토리 테스트가 릴리스
    게이트에서 실제로 도는지도 본다.
    """
    report = TraceReport()
    parse_stories(root, report)
    scan_tests(root, report)
    check_links(report)
    check_traceability(root, report, write=write)
    if playwright_list is not None:
        check_playwright_list(report, playwright_list)
    return report


def _use_utf8_output() -> None:
    """표준 출력·오류를 UTF-8로 다시 연다.

    Windows CI 러너는 파이프 인코딩이 cp1252라 한글 결과 문구를 쓰는 순간 `UnicodeEncodeError`로
    죽는다(#195 2차 리뷰 P1-3). 그러면 검사 결과와 무관하게 step이 실패하고 뒤따르는 e2e가 돌지
    않는다. `reconfigure`가 없는 대체 스트림(테스트 캡처 등)은 그대로 둔다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_output()
    parser = argparse.ArgumentParser(description="유저 스토리 하네스 추적성 검사")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="저장소 루트 (기본: 이 파일 기준)")
    parser.add_argument("--write", action="store_true", help="traceability.md 표를 다시 쓴 뒤 검사한다")
    parser.add_argument(
        "--playwright-list",
        type=Path,
        default=None,
        help="`run-playwright.mjs --list --reporter=json` 결과 파일. 주면 게이트 수집 여부도 본다",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    report = run(root, write=args.write, playwright_list=args.playwright_list)
    for finding in report.findings:
        print(finding.render(), file=sys.stderr)
    stories = len(report.stories)
    tests = len(report.tests)
    if report.ok:
        print(f"유저 스토리 하네스 통과: 스토리 {stories}개, 스토리 태그가 붙은 e2e {tests}개")
        return 0
    print(
        f"유저 스토리 하네스 실패: 문제 {len(report.findings)}건 (스토리 {stories}개, e2e {tests}개)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
