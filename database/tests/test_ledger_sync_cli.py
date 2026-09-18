"""ledger_sync CLI — 종료 코드·JSON·status·catalog 위임 명령."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from ledger_sync import __main__ as cli
from ledger_sync_fake import FakeRemote

REMOTE = "/equity"


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> FakeRemote:
    fake = FakeRemote()
    fake.put_table(REMOTE, "security", "b1", {"": {"part0.parquet": b"S1"}}, {"": "1:1"})
    fake.put_meta(REMOTE, "baseline.json", b"{}")

    @contextmanager
    def fake_open(endpoint, *, accept_new_host_key=False):  # noqa: ANN001, ANN202  # reason: open_sftp 대체
        yield fake

    monkeypatch.setattr(cli, "open_sftp", fake_open)
    return fake


def _base(root: Path) -> list[str]:
    return ["--root", str(root), "--layer", "equity"]


def test_plan_pull_status_round_trip(remote: FakeRemote, tmp_path: Path, capsys) -> None:
    assert cli.main([*_base(tmp_path), "--json", "plan"]) == cli.EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["tables"][0] == {
        "table": "security", "action": "new_build", "remote_build": "b1", "local_build": None,
        "download_bytes": 2, "reused_bytes": 0, "manifest_refresh": False, "detail": None}
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "done=1" in out
    assert (tmp_path / "equity" / "_sync" / "state.json").exists()
    assert list((tmp_path / "equity" / "_sync" / "logs").glob("pull_*.log"))
    assert cli.main([*_base(tmp_path), "status", "--remote"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "security" in out and "behind=0" in out and "last sync: never" in out


def test_pull_exit_codes(remote: FakeRemote, tmp_path: Path, capsys) -> None:
    remote.fail_once.add(f"{REMOTE}/security/v=b1/part0.parquet")
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check"]) == cli.EXIT_ERROR
    capsys.readouterr()

    class Drifting(FakeRemote):
        def download(self, path: str, local: Path, progress=None) -> int:  # noqa: ANN001  # reason: 테스트 더블
            n = super().download(path, local, progress)
            self.put_table(REMOTE, "security", "b2", {"": {"part0.parquet": b"S2"}}, {"": "1:2"})
            return n

    remote.__class__ = Drifting
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check"]) == cli.EXIT_DRIFTED
    assert "drifted" in capsys.readouterr().out


def test_verify_offline_and_mismatch_exit(remote: FakeRemote, tmp_path: Path, capsys) -> None:
    cli.main([*_base(tmp_path), "pull", "--no-space-check"])
    capsys.readouterr()
    # 가짜 parquet(바이트 "S1")는 duckdb 가 읽지 못한다 → hash 층위는 예외가 아니라 값으로
    # 실패해야 한다
    assert cli.main([*_base(tmp_path), "--json", "verify", "--offline"]) == cli.EXIT_MISMATCH
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["findings"][0]["level"] == "hash"
    assert "parquet unreadable" in result["findings"][0]["detail"]
    assert result["checked"] == {"hash": 1}


def test_sync_writes_last_run_and_skips_catalog(remote: FakeRemote, tmp_path: Path, capsys) -> None:
    assert cli.main([*_base(tmp_path), "sync", "--no-space-check", "--skip-catalog"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "== verify: ok" in out and "== sync equity: exit=0" in out
    last = json.loads((tmp_path / "equity" / "_sync" / "last_run.json").read_text(encoding="utf-8"))
    assert last["exit_code"] == 0 and last["pull"]["ok"] and last["verify"]["ok"]
    assert cli.main([*_base(tmp_path), "--json", "status"]) == cli.EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["last_run"]["exit_code"] == 0 and status["tables"] == {"security": "b1"}


def test_sync_reports_verify_mismatch_after_remote_moves_on(remote: FakeRemote, tmp_path: Path,
                                                            capsys, monkeypatch) -> None:
    cli.main([*_base(tmp_path), "sync", "--no-space-check", "--skip-catalog"])
    capsys.readouterr()
    # 검증 단계에서만 원격이 앞서 나간 상황을 흉내 낸다
    original = cli._run_verify

    def advance_then_verify(remote_fs, args, layer_root, state, levels):  # noqa: ANN001, ANN202  # reason: 래핑
        remote.put_table(REMOTE, "security", "b2", {"": {"part0.parquet": b"S2"}}, {"": "1:2"})
        return original(remote_fs, args, layer_root, state, levels)

    monkeypatch.setattr(cli, "_run_verify", advance_then_verify)
    rc = cli.main([*_base(tmp_path), "sync", "--no-space-check", "--skip-catalog"])
    assert rc == cli.EXIT_MISMATCH
    assert "current_build differs" in capsys.readouterr().out


def test_catalog_is_equity_only_and_delegates_to_equity_cli(tmp_path: Path, capsys) -> None:
    assert cli.main([*_base(tmp_path), "--layer", "stage", "catalog"]) == cli.EXIT_ERROR
    command = cli.catalog_command(tmp_path / "equity")
    assert command[1:3] == ["-m", "equity"] and command[-1] == "catalog"
    assert "--stage-root" in command


def test_build_window_detection() -> None:
    from datetime import UTC, datetime

    assert cli._in_build_window(datetime(2026, 9, 19, 13, 30, tzinfo=UTC))
    assert cli._in_build_window(datetime(2026, 9, 19, 0, 10, tzinfo=UTC))
    assert not cli._in_build_window(datetime(2026, 9, 19, 5, 0, tzinfo=UTC))
