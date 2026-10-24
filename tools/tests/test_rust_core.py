from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from quant_study_dev import backend_env, rust_core
from quant_study_dev.rust_core import Action, CoreState, Stamp

ARTIFACT = ("lib/python3.11/site-packages/backtest_core/backtest_core.so", 10, 1)


def _state(**overrides: object) -> CoreState:
    values: dict[str, object] = {
        "artifact": ARTIFACT,
        "stamp": Stamp(source="abc", artifact=ARTIFACT),
        "source_digest": "abc",
        "toolchain": True,
        "retry": False,
    }
    values.update(overrides)
    return CoreState(**values)  # pyright: ignore[reportArgumentType]  # reason: 시험용 부분 덮어쓰기


class DecideTest(unittest.TestCase):
    def test_same_source_and_same_installed_file_is_up_to_date(self) -> None:
        self.assertIs(rust_core.decide(_state()), Action.UP_TO_DATE)
        # 툴체인이 없어도 맞는 설치본은 그대로 쓴다.
        self.assertIs(rust_core.decide(_state(toolchain=False)), Action.UP_TO_DATE)

    def test_missing_extension_is_built(self) -> None:
        self.assertIs(rust_core.decide(_state(artifact=None)), Action.BUILD_MISSING)

    def test_changed_source_is_rebuilt(self) -> None:
        self.assertIs(rust_core.decide(_state(source_digest="new")), Action.BUILD_STALE)

    def test_manual_rebuild_changes_the_file_and_is_treated_as_stale(self) -> None:
        manual = (ARTIFACT[0], 11, 2)
        self.assertIs(rust_core.decide(_state(artifact=manual)), Action.BUILD_STALE)

    def test_install_without_stamp_is_treated_as_stale(self) -> None:
        self.assertIs(rust_core.decide(_state(stamp=Stamp())), Action.BUILD_STALE)

    def test_no_toolchain_warns_instead_of_building(self) -> None:
        self.assertIs(
            rust_core.decide(_state(source_digest="new", toolchain=False)),
            Action.WARN_NO_TOOLCHAIN,
        )

    def test_previous_failure_with_same_source_is_not_retried_unless_asked(
        self,
    ) -> None:
        failed = Stamp(source="old", artifact=ARTIFACT, failed_source="new")
        self.assertIs(
            rust_core.decide(_state(stamp=failed, source_digest="new")),
            Action.WARN_PREVIOUS_FAILURE,
        )
        self.assertIs(
            rust_core.decide(_state(stamp=failed, source_digest="new", retry=True)),
            Action.BUILD_STALE,
        )
        # 소스가 또 바뀌면 다시 시도한다.
        self.assertIs(
            rust_core.decide(_state(stamp=failed, source_digest="newer")),
            Action.BUILD_STALE,
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

    def test_stamp_round_trip_and_broken_stamp(self) -> None:
        stamp = Stamp(source="a", artifact=ARTIFACT, failed_source="b")
        self.assertEqual(Stamp.parse(stamp.to_json()), stamp)
        self.assertEqual(Stamp.parse("not json"), Stamp())
        self.assertEqual(Stamp.parse('{"artifact": 3}'), Stamp())


class SourceDigestTest(unittest.TestCase):
    def test_digest_covers_crate_files_and_cargo_settings_but_not_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            crate = backend / rust_core.CRATE
            (crate / "src").mkdir(parents=True)
            (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            (crate / "src" / "lib.rs").write_text("fn a() {}\n", encoding="utf-8")
            first = rust_core.source_digest(backend)
            self.assertEqual(first, rust_core.source_digest(backend))
            (crate / "target").mkdir()
            (crate / "target" / "out.rs").write_text("x", encoding="utf-8")
            self.assertEqual(first, rust_core.source_digest(backend))
            changes: list[Callable[[], object]] = [
                lambda: (crate / "build.rs").write_text(
                    "fn main() {}", encoding="utf-8"
                ),
                lambda: (crate / "src" / "data.json").write_text(
                    "{}", encoding="utf-8"
                ),
                lambda: (backend / "rust" / "rust-toolchain.toml").write_text(
                    "[toolchain]", encoding="utf-8"
                ),
                lambda: (
                    (backend / ".cargo").mkdir(),
                    (backend / ".cargo" / "config.toml").write_text(
                        "", encoding="utf-8"
                    ),
                ),
                lambda: (crate / "src" / "lib.rs").write_text(
                    "fn b() {}\n", encoding="utf-8"
                ),
            ]
            previous = first
            for change in changes:
                change()
                current = rust_core.source_digest(backend)
                self.assertNotEqual(previous, current)
                previous = current


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
        crate = self.backend / rust_core.CRATE
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        (crate / "src" / "lib.rs").write_text("fn a() {}\n", encoding="utf-8")
        self.venv = self.backend / ".venv"
        self.packages = (
            self.venv / "Lib" / "site-packages"
            if os.name == "nt"
            else self.venv / "lib" / "python3.11" / "site-packages"
        )
        self.packages.mkdir(parents=True)
        self.calls: list[list[str]] = []
        self.logs: list[str] = []
        self.build_ok = True
        self.uninstall_ok = True

    def _install(self, content: bytes = b"so") -> None:
        package = self.packages / "backtest_core"
        package.mkdir(exist_ok=True)
        (package / "backtest_core.cpython-311-darwin.so").write_bytes(content)

    def _which(self, name: str) -> str | None:
        return {"uv": "/bin/uv", "cargo": "/bin/cargo"}.get(name)

    def _run(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        if "maturin" in command:
            if self.build_ok:
                self._install(b"fresh-build")
            return subprocess.CompletedProcess(command, 0 if self.build_ok else 1)
        if "uninstall" in command and self.uninstall_ok:
            for path in (self.packages / "backtest_core").iterdir():
                path.unlink()
        return subprocess.CompletedProcess(command, 0 if self.uninstall_ok else 1)

    def _ensure(self, **kwargs: object) -> Action:
        which = kwargs.pop("which", self._which)
        return rust_core.ensure_core(
            self.root,
            disabled=bool(kwargs.pop("disabled", False)),
            retry=bool(kwargs.pop("retry", False)),
            environ=kwargs.pop("environ", {}),  # pyright: ignore[reportArgumentType]  # reason: 시험 인자
            run=self._run,
            which=which,  # pyright: ignore[reportArgumentType]  # reason: 시험 인자
            log=self.logs.append,
        )

    def _built(self) -> bool:
        return any("maturin" in call for call in self.calls)

    def test_missing_extension_builds_with_no_sync_and_records_the_file(self) -> None:
        self.assertIs(self._ensure(), Action.BUILD_MISSING)
        build = next(call for call in self.calls if "maturin" in call)
        self.assertEqual(
            build[:5], ["/bin/uv", "run", "--no-sync", "--project", str(self.backend)]
        )
        self.assertIn("--release", build)
        stamp = rust_core.read_stamp(self.venv)
        self.assertEqual(stamp.source, rust_core.source_digest(self.backend))
        self.assertEqual(stamp.artifact, rust_core.find_artifact(self.venv))
        # 확인에 import 서브프로세스를 쓰지 않으므로 최신이면 아무것도 실행하지 않는다.
        self.calls.clear()
        self.assertIs(self._ensure(), Action.UP_TO_DATE)
        self.assertEqual(self.calls, [])

    def test_stale_build_failure_removes_the_old_core_and_records_failure(self) -> None:
        self._install(b"old")
        self.build_ok = False
        self.assertIs(self._ensure(), Action.BUILD_STALE)
        self.assertIsNone(rust_core.find_artifact(self.venv))
        self.assertTrue(any("uninstall" in call for call in self.calls))
        self.assertIn("낡은 Rust 코어를 지웠습니다", self.logs[-1])
        # 같은 소스로는 다음 시작에 긴 빌드를 되풀이하지 않는다.
        self.calls.clear()
        self.assertIs(self._ensure(), Action.WARN_PREVIOUS_FAILURE)
        self.assertFalse(self._built())
        self.assertIs(self._ensure(retry=True), Action.BUILD_MISSING)
        self.assertTrue(self._built())

    def test_no_toolchain_removes_the_stale_core(self) -> None:
        self._install(b"old")
        with patch.object(rust_core.Path, "home", return_value=self.root / "nohome"):
            action = self._ensure(
                which=lambda name: "/bin/uv" if name == "uv" else None
            )
        self.assertIs(action, Action.WARN_NO_TOOLCHAIN)
        self.assertFalse(self._built())
        self.assertIsNone(rust_core.find_artifact(self.venv))
        self.assertIn("Python reference", self.logs[-1])

    def test_failed_removal_warns_that_results_may_differ(self) -> None:
        self._install(b"old")
        self.build_ok = False
        self.uninstall_ok = False
        self._ensure()
        self.assertIsNotNone(rust_core.find_artifact(self.venv))
        self.assertIn("다른 결과", self.logs[-1])

    def test_os_errors_become_a_warning(self) -> None:
        def broken(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[bytes]:
            raise FileNotFoundError("uv")

        action = rust_core.ensure_core(
            self.root,
            disabled=False,
            environ={},
            run=broken,
            which=self._which,
            log=self.logs.append,
        )
        self.assertIs(action, Action.WARN_ERROR)
        self.assertIn("서버는 계속 시작합니다", self.logs[-1])

    def test_disabled_and_missing_environment_return_before_any_work(self) -> None:
        with patch.object(rust_core, "source_digest") as digest:
            self.assertIs(self._ensure(disabled=True), Action.SKIP_DISABLED)
            self.assertIs(
                self._ensure(environ={rust_core.SKIP_ENV: "1"}), Action.SKIP_DISABLED
            )
            self.assertIs(
                self._ensure(environ={"UV_PROJECT_ENVIRONMENT": "missing"}),
                Action.SKIP_NO_ENVIRONMENT,
            )
            digest.assert_not_called()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
