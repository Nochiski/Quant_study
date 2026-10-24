from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from quant_study_dev import backend_env, rust_core
from quant_study_dev.rust_core import Action


class DecideTest(unittest.TestCase):
    def test_fresh_or_unchecked_needs_nothing(self) -> None:
        for status in ("fresh", "unchecked"):
            self.assertIs(rust_core.decide(status, toolchain=False), Action.UP_TO_DATE)

    def test_unknown_status_is_not_built(self) -> None:
        for status in ("", "weird", "fresh extra"):
            self.assertIs(
                rust_core.decide(status, toolchain=True), Action.WARN_UNKNOWN_STATUS
            )

    def test_missing_stale_or_debug_builds_when_cargo_exists(self) -> None:
        for status in ("missing", "broken", "stale", "debug"):
            self.assertIsNone(rust_core.decide(status, toolchain=True))
            self.assertIs(
                rust_core.decide(status, toolchain=False), Action.WARN_NO_TOOLCHAIN
            )

    def test_flag_values(self) -> None:
        for value in ("1", "true", "YES", " on "):
            self.assertTrue(
                rust_core.flag({rust_core.SKIP_ENV: value}, rust_core.SKIP_ENV)
            )
        for value in ("", "0", "false"):
            self.assertFalse(
                rust_core.flag({rust_core.SKIP_ENV: value}, rust_core.SKIP_ENV)
            )


class BackendEnvTest(unittest.TestCase):
    def test_venv_honours_uv_project_environment(self) -> None:
        backend = Path("checkout") / "backend"
        self.assertEqual(backend_env.backend_venv(backend, {}), backend / ".venv")
        self.assertEqual(
            backend_env.backend_venv(backend, {"UV_PROJECT_ENVIRONMENT": "env"}),
            backend / "env",
        )
        absolute = Path(tempfile.gettempdir()) / "venv"
        self.assertEqual(
            backend_env.backend_venv(
                backend, {"UV_PROJECT_ENVIRONMENT": str(absolute)}
            ),
            absolute,
        )

    def test_build_environment_prepends_cargo_dir_and_drops_wrapper_venv(self) -> None:
        cargo = Path("/opt/cargo/bin/cargo")
        environment = rust_core.build_environment(
            {"PATH": "/usr/bin", "VIRTUAL_ENV": "wrapper", "KEEP": "1"}, cargo
        )
        self.assertNotIn("VIRTUAL_ENV", environment)
        self.assertEqual(environment["KEEP"], "1")
        self.assertTrue(environment["PATH"].startswith(str(cargo.parent)))

    def test_cargo_falls_back_to_rustup_default_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            name = "cargo.exe" if os.name == "nt" else "cargo"
            (home / ".cargo" / "bin").mkdir(parents=True)
            (home / ".cargo" / "bin" / name).write_text("", encoding="utf-8")
            found = rust_core.find_cargo({}, lambda _: None, home)
            self.assertEqual(found, home / ".cargo" / "bin" / name)
            self.assertIsNone(rust_core.find_cargo({}, lambda _: None, home / "none"))


class EnsureCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.backend = self.root / "backend"
        (self.backend / ".venv").mkdir(parents=True)
        build_script = self.backend / rust_core.BUILD_SCRIPT
        build_script.parent.mkdir(parents=True)
        build_script.write_text("fn main() {}", encoding="utf-8")
        os.utime(build_script, (0, 0))
        self.build_script = build_script
        self.calls: list[list[str]] = []
        self.logs: list[str] = []
        self.statuses = ["missing", "fresh"]
        self.build_ok = True

    def _run(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        if "maturin" in command:
            return subprocess.CompletedProcess(command, 0 if self.build_ok else 1)
        status = self.statuses.pop(0)
        # 상태 앞에 경고 같은 다른 출력이 있어도 마지막 줄만 본다.
        return subprocess.CompletedProcess(
            command, 0, f"warning line\n{status}\n".encode(), b""
        )

    def _ensure(self, cargo: bool = True, **kwargs: object) -> Action:
        return rust_core.ensure_core(
            self.root,
            uv="/bin/uv",
            disabled=bool(kwargs.get("disabled", False)),
            environ={"CARGO_HOME": str(self.root / "nocargo")},
            run=self._run,
            which=lambda name: "/bin/cargo" if cargo and name == "cargo" else None,
            log=self.logs.append,
        )

    def _built(self) -> bool:
        return any("maturin" in call for call in self.calls)

    def test_missing_core_is_built_with_no_sync_and_rechecked(self) -> None:
        self.assertIs(self._ensure(), Action.BUILT)
        status, build, recheck = self.calls
        self.assertEqual(
            status[:5], ["/bin/uv", "run", "--no-sync", "--project", str(self.backend)]
        )
        self.assertEqual(status[-2:], ["-c", rust_core.STATUS_SCRIPT])
        self.assertIn("--release", build)
        # 새 설정 파일을 놓치지 않게 빌드 전에 build.rs를 다시 돌게 한다.
        self.assertGreater(self.build_script.stat().st_mtime, 0)
        self.assertEqual(recheck, status)

    def test_stale_core_is_rebuilt(self) -> None:
        self.statuses = ["stale", "fresh"]
        self.assertIs(self._ensure(), Action.BUILT)
        self.assertIn("오래됐습니다", self.logs[0])

    def test_fresh_core_runs_only_the_status_check(self) -> None:
        self.statuses = ["fresh"]
        self.assertIs(self._ensure(), Action.UP_TO_DATE)
        self.assertFalse(self._built())

    def test_debug_build_is_rebuilt_in_release(self) -> None:
        self.statuses = ["debug", "fresh"]
        self.assertIs(self._ensure(), Action.BUILT)
        self.assertIn("디버그", self.logs[0])

    def test_build_failure_warns_and_retries_next_time(self) -> None:
        self.build_ok = False
        self.assertIs(self._ensure(), Action.WARN_BUILD_FAILED)
        self.assertIn("다음 uv run dev에서 다시 시도", self.logs[-1])

    def test_successful_build_that_is_still_stale_is_reported_separately(self) -> None:
        self.statuses = ["stale", "stale"]
        self.assertIs(self._ensure(), Action.WARN_DIGEST_MISMATCH)
        self.assertIn("빌드는 성공했지만", self.logs[-1])

    def test_unknown_status_warns_without_building(self) -> None:
        self.statuses = ["huh"]
        self.assertIs(self._ensure(), Action.WARN_UNKNOWN_STATUS)
        self.assertFalse(self._built())

    def test_no_toolchain_warns_without_building(self) -> None:
        self.statuses = ["stale"]
        self.assertIs(self._ensure(cargo=False), Action.WARN_NO_TOOLCHAIN)
        self.assertFalse(self._built())
        self.assertIn("Python reference", self.logs[-1])

    def test_any_exception_becomes_a_warning(self) -> None:
        def broken(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[bytes]:
            raise FileNotFoundError("uv")

        def failing(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(command, 1, b"", b"Traceback ...")

        for run in (broken, failing):
            action = rust_core.ensure_core(
                self.root,
                uv="/bin/uv",
                disabled=False,
                environ={},
                run=run,
                log=self.logs.append,
            )
            self.assertIs(action, Action.WARN_ERROR)
            self.assertIn("서버는 계속 시작합니다", self.logs[-1])

    def test_disabled_or_missing_environment_runs_nothing(self) -> None:
        self.assertIs(self._ensure(disabled=True), Action.SKIP_DISABLED)
        skip = rust_core.ensure_core(
            self.root,
            uv="/bin/uv",
            disabled=False,
            environ={rust_core.SKIP_ENV: "1"},
            run=self._run,
            log=self.logs.append,
        )
        self.assertIs(skip, Action.SKIP_DISABLED)
        missing = rust_core.ensure_core(
            self.root,
            uv="/bin/uv",
            disabled=False,
            environ={"UV_PROJECT_ENVIRONMENT": "missing"},
            run=self._run,
            log=self.logs.append,
        )
        self.assertIs(missing, Action.SKIP_NO_ENVIRONMENT)
        no_uv = rust_core.ensure_core(
            self.root,
            uv=None,
            disabled=False,
            environ={},
            run=self._run,
            log=self.logs.append,
        )
        self.assertIs(no_uv, Action.SKIP_NO_ENVIRONMENT)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
