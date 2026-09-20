"""ledger_sync CLI — 종료 코드·JSON·status·catalog 위임 명령."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from ledger_sync import __main__ as cli
from ledger_sync.layout import snapshot_id
from ledger_sync_fake import FakeRemote

REMOTE = "/equity"


def _real_parquet(tmp_path: Path) -> tuple[bytes, str]:
    """duckdb 로 만든 진짜 parquet 와 서버 규칙(`stage/build.py:_content_hash`)의 해시."""
    import duckdb

    path = tmp_path / "srv" / "security" / "part0.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY (SELECT '005930' AS ticker, 1 AS close) TO '{path}' (FORMAT PARQUET)")
    n, h = con.execute("SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM "
                       f"read_parquet('{path.parent}/*.parquet', hive_partitioning=true) t"
                       ).fetchone()
    con.close()
    return path.read_bytes(), f"{int(str(n))}:{format(int(str(h)), 'x')}"


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRemote:
    fake = FakeRemote()
    data, content_hash = _real_parquet(tmp_path)
    fake.put_table(REMOTE, "security", "b1", {"": {"part0.parquet": data}}, {"": content_hash})
    fake.put_meta(REMOTE, "baseline.json", b"{}")

    @contextmanager
    def fake_open(endpoint, *, accept_new_host_key=False):  # noqa: ANN001, ANN202  # reason: open_sftp 대체
        yield fake

    monkeypatch.setattr(cli, "open_sftp", fake_open)
    return fake


# RFC 5737 문서용 주소 — 공개 저장소라 실제 서버 주소는 코드·테스트 어디에도 두지 않는다.
TEST_HOST = "203.0.113.10"


def _base(root: Path) -> list[str]:
    return ["--root", str(root), "--layer", "equity", "--host", TEST_HOST]


def test_plan_pull_status_round_trip(remote: FakeRemote, tmp_path: Path, capsys) -> None:
    assert cli.main([*_base(tmp_path), "--json", "plan"]) == cli.EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["tables"][0] == {
        "table": "security", "action": "new_build", "remote_build": "b1", "local_build": None,
        "download_bytes": len(remote.files[f"{REMOTE}/security/v=b1/part0.parquet"]),
        "reused_bytes": 0, "manifest_refresh": False, "detail": None}
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
    # pull 전(state 없음)에는 검사 0건이므로 ok 가 아니라 MISMATCH 다
    assert cli.main([*_base(tmp_path), "--json", "verify", "--offline"]) == cli.EXIT_MISMATCH
    empty = json.loads(capsys.readouterr().out)
    assert empty["checked"] == {} and "nothing to verify" in empty["findings"][0]["detail"]
    cli.main([*_base(tmp_path), "pull", "--no-space-check"])
    capsys.readouterr()
    assert cli.main([*_base(tmp_path), "verify", "--offline"]) == cli.EXIT_OK
    capsys.readouterr()
    # 받은 parquet 를 같은 크기의 쓰레기로 바꾼다 → hash 층위는 예외가 아니라 값으로 실패해야 한다
    target = tmp_path / "equity" / "security" / "v=b1" / "part0.parquet"
    target.write_bytes(b"X" * target.stat().st_size)
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
    assert "catalog_rc" not in last   # --skip-catalog
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


def test_sync_runs_catalog_before_verify_so_a_stale_catalog_is_rebuilt(remote: FakeRemote,
                                                                       tmp_path: Path, capsys,
                                                                       monkeypatch) -> None:
    # 서버 _catalog_meta.json 의 snapshot 이 로컬 빌드 집합과 다르면 verify(manifest) 가
    # "재생성하라" 고 지적한다 — sync 는 그 재생성(catalog)을 verify 앞에서 돌려야 한다.
    remote.put_meta(REMOTE, "_catalog_meta.json", b'{"snapshot_id": "stale"}')
    order: list[str] = []

    def fake_catalog(equity_root: Path, log) -> int:  # noqa: ANN001  # reason: run_catalog 대체
        order.append("catalog")
        (equity_root / "_catalog_meta.json").write_text(
            json.dumps({"snapshot_id": snapshot_id({"security": "b1"})}), encoding="utf-8")
        return cli.EXIT_OK

    original_verify = cli._run_verify

    def spy_verify(*a, **k):  # noqa: ANN002, ANN003, ANN202  # reason: 순서 기록용 래핑
        order.append("verify")
        return original_verify(*a, **k)

    monkeypatch.setattr(cli, "run_catalog", fake_catalog)
    monkeypatch.setattr(cli, "_run_verify", spy_verify)
    assert cli.main([*_base(tmp_path), "sync", "--no-space-check"]) == cli.EXIT_OK
    assert order == ["catalog", "verify"]
    last = json.loads((tmp_path / "equity" / "_sync" / "last_run.json").read_text(encoding="utf-8"))
    assert last["catalog_rc"] == 0 and last["verify"]["ok"]


def test_offline_with_other_levels_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main([*_base(tmp_path), "verify", "--offline", "--level", "manifest"])
    assert e.value.code == 2


def test_accept_new_appends_one_line_without_rewriting_known_hosts(tmp_path: Path) -> None:
    from ledger_sync.remote import append_known_host

    known = tmp_path / "known_hosts"
    known.write_text("# comment\n@revoked host ssh-rsa AAAA\n", encoding="utf-8")
    append_known_host(known, TEST_HOST, "ssh-ed25519", "AAAAC3")
    assert known.read_text(encoding="utf-8") == (
        f"# comment\n@revoked host ssh-rsa AAAA\n{TEST_HOST} ssh-ed25519 AAAAC3\n")


def test_sync_records_failure_when_an_unexpected_exception_escapes(remote: FakeRemote,
                                                                   tmp_path: Path,
                                                                   monkeypatch) -> None:
    def explode(*a, **k):  # noqa: ANN002, ANN003, ANN202  # reason: 예상 밖 예외 주입
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(cli, "_run_pull", explode)
    with pytest.raises(ZeroDivisionError):
        cli.main([*_base(tmp_path), "sync", "--no-space-check", "--skip-catalog"])
    last = json.loads((tmp_path / "equity" / "_sync" / "last_run.json").read_text(encoding="utf-8"))
    assert last["exit_code"] == cli.EXIT_ERROR and "ZeroDivisionError" in last["error"]


def test_status_remote_labels_broken_remote_manifests(remote: FakeRemote, tmp_path: Path,
                                                      capsys) -> None:
    cli.main([*_base(tmp_path), "pull", "--no-space-check"])
    capsys.readouterr()
    remote.files[f"{REMOTE}/security/MANIFEST.json"] = b"{broken"
    assert cli.main([*_base(tmp_path), "--json", "status", "--remote"]) == cli.EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["behind"] == [] and "security" in status["remote_errors"]


def test_lock_rejects_a_concurrent_pull_and_is_released_after(remote: FakeRemote, tmp_path: Path,
                                                                capsys) -> None:
    lock = tmp_path / "equity" / "_sync" / "lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": 1, "started_at_utc": "now"}), encoding="utf-8")
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check"]) == cli.EXIT_ERROR
    assert "holds the lock" in capsys.readouterr().err
    # sync 는 락에 막혀도 last_run.json 에 실패를 남겨 status 가 낡은 성공을 보고하지 않는다
    rc = cli.main([*_base(tmp_path), "sync", "--no-space-check", "--skip-catalog"])
    assert rc == cli.EXIT_ERROR
    last = json.loads((tmp_path / "equity" / "_sync" / "last_run.json").read_text(encoding="utf-8"))
    assert last["exit_code"] == cli.EXIT_ERROR and "holds the lock" in last["error"]
    capsys.readouterr()
    assert cli.main([*_base(tmp_path), "status"]) == cli.EXIT_OK
    assert "lock: held pid=1" in capsys.readouterr().out
    # --break-lock 은 강제 회수, 정상 종료 뒤에는 락이 없다
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check", "--break-lock"]) == cli.EXIT_OK
    assert "reclaiming" in capsys.readouterr().out
    assert not lock.exists()


def test_stale_lock_from_a_dead_run_is_reclaimed(remote: FakeRemote, tmp_path: Path,
                                                 capsys) -> None:
    import os

    lock = tmp_path / "equity" / "_sync" / "lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": 99999, "started_at_utc": "old"}), encoding="utf-8")
    old = lock.stat().st_mtime - cli.LOCK_STALE_S - 60
    os.utime(lock, (old, old))
    assert cli.lock_info(tmp_path / "equity")["stale"] is True
    assert cli.main([*_base(tmp_path), "pull", "--no-space-check"]) == cli.EXIT_OK
    assert "reclaiming stale lock" in capsys.readouterr().out
    assert not lock.exists()


def test_known_hosts_append_adds_missing_trailing_newline(tmp_path: Path) -> None:
    from ledger_sync.remote import append_known_host

    known = tmp_path / "known_hosts"
    known.write_bytes(b"host1 ssh-rsa AAAA")   # 개행 없이 끝난 파일
    append_known_host(known, "host2", "ssh-ed25519", "BBBB")
    assert known.read_text(encoding="utf-8") == "host1 ssh-rsa AAAA\nhost2 ssh-ed25519 BBBB\n"


def test_log_rotation_keeps_the_newest_by_mtime(tmp_path: Path) -> None:
    import os

    log_dir = tmp_path / "equity" / "_sync" / "logs"
    log_dir.mkdir(parents=True)
    names = [f"sync_{i:03d}.log" for i in range(cli.LOG_KEEP + 3)]
    for i, name in enumerate(names):
        path = log_dir / name
        path.write_text("x", encoding="utf-8")
        os.utime(path, (1_000_000 - i, 1_000_000 - i))   # 이름순과 반대로 오래된 mtime
    cli._rotate_logs(tmp_path / "equity")
    left = sorted(p.name for p in log_dir.glob("*.log"))
    assert len(left) == cli.LOG_KEEP and "sync_000.log" in left and "sync_062.log" not in left


def test_remote_verbs_fail_loudly_without_a_host(remote: FakeRemote, tmp_path: Path, capsys,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """서버 주소는 기본값이 없다 — 빠지면 접속을 시도하지 않고 무엇을 설정할지 말하며 끝난다."""
    monkeypatch.delenv("QL_SYNC_HOST", raising=False)
    no_host = ["--root", str(tmp_path), "--layer", "equity"]
    assert cli.main([*no_host, "plan"]) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "QL_SYNC_HOST" in err and "--host" in err


def test_local_verbs_do_not_need_a_host(tmp_path: Path, capsys,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """status 는 로컬 상태만 읽는다 — 서버 주소 없이도 돌아야 한다."""
    monkeypatch.delenv("QL_SYNC_HOST", raising=False)
    no_host = ["--root", str(tmp_path), "--layer", "equity"]
    assert cli.main([*no_host, "--json", "status"]) == cli.EXIT_OK

