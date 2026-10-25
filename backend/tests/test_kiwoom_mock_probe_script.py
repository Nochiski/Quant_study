"""`scripts/kiwoom_mock_probe.py`의 안전장치. 네트워크 대신 가짜 키움 서버를 쓴다(S06 ③)."""

from __future__ import annotations

import stat
import sys
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

from strategy_workbench.adapters.outbound.secrets_local.facade.store import (
    LocalFileBrokerCredentialStore,
    LocalFileProviderSecretStore,
)
from strategy_workbench.application.broker_connection.facade.connection import BrokerCredentials
from strategy_workbench.bootstrap.facade.http import ASSISTANT_SECRETS_PATH_ENV
from tests.test_adapters_kiwoom_mock import ACCOUNT, APP_KEY, SECRET, Clock, FakeKiwoom

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="module")
def probe() -> Iterator[ModuleType]:
    """`scripts/`는 패키지가 아니라 실행 스크립트 디렉터리라 sys.path에 없다."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        yield import_module("kiwoom_mock_probe")
    finally:
        sys.path.remove(str(_SCRIPTS_DIR))


@pytest.fixture
def saved_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "settings" / "secrets.json"
    monkeypatch.setenv(ASSISTANT_SECRETS_PATH_ENV, str(path))
    LocalFileBrokerCredentialStore(LocalFileProviderSecretStore(path)).save(
        BrokerCredentials(APP_KEY, SECRET, ACCOUNT)
    )


def _run(probe: ModuleType, fake: FakeKiwoom, clock: Clock, *argv: str) -> int:
    return probe.run(
        list(argv),
        transport=fake,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


ORDER = ("--symbol", "005930", "--quantity", "1", "--max-orders", "1")


@pytest.mark.usefixtures("saved_keys")
def test_orders_without_send_only_preview(probe: ModuleType, tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    out = str(tmp_path / "out")
    assert (
        _run(probe, fake, clock, "--out", out, "buy", "--intent", "b1", "--price", "70000", *ORDER)
        == 0
    )
    assert _run(probe, fake, clock, "--out", out, "cancel", "--intent", "b1") == 0
    assert fake.calls == []


@pytest.mark.usefixtures("saved_keys")
def test_max_orders_stops_the_next_send(probe: ModuleType, tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    out = str(tmp_path / "out")
    assert _run(probe, fake, clock, "--out", out, "init") == 0
    send = ("--price", "70000", *ORDER, "--send")
    assert _run(probe, fake, clock, "--out", out, "buy", "--intent", "b1", *send) == 0
    assert _run(probe, fake, clock, "--out", out, "buy", "--intent", "b2", *send) == 2
    assert fake.api_ids().count("kt10000") == 1


@pytest.mark.usefixtures("saved_keys")
def test_out_folder_inside_the_repository_is_refused(probe: ModuleType) -> None:
    clock = Clock()
    with pytest.raises(SystemExit):
        _run(probe, FakeKiwoom(clock), clock, "--out", str(_SCRIPTS_DIR / "out"), "status")


def test_missing_keys_stop_before_calling_kiwoom(
    probe: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ASSISTANT_SECRETS_PATH_ENV, str(tmp_path / "empty" / "secrets.json"))
    clock = Clock()
    fake = FakeKiwoom(clock)
    with pytest.raises(SystemExit):
        _run(probe, fake, clock, "--out", str(tmp_path / "out"), "status")
    assert fake.calls == []


@pytest.mark.usefixtures("saved_keys")
def test_resume_lifts_the_halt_only_when_reconciled(probe: ModuleType, tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeKiwoom(clock)
    out = str(tmp_path / "out")
    assert _run(probe, fake, clock, "--out", out, "init") == 0
    fake.cash -= 1  # 장부에 없는 현금 변화
    assert _run(probe, fake, clock, "--out", out, "recover") == 1
    assert _run(probe, fake, clock, "--out", out, "resume") == 1
    fake.cash += 1
    assert _run(probe, fake, clock, "--out", out, "resume") == 0
    send = ("--price", "70000", *ORDER, "--send")
    assert _run(probe, fake, clock, "--out", out, "buy", "--intent", "b1", *send) == 0
    assert fake.api_ids().count("kt10000") == 1


@pytest.mark.usefixtures("saved_keys")
def test_measurement_files_are_private_even_in_an_existing_folder(
    probe: ModuleType, tmp_path: Path
) -> None:
    # 리뷰 #596 P3: 실제로 600 인 것은 조회 원본뿐이었고 원장 두 파일은 umask 를 따랐다
    out = tmp_path / "out"
    out.mkdir(mode=0o755)
    out.chmod(0o755)
    clock = Clock()
    assert _run(probe, FakeKiwoom(clock), clock, "--out", str(out), "status") in (0, 1)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for name in ("kiwoom-reference.sqlite", "execution.sqlite"):
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o600

