from __future__ import annotations

import os
import signal
import socket
import subprocess
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, call, patch

from quant_study_dev import dev

CHECKOUT = Path("checkout")
POPEN = subprocess.Popen


def _owned(name: str = "서버", port: int = 8000) -> dev.OwnedServer:
    process = Mock(spec=POPEN)
    process.pid = 1234
    process.poll.return_value = None
    return dev.OwnedServer(
        dev.DevServer(name, ("command",), CHECKOUT, port, "url"), process
    )


class DevTest(unittest.TestCase):
    def setUp(self) -> None:
        # 서버 수명주기 시험이 실제 uv·cargo를 부르지 않게 Rust 코어 준비를 막는다.
        patcher = patch.object(dev, "ensure_core")
        self.ensure_core = patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(dev.shutil, "which", side_effect=lambda name: f"C:/bin/{name}.cmd")
    def test_reuses_canonical_commands_without_installing_dependencies(
        self, which: Mock
    ) -> None:
        root = Path("checkout")
        backend, frontend = dev._servers(root)
        self.assertEqual(
            backend.command,
            (
                "C:/bin/uv.cmd",
                "run",
                "--no-sync",
                "--project",
                str(root / "backend"),
                "server",
            ),
        )
        self.assertEqual(frontend.command[:4], ("C:/bin/npm.cmd", "run", "dev", "--"))
        self.assertIn("--strictPort", frontend.command)
        self.assertEqual(frontend.cwd, root)

    @patch.object(dev.subprocess, "Popen")
    def test_start_preserves_runtime_environment_and_inherits_io(
        self, popen: Mock
    ) -> None:
        owned = _owned()
        with patch.dict(
            os.environ,
            {"VIRTUAL_ENV": "wrapper", "STRATEGY_WORKBENCH_DB_PATH": "my.db"},
            clear=True,
        ):
            dev._start(owned.server)
        arguments = popen.call_args.kwargs
        self.assertEqual(arguments["env"], {"STRATEGY_WORKBENCH_DB_PATH": "my.db"})
        self.assertNotIn("shell", arguments)
        self.assertNotIn("stdout", arguments)
        self.assertNotIn("stderr", arguments)

    def test_occupied_port_is_rejected_without_touching_its_owner(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
            server = dev.DevServer("프런트", ("command",), CHECKOUT, port, "url")
            with self.assertRaisesRegex(RuntimeError, str(port)):
                dev._require_available_port(server)
            self.assertEqual(listener.getsockname()[1], port)

    @patch.object(dev, "_servers")
    @patch.object(
        dev, "_require_available_port", side_effect=[None, RuntimeError("occupied")]
    )
    @patch.object(dev, "_start")
    def test_both_ports_are_checked_before_any_process_starts(
        self, start: Mock, available: Mock, servers: Mock
    ) -> None:
        servers.return_value = (_owned("백엔드").server, _owned("프런트").server)
        self.assertEqual(dev.run(Path("checkout"), open_browser=False), 1)
        start.assert_not_called()

    @patch.object(dev, "_servers")
    @patch.object(dev, "_require_available_port")
    @patch.object(dev, "_start")
    @patch.object(dev, "_stop")
    def test_second_spawn_failure_cleans_the_already_owned_child(
        self, stop: Mock, start: Mock, available: Mock, servers: Mock
    ) -> None:
        backend = _owned("백엔드")
        servers.return_value = (backend.server, _owned("프런트").server)
        start.side_effect = [backend, OSError("npm failed")]
        self.assertEqual(dev.run(Path("checkout"), open_browser=False), 1)
        stop.assert_called_once_with([backend])

    @patch.object(dev, "_ready")
    def test_startup_child_exit_is_reported_before_opening_a_browser(
        self, ready: Mock
    ) -> None:
        owned = _owned()
        cast(Mock, owned.process).poll.return_value = 17
        with self.assertRaisesRegex(RuntimeError, "exit=17"):
            dev._wait_until_ready([owned])
        ready.assert_not_called()

    @patch.object(dev.time, "sleep")
    @patch.object(dev.time, "monotonic", side_effect=[0, 1, 31])
    @patch.object(dev, "_ready", return_value=False)
    def test_readiness_timeout_identifies_the_unready_service(
        self, ready: Mock, monotonic: Mock, sleep: Mock
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "프런트"):
            dev._wait_until_ready([_owned("프런트")])

    @patch.object(dev, "_servers")
    @patch.object(dev, "_require_available_port")
    @patch.object(dev, "_start")
    @patch.object(dev, "_wait_until_ready")
    @patch.object(dev, "_require_running", side_effect=KeyboardInterrupt)
    @patch.object(dev, "_stop")
    @patch.object(dev.webbrowser, "open", return_value=True)
    def test_open_waits_for_readiness_and_ctrl_c_cleans_both(
        self,
        browser: Mock,
        stop: Mock,
        running: Mock,
        ready: Mock,
        start: Mock,
        available: Mock,
        servers: Mock,
    ) -> None:
        children = [_owned("백엔드"), _owned("프런트")]
        servers.return_value = tuple(child.server for child in children)
        start.side_effect = children
        observed: list[str] = []
        ready.side_effect = lambda _children: observed.append("ready")
        browser.side_effect = lambda _url: observed.append("open") or True
        self.assertEqual(dev.run(Path("checkout"), open_browser=True), 130)
        self.assertEqual(observed, ["ready", "open"])
        stop.assert_called_once_with(children)

    @patch.object(dev.os, "killpg", create=True)
    @patch.object(dev.os, "name", "posix")
    def test_cleanup_removes_remaining_group_when_leader_has_already_exited(
        self, killpg: Mock
    ) -> None:
        owned = _owned()
        cast(Mock, owned.process).poll.return_value = 0
        dev._stop([owned])
        self.assertEqual(
            killpg.call_args_list,
            [call(1234, signal.SIGINT), call(1234, signal.SIGKILL)],
        )

    @patch.object(dev.os, "name", "nt")
    @patch.object(dev.signal, "CTRL_BREAK_EVENT", 1, create=True)
    @patch.object(dev.os, "kill")
    @patch.object(dev.subprocess, "run")
    def test_windows_escalation_targets_only_the_directly_spawned_tree(
        self, run: Mock, kill: Mock
    ) -> None:
        owned = _owned()
        cast(Mock, owned.process).wait.side_effect = [
            subprocess.TimeoutExpired("command", 5),
            0,
        ]
        dev._stop([owned])
        kill.assert_called_once_with(1234, 1)
        run.assert_called_once_with(
            ["taskkill", "/PID", "1234", "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch.object(dev.os, "name", "nt")
    @patch.object(dev.os, "kill")
    @patch.object(dev.signal, "CTRL_BREAK_EVENT", 1, create=True)
    def test_windows_interrupt_still_targets_group_after_its_leader_exits(
        self, kill: Mock
    ) -> None:
        owned = _owned()
        cast(Mock, owned.process).poll.return_value = 0
        dev._interrupt(owned)
        kill.assert_called_once_with(1234, 1)


class DevRustBuildTest(unittest.TestCase):
    @patch.object(dev, "_servers", return_value=())
    @patch.object(dev, "ensure_core")
    @patch.object(dev, "_wait_until_ready")
    @patch.object(dev, "_require_running", side_effect=KeyboardInterrupt)
    def test_rust_core_is_prepared_before_servers_and_can_be_disabled(
        self, _running: Mock, _ready: Mock, ensure_core: Mock, _servers: Mock
    ) -> None:
        self.assertEqual(dev.run(CHECKOUT, open_browser=False), 130)
        ensure_core.assert_called_once_with(CHECKOUT, disabled=False, retry=False)
        ensure_core.reset_mock()
        dev.run(CHECKOUT, open_browser=False, rust_build=False, retry_rust_build=True)
        ensure_core.assert_called_once_with(CHECKOUT, disabled=True, retry=True)

    @patch.object(dev, "_servers")
    @patch.object(dev, "_require_available_port", side_effect=RuntimeError("occupied"))
    @patch.object(dev, "ensure_core")
    def test_occupied_port_is_rejected_before_a_long_build(
        self, ensure_core: Mock, _port: Mock, servers: Mock
    ) -> None:
        servers.return_value = (dev.DevServer("백엔드", ("c",), CHECKOUT, 8000, "u"),)
        self.assertEqual(dev.run(CHECKOUT, open_browser=False), 1)
        ensure_core.assert_not_called()

    @patch.object(dev, "_servers")
    @patch.object(
        dev, "_require_available_port", side_effect=[None, RuntimeError("포트 8000")]
    )
    @patch.object(dev, "ensure_core")
    @patch.object(dev, "_start")
    def test_port_taken_during_build_is_rejected_before_starting(
        self, start: Mock, _ensure: Mock, _port: Mock, servers: Mock
    ) -> None:
        servers.return_value = (dev.DevServer("백엔드", ("c",), CHECKOUT, 8000, "u"),)
        self.assertEqual(dev.run(CHECKOUT, open_browser=False), 1)
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
