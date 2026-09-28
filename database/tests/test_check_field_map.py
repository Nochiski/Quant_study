"""`check_field_map.py` — 대응표(EQUITY_FIELD_MAP §2)가 레지스트리(backend/FACTORS.md) 요구 필드를
전부 덮는지 보는 검사기. 대응표 머리말의 규약("레지스트리가 바뀌면 같은 PR 에서 갱신")을 CI 가
지키게 하는 유일한 장치다 — #218 이 레지스트리 가격 팩터를 `price.adj_close` 로 옮기고 대응표를
두고 가 이 검사가 조용히 rc=1 이었다(문서 감사 결정 5).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "database" / "scripts"))

import check_field_map  # scripts/ 는 패키지가 아니라 경로를 올린 뒤 읽는다

REGISTRY = _REPO / "backend" / "FACTORS.md"
FIELD_MAP = _REPO / "database" / "docs" / "EQUITY_FIELD_MAP.md"


def _run(monkeypatch: pytest.MonkeyPatch, registry: Path, field_map: Path) -> int:
    monkeypatch.setattr(sys, "argv", ["check_field_map.py", "--registry", str(registry),
                                      "--map", str(field_map)])
    return check_field_map.main()


def test_저장소의_대응표는_레지스트리_요구_필드를_전부_덮는다(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rc = _run(monkeypatch, REGISTRY, FIELD_MAP)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "MISSING" not in out


def test_레지스트리의_수정주가는_대응표_어휘다() -> None:
    """가격 변화 팩터가 읽는 `price.adj_close` 는 레지스트리 요구 필드라 §2 에 행이 있어야 한다."""
    assert "price.adj_close" in check_field_map.registry_fields(REGISTRY)
    assert "price.adj_close" in check_field_map.map_fields(FIELD_MAP)


def test_대응표에_없는_레지스트리_필드는_1_로_끝난다(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    registry = tmp_path / "FACTORS.md"
    registry.write_text("| 1 | `price.momentum_12_1` | price | high | `price.adj_close` | 273 |\n",
                        encoding="utf-8")
    field_map = tmp_path / "MAP.md"
    field_map.write_text("## 2. field_id 대응\n\n| `price.close` | x | 지원 | |\n\n## 3. 끝\n",
                         encoding="utf-8")

    assert _run(monkeypatch, registry, field_map) == 1
    assert "MISSING in map: price.adj_close" in capsys.readouterr().out


def test_파일이_없으면_2_로_끝난다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, tmp_path / "none.md", FIELD_MAP) == 2
