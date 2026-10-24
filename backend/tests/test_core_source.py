"""Rust 코어 소스 지문 규칙과 낡은 확장 판정."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtest_engine.engine import core, core_source
from backtest_engine.errors import CoreUnavailable


def _crate(root: Path) -> Path:
    (root / ".git").mkdir()
    crate = root / "backend" / "rust" / "backtest_core"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (crate / "src" / "lib.rs").write_text("fn a() {}\n", encoding="utf-8")
    return crate


def _keys(crate: Path) -> list[str]:
    return [key for key, _ in core_source.source_inputs(crate)]


def test_digest_covers_build_inputs_and_ancestor_settings_up_to_repo_root(
    tmp_path: Path,
) -> None:
    crate = _crate(tmp_path)
    assert _keys(crate) == ["Cargo.toml", "src/lib.rs"]
    (tmp_path / "rust-toolchain.toml").write_text("[toolchain]\n", encoding="utf-8")
    (tmp_path / "backend" / ".cargo").mkdir()
    (tmp_path / "backend" / ".cargo" / "config.toml").write_text("", encoding="utf-8")
    (crate / ".cargo").mkdir()
    (crate / ".cargo" / "config.toml").write_text("", encoding="utf-8")
    (crate / "build.rs").write_text("fn main() {}\n", encoding="utf-8")
    # 저장소 루트 밖 파일과 빌드 입력이 아닌 crate 루트 파일·디렉터리는 보지 않는다.
    (tmp_path.parent / "rust-toolchain").write_text("x", encoding="utf-8")
    (crate / "README.md").write_text("docs", encoding="utf-8")
    (crate / "tests").mkdir()
    (crate / "tests" / "t.rs").write_text("x", encoding="utf-8")
    try:
        keys = _keys(crate)
    finally:
        (tmp_path.parent / "rust-toolchain").unlink()
    assert keys == [
        "../../../rust-toolchain.toml",
        "../../.cargo/config.toml",
        ".cargo/config.toml",
        "Cargo.toml",
        "build.rs",
        "src/lib.rs",
    ]


def test_digest_skips_editor_and_temporary_files(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    before = core_source.source_digest(crate)
    junk = (
        "target/out.rs",
        "src/.lib.rs.swp",
        "src/.lib.rs.swo",
        "src/4913",
        "src/.#lib.rs",
        "src/lib.rs~",
        "src/x.tmp",
        "src/.DS_Store",
        "src/.idea/x.xml",
        "src/.vscode/s.json",
    )
    for relative in junk:
        path = crate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("junk", encoding="utf-8")
    assert core_source.source_digest(crate) == before
    # src 아래의 target 이름 디렉터리는 소스라 지문에 든다.
    (crate / "src" / "target").mkdir()
    (crate / "src" / "target" / "mod.rs").write_text("x", encoding="utf-8")
    assert core_source.source_digest(crate) != before


@pytest.mark.skipif(sys.platform == "win32", reason="심볼릭 링크 권한이 OS 설정에 달렸다")
def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    before = core_source.source_digest(crate)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.rs").write_text("x", encoding="utf-8")
    os.symlink(outside, crate / "src" / "linked")
    os.symlink(outside / "x.rs", crate / "src" / "linked.rs")
    os.symlink(tmp_path / "missing.rs", crate / "src" / "broken.rs")
    assert core_source.source_digest(crate) == before


def test_one_byte_change_changes_the_digest(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    before = core_source.source_digest(crate)
    (crate / "src" / "lib.rs").write_text("fn b() {}\n", encoding="utf-8")
    assert core_source.source_digest(crate) != before
    assert len(before) == 32


def _module(monkeypatch: pytest.MonkeyPatch, **attributes: str) -> SimpleNamespace:
    module = SimpleNamespace(**attributes)
    monkeypatch.setattr(core_source.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(core_source.importlib, "import_module", lambda _: module)
    return module


def test_status_compares_digest_and_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    crate = _crate(tmp_path)
    digest = core_source.source_digest(crate)
    module = _module(monkeypatch, SOURCE_DIGEST=digest, BUILD_PROFILE="release")
    assert core_source.core_status(crate) == "fresh"
    module.BUILD_PROFILE = "debug"
    assert core_source.core_status(crate) == "debug"
    module.SOURCE_DIGEST = "0" * 32
    assert core_source.core_status(crate) == "stale"
    del module.SOURCE_DIGEST
    assert core_source.core_status(crate) == "stale"
    monkeypatch.setattr(core_source.importlib.util, "find_spec", lambda _: None)
    assert core_source.core_status(crate) == "missing"


def test_partial_source_tree_is_not_compared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    crate = _crate(tmp_path)
    _module(monkeypatch, SOURCE_DIGEST="x", BUILD_PROFILE="release")
    (crate / "src" / "lib.rs").unlink()
    assert core_source.core_status(crate) == "unchecked"
    assert core_source.core_status(tmp_path / "none") == "unchecked"


def test_unreadable_source_counts_as_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    _module(monkeypatch, SOURCE_DIGEST="x", BUILD_PROFILE="release")

    def vanished(_: Path) -> str:
        raise FileNotFoundError("saved mid-read")

    monkeypatch.setattr(core_source, "source_digest", vanished)
    assert core_source.core_status(crate) == "stale"


def test_process_status_is_decided_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def status() -> str:
        calls.append(1)
        return "fresh"

    core_source.process_core_status.cache_clear()
    monkeypatch.setattr(core_source, "core_status", status)
    try:
        assert core_source.process_core_status() == "fresh"
        assert core_source.process_core_status() == "fresh"
        assert len(calls) == 1
    finally:
        core_source.process_core_status.cache_clear()


def test_stale_core_is_unavailable_with_restart_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "process_core_status", lambda: "stale")
    assert not core.core_available("rust_persistent")
    assert core.core_available("python")
    with pytest.raises(CoreUnavailable, match="then restart the server"):
        core._require_core("rust_persistent")  # pyright: ignore[reportPrivateUsage]  # reason: 판정 경계 직접 시험
    monkeypatch.setattr(core, "process_core_status", lambda: "missing")
    with pytest.raises(CoreUnavailable, match="not installed"):
        core._require_core("rust")  # pyright: ignore[reportPrivateUsage]  # reason: 판정 경계 직접 시험
    # 디버그 빌드는 느리지만 같은 결과라 쓴다(경고는 판정 때 한 번 남긴다).
    monkeypatch.setattr(core, "process_core_status", lambda: "debug")
    assert core.core_available("rust")


def test_status_command_prints_one_word(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_source, "core_status", lambda: "stale")
    assert core_source.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "stale"
    assert core_source.main(["other"]) == 2


@pytest.mark.skipif(
    importlib.util.find_spec("backtest_core") is None, reason="backtest_core 확장 없음"
)
def test_rust_build_script_and_python_rule_give_the_same_digest() -> None:
    """계약: build.rs가 구운 지문과 Python 정본이 같은 소스에서 같은 값을 낸다."""
    import backtest_core

    assert backtest_core.SOURCE_DIGEST == core_source.source_digest(), (
        "Rust 코어가 현재 소스보다 오래됐거나 build.rs와 core_source.py의 규칙이 어긋났다 — "
        f"다시 빌드: {core_source.BUILD_COMMAND}"
    )
    assert backtest_core.BUILD_PROFILE in ("release", "debug")
