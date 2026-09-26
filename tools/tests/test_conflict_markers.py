"""충돌 표식 검출 게이트 테스트.

표식이 있는 파일을 실제로 만들어 검출되는지, 표식을 닮은 정상 문서(Markdown setext 밑줄,
구분선)를 오검출하지 않는지 본다. 이 파일 자신이 검출되지 않도록 표식 문자열은 모두
런타임에 조립한다.
"""

from __future__ import annotations

import contextlib
import io
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
        self.assertEqual([number for number, _ in scan_text(f"a\n{SPLIT_MARKER}\nb")], [2])


class FindConflictMarkersTests(unittest.TestCase):
    def test_reports_path_and_skips_generated_directories_and_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "rules").mkdir()
            (root / "rules" / "sot.md").write_text(
                f"| 행 |\n{OPEN_MARKER} HEAD\n| A |\n{SPLIT_MARKER}\n| B |\n{CLOSE_MARKER} x\n",
                encoding="utf-8",
            )
            (root / "node_modules").mkdir()
            (root / "node_modules" / "pkg.js").write_text(
                f"{OPEN_MARKER} HEAD\n", encoding="utf-8"
            )
            (root / "shot.png").write_bytes(f"{OPEN_MARKER} HEAD\n".encode())
            (root / "clean.md").write_text("제목\n" + "=" * 4 + "\n", encoding="utf-8")

            findings = find_conflict_markers(root)

            self.assertEqual(
                sorted({path.relative_to(root).as_posix() for path, _, _ in findings}),
                ["rules/sot.md"],
            )
            self.assertEqual([number for _, number, _ in findings], [2, 4, 6])

    def test_clean_tree_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "a.md").write_text("본문\n", encoding="utf-8")
            self.assertEqual(find_conflict_markers(root), [])


class MainTests(unittest.TestCase):
    def test_exit_code_signals_whether_markers_were_found(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "a.md").write_text("본문\n", encoding="utf-8")
            self.assertEqual(self._run(root), 0)
            (root / "b.md").write_text(f"{OPEN_MARKER} HEAD\n", encoding="utf-8")
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
