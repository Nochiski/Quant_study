"""Rust 코어 소스 지문 규칙과 낡은 확장 판정."""

from __future__ import annotations

import importlib.util
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


def test_digest_covers_crate_and_ancestor_settings_up_to_repo_root(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    keys = [key for key, _ in core_source.source_inputs(crate)]
    assert keys == ["Cargo.toml", "src/lib.rs"]
    (tmp_path / "rust-toolchain.toml").write_text("[toolchain]\n", encoding="utf-8")
    (tmp_path / "backend" / ".cargo").mkdir()
    (tmp_path / "backend" / ".cargo" / "config.toml").write_text("", encoding="utf-8")
    # 저장소 루트 밖 파일은 보지 않는다.
    (tmp_path.parent / "rust-toolchain").write_text("x", encoding="utf-8")
    try:
        keys = [key for key, _ in core_source.source_inputs(crate)]
    finally:
        (tmp_path.parent / "rust-toolchain").unlink()
    assert keys == [
        "../../../rust-toolchain.toml",
        "../../.cargo/config.toml",
        "Cargo.toml",
        "src/lib.rs",
    ]


def test_digest_skips_top_level_target_and_editor_files_only(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    before = core_source.source_digest(crate)
    for relative in (
        "target/out.rs",
        ".idea/x.xml",
        ".vscode/s.json",
        "src/.lib.rs.swp",
        ".DS_Store",
    ):
        path = crate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("junk", encoding="utf-8")
    assert core_source.source_digest(crate) == before
    # 하위의 target 이름 디렉터리는 소스일 수 있어 지문에 든다.
    (crate / "src" / "target").mkdir()
    (crate / "src" / "target" / "mod.rs").write_text("x", encoding="utf-8")
    assert core_source.source_digest(crate) != before


def test_one_byte_change_changes_the_digest(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    before = core_source.source_digest(crate)
    (crate / "src" / "lib.rs").write_text("fn b() {}\n", encoding="utf-8")
    assert core_source.source_digest(crate) != before
    assert len(before) == 32


def test_status_compares_the_baked_digest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    digest = core_source.source_digest(crate)
    module = SimpleNamespace(SOURCE_DIGEST=digest)
    monkeypatch.setattr(core_source.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(core_source.importlib, "import_module", lambda _: module)
    assert core_source.core_status(crate) == "fresh"
    module.SOURCE_DIGEST = "0" * 32
    assert core_source.core_status(crate) == "stale"
    del module.SOURCE_DIGEST
    assert core_source.core_status(crate) == "stale"
    # 소스 트리가 없는 배포 환경은 비교하지 않는다.
    assert core_source.core_status(tmp_path / "none") == "unchecked"
    monkeypatch.setattr(core_source.importlib.util, "find_spec", lambda _: None)
    assert core_source.core_status(crate) == "missing"


def test_stale_core_is_unavailable_with_an_explicit_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "core_status", lambda: "stale")
    assert not core.core_available("rust_persistent")
    assert core.core_available("python")
    with pytest.raises(CoreUnavailable, match="older than the current Rust source"):
        core._require_core("rust_persistent")  # pyright: ignore[reportPrivateUsage]  # reason: 판정 경계 직접 시험
    monkeypatch.setattr(core, "core_status", lambda: "missing")
    with pytest.raises(CoreUnavailable, match="not installed"):
        core._require_core("rust")  # pyright: ignore[reportPrivateUsage]  # reason: 판정 경계 직접 시험


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

    assert backtest_core.SOURCE_DIGEST == core_source.source_digest()
