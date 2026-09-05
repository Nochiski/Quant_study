from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quant_study_dev import server


class ServerDelegateTest(unittest.TestCase):
    def test_finds_repo_root_from_a_descendant(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(server._repo_root(root / "tools" / "tests"), root)

    @patch.object(server, "_repo_root")
    @patch.object(server.subprocess, "run")
    def test_delegates_args_and_inherits_process_io(
        self,
        run: Mock,
        repo_root: Mock,
    ) -> None:
        root = Path("C:/checkout/Quant_study")
        repo_root.return_value = root
        run.return_value = subprocess.CompletedProcess([], 0)
        with (
            patch.object(sys, "argv", ["server", "--port", "8123"]),
            patch.dict(os.environ, {"VIRTUAL_ENV": "C:/wrapper", "KEEP": "yes"}, clear=True),
        ):
            server.main()

        run.assert_called_once_with(
            [
                "uv",
                "run",
                "--project",
                str(root / "backend"),
                "server",
                "--port",
                "8123",
            ],
            cwd=root / "backend",
            check=False,
            env={"KEEP": "yes"},
        )

    @patch.object(server, "_repo_root")
    @patch.object(server.subprocess, "run")
    def test_propagates_backend_exit_code(
        self,
        run: Mock,
        repo_root: Mock,
    ) -> None:
        repo_root.return_value = Path("C:/checkout/Quant_study")
        run.return_value = subprocess.CompletedProcess([], 17)

        with self.assertRaises(SystemExit) as raised:
            server.main()

        self.assertEqual(raised.exception.code, 17)

    @patch.object(server, "_repo_root")
    @patch.object(server.subprocess, "run", side_effect=KeyboardInterrupt)
    def test_exits_cleanly_after_terminal_interrupt(
        self,
        run: Mock,
        repo_root: Mock,
    ) -> None:
        repo_root.return_value = Path("C:/checkout/Quant_study")

        with self.assertRaises(SystemExit) as raised:
            server.main()

        self.assertEqual(raised.exception.code, 130)


if __name__ == "__main__":
    unittest.main()
