"""scripts/equity_rebuild_all.sh — 패스 실패 뒤 롤백·카탈로그 재생성, before.json 실패 시 시작 안 함
(배포 묶음 4 갈래 4-1 C-01 품질 검토).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역 `.venv/bin/python`·`scripts/run_equity.sh` 를 두고
진짜 스크립트를 돌린다. 대역 python 은 호출을 `calls.txt` 에 적고 `-m equity rollback|catalog` 는
RC_ROLLBACK·RC_CATALOG 로 끝낸다. before.json heredoc 은 진짜 python 이 저장소 `src` 로 돌린다
(RC_BEFORE 를 주면 그 rc 로 실패한다). 빌드 락은 QL_BUILD_LOCK_HELD=1 로 건너뛴다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DB = Path(__file__).resolve().parents[1]
SCRIPT = DB / "scripts" / "equity_rebuild_all.sh"
TABLES = ("trading_calendar", "price_daily", "corp_event")

_PY = """#!/usr/bin/env bash
echo "py $*" >> "$QL_CALLS"
if [ "$1" = "-" ]; then
  code=$(cat)
  if [ -n "${RC_BEFORE:-}" ]; then exit "$RC_BEFORE"; fi
  shift
  exec "$REAL_PY" - "$@" <<<"$code"
fi
if [ "$1" = "scripts/equity_manifest_row.py" ]; then echo "1:x"; exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "equity" ]; then
  case " $* " in
    *" rollback "*) exit "${RC_ROLLBACK:-0}" ;;
    *" catalog "*) exit "${RC_CATALOG:-0}" ;;
  esac
fi
exit 0
"""
_RUN_EQUITY = """#!/usr/bin/env bash
echo "build $1" >> "$QL_CALLS"
if [ "$1" = "${FAIL_TABLE:-}" ]; then exit 7; fi
exit 0
"""


def _root(home: Path) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", ".venv/bin", "data/equity"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "equity_rebuild_all.sh")
    (root / "scripts" / "equity_order.txt").write_text("\n".join(TABLES) + "\n", encoding="utf-8")
    (root / "src").symlink_to(DB / "src")
    for rel, body in ((".venv/bin/python", _PY), ("scripts/run_equity.sh", _RUN_EQUITY)):
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _run(home: Path, basis: str = "morning", **env: str) -> tuple[subprocess.CompletedProcess[str],
                                                                   list[str]]:
    root = _root(home)
    calls = home / "calls.txt"
    calls.touch()
    e = dict(os.environ, HOME=str(home), QL_BUILD_LOCK_HELD="1", REAL_PY=sys.executable,
             QL_CALLS=str(calls), **env)
    e.pop("QL_EQUITY_CONTINUE", None)
    p = subprocess.run(["bash", str(root / "scripts" / "equity_rebuild_all.sh"), "p1",
                        "--basis", basis],
                       env=e, capture_output=True, text=True, timeout=60, check=False)
    return p, calls.read_text(encoding="utf-8").splitlines()


def _equity_verbs(calls: list[str]) -> list[str]:
    return [c.split()[-1] if c.endswith(" catalog") else "rollback"
            for c in calls if c.startswith("py -m equity")]


@pytest.mark.parametrize("basis", ["morning", "evening"])
def test_롤백이_성공하면_catalog_를_한_번_돌린다(tmp_path: Path, basis: str) -> None:
    """롤백으로 포인터가 바뀌면 equity.duckdb 매크로·_catalog_meta 도 그 판으로 맞춘다 — 안 하면
    다음 성공 패스까지 매크로 소비자는 실패 전 판을, 워크벤치는 'catalog is stale' 을 본다."""
    p, calls = _run(tmp_path, basis, FAIL_TABLE="price_daily")
    assert p.returncode == 7, p.stdout + p.stderr
    assert _equity_verbs(calls) == ["rollback", "catalog"]
    assert "py -m equity catalog" in calls        # build_chain catalog_step 과 같은 모양
    assert [c for c in calls if c.startswith("build ")] == ["build trading_calendar",
                                                             "build price_daily"]


def test_롤백_뒤_catalog_가_실패해도_패스_rc_는_빌드_실패_그대로다(tmp_path: Path) -> None:
    p, calls = _run(tmp_path, FAIL_TABLE="price_daily", RC_CATALOG="5")
    assert p.returncode == 7, p.stdout + p.stderr
    assert _equity_verbs(calls) == ["rollback", "catalog"]
    assert "catalog 실패" in p.stdout


def test_롤백이_실패하면_catalog_는_돌리지_않는다(tmp_path: Path) -> None:
    p, calls = _run(tmp_path, FAIL_TABLE="price_daily", RC_ROLLBACK="1")
    assert p.returncode == 7, p.stdout + p.stderr
    assert _equity_verbs(calls) == ["rollback"]
    assert "rollback 실패" in p.stdout


def test_before_json_을_못_만들면_아무_표도_짓지_않고_멈춘다(tmp_path: Path) -> None:
    """되돌릴 기준이 없는 패스는 시작하지 않는다(P1)."""
    p, calls = _run(tmp_path, RC_BEFORE="1")
    assert p.returncode != 0
    assert [c for c in calls if c.startswith("build ")] == []
    assert "before.json" in p.stdout + p.stderr


def test_모든_표가_성공하면_롤백도_catalog_도_부르지_않는다(tmp_path: Path) -> None:
    """회귀 가드 — catalog 는 체인의 catalog_step 몫이다."""
    p, calls = _run(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert _equity_verbs(calls) == []
    assert [c for c in calls if c.startswith("build ")] == [f"build {t}" for t in TABLES]
