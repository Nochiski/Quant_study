"""충돌 표식 검출 게이트 테스트.

표식이 있는 파일을 실제로 만들어 검출되는지, 표식을 닮은 정상 문서(Markdown setext 밑줄,
구분선)를 오검출하지 않는지 본다. 이 파일 자신이 검출되지 않도록 표식 문자열은 모두
런타임에 조립한다. 게이트는 git 이 추적하는 파일만 보므로 파일 단위 테스트는 임시 git 저장소의
index 에 파일을 올려 쓴다(커밋은 필요 없다).
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from quant_study_dev.conflict_markers import (
    find_conflict_markers,
    main,
    scan_text,
)

OPEN_MARKER = "<" * 7
SPLIT_MARKER = "=" * 7
CLOSE_MARKER = ">" * 7
CONFLICT = (
    f"| 행 |\n{OPEN_MARKER} HEAD\n| A |\n{SPLIT_MARKER}\n| B |\n{CLOSE_MARKER} x\n"
)


def make_repo(
    root: Path, tracked: dict[str, bytes], untracked: dict[str, bytes]
) -> None:
    """`root` 를 git 저장소로 만들고 `tracked` 만 index 에 올린다.

    전역 ignore 설정이 `build/`·`*.lock` 등을 가려도 올라가게 `-f` 로 더한다.
    """
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for name, data in {**tracked, **untracked}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    subprocess.run(
        ["git", "add", "-f", "--", *tracked], cwd=root, check=True, capture_output=True
    )


def reported(root: Path) -> dict[str, list[int]]:
    """게이트가 찾은 표식 줄 번호를 파일별로."""
    found: dict[str, list[int]] = {}
    for path, number, _ in find_conflict_markers(root):
        found.setdefault(path.relative_to(root).as_posix(), []).append(number)
    return found


class ScanTextTests(unittest.TestCase):
    def test_finds_all_three_marker_kinds_with_line_numbers(self) -> None:
        text = "\n".join(
            [
                "| 행 | 값 |",
                f"{OPEN_MARKER} HEAD",
                "| 실행 설정 | A |",
                SPLIT_MARKER,
                "| 실행 설정 | B |",
                f"{CLOSE_MARKER} 86ce099c (제목)",
                "| 다음 행 | 값 |",
            ]
        )
        self.assertEqual([number for number, _ in scan_text(text)], [2, 4, 6])

    def test_ignores_markers_that_are_not_at_the_start_of_a_line(self) -> None:
        text = f'  content = "{OPEN_MARKER} HEAD"\nprint("{SPLIT_MARKER}")\n'
        self.assertEqual(scan_text(text), [])

    def test_ignores_separators_and_setext_underlines_of_other_lengths(self) -> None:
        # 표식은 정확히 7자다. 더 길거나 짧은 줄은 문서의 밑줄·구분선이다.
        text = "\n".join(["제목", "=" * 4, "본문", "=" * 12, "-" * 7])
        self.assertEqual(scan_text(text), [])

    def test_flags_a_bare_seven_character_split_marker(self) -> None:
        self.assertEqual(
            [number for number, _ in scan_text(f"a\n{SPLIT_MARKER}\nb")], [2]
        )


class FindConflictMarkersTests(unittest.TestCase):
    def test_finds_markers_in_every_tracked_text_file_whatever_its_encoding_or_folder(
        self,
    ) -> None:
        # BACKLOG-008 의 놓침 5건 — cp949·UTF-16(BOM) 텍스트, `.lock`, 소스 트리 안 `build/`·`dist/`
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            make_repo(
                root,
                tracked={
                    "rules/sot.md": CONFLICT.encode(),
                    "notes/cp949.txt": ("제목\n" + CONFLICT).encode("cp949"),
                    "notes/utf16.txt": ("제목\n" + CONFLICT).encode("utf-16"),
                    "reference/uv.lock": CONFLICT.encode(),
                    "src/build/out.js": CONFLICT.encode(),
                    "src/dist/app.js": CONFLICT.encode(),
                },
                untracked={},
            )

            self.assertEqual(
                reported(root),
                {
                    "rules/sot.md": [2, 4, 6],
                    "notes/cp949.txt": [3, 5, 7],
                    "notes/utf16.txt": [3, 5, 7],
                    "reference/uv.lock": [2, 4, 6],
                    "src/build/out.js": [2, 4, 6],
                    "src/dist/app.js": [2, 4, 6],
                },
            )

    def test_ignores_untracked_files_binaries_and_tracked_files_deleted_from_the_tree(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            make_repo(
                root,
                tracked={
                    "shot.png": b"\x89PNG\r\n\x1a\n\x00\x00\n" + CONFLICT.encode(),
                    "clean.md": ("제목\n" + "=" * 4 + "\n").encode(),
                    "gone.md": CONFLICT.encode(),
                },
                untracked={
                    "rules/sot.md.orig": CONFLICT.encode(),
                    "rules/sot.md.rej": CONFLICT.encode(),
                    "node_modules/pkg.js": CONFLICT.encode(),
                },
            )
            (root / "gone.md").unlink()

            self.assertEqual(reported(root), {})


class MainTests(unittest.TestCase):
    def test_exit_code_signals_whether_markers_were_found(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            make_repo(root, tracked={"a.md": "본문\n".encode()}, untracked={})
            self.assertEqual(self._run(root), 0)
            make_repo(
                root, tracked={"b.md": f"{OPEN_MARKER} HEAD\n".encode()}, untracked={}
            )
            self.assertEqual(self._run(root), 1)

    @staticmethod
    def _run(root: Path) -> int:
        """CLI 출력이 테스트 로그를 어지럽히지 않게 삼킨다."""
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return main([str(root)])


if __name__ == "__main__":
    unittest.main()
