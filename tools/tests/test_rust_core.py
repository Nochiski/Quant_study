from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_study_dev import rust_core
from quant_study_dev.rust_core import Action, CoreState


def _state(**overrides: object) -> CoreState:
    values: dict[str, object] = {
        "disabled": False,
        "environment_ready": True,
        "installed": True,
        "built_digest": "abc",
        "source_digest": "abc",
        "toolchain": True,
    }
    values.update(overrides)
    return CoreState(**values)  # pyright: ignore[reportArgumentType]  # reason: 시험용 부분 덮어쓰기


class DecideTest(unittest.TestCase):
    def test_installed_with_matching_digest_is_up_to_date(self) -> None:
        self.assertIs(rust_core.decide(_state()), Action.UP_TO_DATE)

    def test_missing_extension_is_built(self) -> None:
        self.assertIs(rust_core.decide(_state(installed=False)), Action.BUILD_MISSING)

    def test_changed_source_is_rebuilt(self) -> None:
        self.assertIs(rust_core.decide(_state(built_digest="old")), Action.BUILD_STALE)

    def test_install_without_stamp_is_treated_as_stale(self) -> None:
        self.assertIs(rust_core.decide(_state(built_digest=None)), Action.BUILD_STALE)

    def test_no_toolchain_warns_instead_of_building(self) -> None:
        self.assertIs(
            rust_core.decide(_state(installed=False, toolchain=False)),
            Action.WARN_NO_TOOLCHAIN,
        )

    def test_no_toolchain_keeps_a_fresh_install(self) -> None:
        self.assertIs(rust_core.decide(_state(toolchain=False)), Action.UP_TO_DATE)

    def test_disabled_and_missing_environment_skip(self) -> None:
        self.assertIs(rust_core.decide(_state(disabled=True)), Action.SKIP_DISABLED)
        self.assertIs(
            rust_core.decide(_state(environment_ready=False)),
            Action.SKIP_NO_ENVIRONMENT,
        )

    def test_skip_env_values(self) -> None:
        for value in ("1", "true", "YES", " on "):
            self.assertTrue(rust_core.skip_requested({rust_core.SKIP_ENV: value}))
        for value in ("", "0", "false"):
            self.assertFalse(rust_core.skip_requested({rust_core.SKIP_ENV: value}))


class SourceDigestTest(unittest.TestCase):
    def test_digest_changes_with_rust_source_and_ignores_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            crate = Path(directory)
            (crate / "src").mkdir()
            (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            (crate / "src" / "lib.rs").write_text("fn a() {}\n", encoding="utf-8")
            first = rust_core.source_digest(crate)
            self.assertEqual(first, rust_core.source_digest(crate))
            # 빌드 산출물은 지문에 들어가지 않는다.
            (crate / "target").mkdir()
            (crate / "target" / "out.rs").write_text("x", encoding="utf-8")
            self.assertEqual(first, rust_core.source_digest(crate))
            (crate / "src" / "lib.rs").write_text("fn b() {}\n", encoding="utf-8")
            self.assertNotEqual(first, rust_core.source_digest(crate))


class FindCargoTest(unittest.TestCase):
    def test_falls_back_to_rustup_default_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            name = "cargo.exe" if rust_core.os.name == "nt" else "cargo"
            (home / ".cargo" / "bin").mkdir(parents=True)
            (home / ".cargo" / "bin" / name).write_text("", encoding="utf-8")
            found = rust_core.find_cargo({}, lambda _: None, home)
            self.assertEqual(found, home / ".cargo" / "bin" / name)
            self.assertIsNone(rust_core.find_cargo({}, lambda _: None, home / "none"))

    def test_build_environment_prepends_cargo_dir_and_drops_wrapper_venv(self) -> None:
        cargo = Path("/opt/cargo/bin/cargo")
        environment = rust_core.build_environment(
            {"PATH": "/usr/bin", "VIRTUAL_ENV": "wrapper", "KEEP": "1"}, cargo
        )
        self.assertNotIn("VIRTUAL_ENV", environment)
        self.assertEqual(environment["KEEP"], "1")
        self.assertTrue(environment["PATH"].startswith(str(cargo.parent)))


class EnsureCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.backend = self.root / "backend"
        crate = self.backend / "rust" / "backtest_core"
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        (crate / "src" / "lib.rs").write_text("fn a() {}\n", encoding="utf-8")
        (self.backend / ".venv").mkdir()
        self.calls: list[list[str]] = []
        self.logs: list[str] = []

    def _which(self, name: str) -> str | None:
        return {"uv": "/bin/uv", "cargo": "/bin/cargo"}.get(name)

    def _runner(self, import_ok: bool, build_ok: bool):
        def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            self.calls.append(command)
            ok = build_ok if "maturin" in command else import_ok
            return subprocess.CompletedProcess(command, 0 if ok else 1)

        return run

    def _ensure(self, run, which=None, **kwargs: object) -> Action:
        return rust_core.ensure_core(
            self.root,
            disabled=bool(kwargs.get("disabled", False)),
            environ={},
            run=run,
            which=which or self._which,
            log=self.logs.append,
        )

    def test_missing_extension_builds_with_no_sync_and_writes_stamp(self) -> None:
        action = self._ensure(self._runner(import_ok=False, build_ok=True))
        self.assertIs(action, Action.BUILD_MISSING)
        build = self.calls[-1]
        self.assertEqual(
            build[:5], ["/bin/uv", "run", "--no-sync", "--project", str(self.backend)]
        )
        self.assertIn("--release", build)
        self.assertEqual(
            rust_core.read_stamp(self.backend),
            rust_core.source_digest(self.backend / "rust" / "backtest_core"),
        )
        # 지문이 맞으면 다음 실행은 빌드하지 않는다.
        self.calls.clear()
        action = self._ensure(self._runner(import_ok=True, build_ok=True))
        self.assertIs(action, Action.UP_TO_DATE)
        self.assertFalse(any("maturin" in call for call in self.calls))

    def test_build_failure_warns_and_does_not_raise_or_stamp(self) -> None:
        action = self._ensure(self._runner(import_ok=False, build_ok=False))
        self.assertIs(action, Action.BUILD_MISSING)
        self.assertIsNone(rust_core.read_stamp(self.backend))
        self.assertIn("실패", self.logs[-1])

    def test_no_toolchain_warns_without_building(self) -> None:
        with patch.object(rust_core.Path, "home", return_value=self.root / "nohome"):
            action = self._ensure(
                self._runner(import_ok=False, build_ok=True),
                which=lambda name: "/bin/uv" if name == "uv" else None,
            )
        self.assertIs(action, Action.WARN_NO_TOOLCHAIN)
        self.assertFalse(any("maturin" in call for call in self.calls))
        self.assertIn("Python reference", self.logs[-1])

    def test_disabled_runs_nothing(self) -> None:
        action = self._ensure(
            self._runner(import_ok=False, build_ok=True), disabled=True
        )
        self.assertIs(action, Action.SKIP_DISABLED)
        self.assertEqual(self.calls, [])

    def test_missing_backend_environment_skips(self) -> None:
        (self.backend / ".venv").rmdir()
        action = self._ensure(self._runner(import_ok=False, build_ok=True))
        self.assertIs(action, Action.SKIP_NO_ENVIRONMENT)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
