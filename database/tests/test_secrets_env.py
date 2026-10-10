"""비밀 파일 이관(RG-C7-4) — quant-ledger 는 비밀을 `QL_ENV` 하나에서만 읽는다.

- `import api` 는 QL_ENV 가 비었거나 파일이 없으면 FileNotFoundError 다. HOME 에 옛 시스템
  폴더의 비밀 파일(더미)을 일부러 두어도 그쪽으로 넘어가지 않아야 한다 — 옛 후보 순회가 살아
  있으면 이 대조가 깨진다.
- 크론이 부르는 체인 진입점은 시작부에서 `QL_ENV="$HOME/quant-ledger/.env"` 를 고정한다
  (크론 줄 무변경).
- 보조: src·scripts 어느 줄에도 옛 시스템 폴더 이름과 `.env` 가 함께 나오지 않는다.

실제 비밀 파일은 열지 않는다. 쓰는 파일은 전부 tmp_path 아래 더미다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

DB_ROOT = Path(__file__).resolve().parents[1]
SRC = DB_ROOT / "src"
SCRIPTS = DB_ROOT / "scripts"
OLD = "kael-system-v3"                 # 옛 시스템 폴더 이름
FIXED = 'export QL_ENV="$HOME/quant-ledger/.env"'

# 크론이 부르는 진입점(README '운영 (P6)' 크론 원문·제안 블록) + 손 발송 진입점 model_daily.sh
# (README: 손 발송은 `scripts/model_daily.sh --date D` — 체인과 같은 환경으로 돌아야 한다).
ENTRY_POINTS = ("daily_ledger.sh", "daily_build.sh", "daily_evening.sh", "build_evening.sh",
                "watchdog.sh", "backup_raw.sh", "gc.sh", "wics_weekly.sh", "postclose_chain.sh",
                "model_daily.sh")


def _sentinel_home(tmp_path: Path) -> Path:
    """옛 시스템 폴더에 더미 비밀 파일을 둔 HOME — 폴백이 살아 있으면 import 가 성공해 버린다."""
    home = tmp_path / "home"
    dummy = "KRX_API_KEY=old-dummy\nDART_API_KEY=old-prod-dummy\n"
    for d in (home / OLD, home / "Desktop" / OLD):
        d.mkdir(parents=True)
        (d / ".env").write_text(dummy, encoding="utf-8")
    return home


def _import_api(tmp_path: Path, ql_env: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "QL_ENV"}
    env.update(HOME=str(_sentinel_home(tmp_path)), PYTHONPATH=str(SRC))
    if ql_env is not None:
        env["QL_ENV"] = ql_env
    code = "import api; print(api.ENV); print(api._K['KRX_API_KEY'])"
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.parametrize("ql_env", [None, ""])
def test_api_import_fails_without_ql_env(tmp_path: Path, ql_env: str | None) -> None:
    p = _import_api(tmp_path, ql_env)
    assert p.returncode != 0
    assert "FileNotFoundError" in p.stderr and "QL_ENV" in p.stderr
    assert "old-dummy" not in p.stdout


def test_api_import_fails_when_ql_env_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "none.env"
    p = _import_api(tmp_path, str(missing))
    assert p.returncode != 0
    assert "FileNotFoundError" in p.stderr and str(missing) in p.stderr


def test_api_import_reads_ql_env_only(tmp_path: Path) -> None:
    f = tmp_path / "ql.env"
    f.write_text("KRX_API_KEY=ql-dummy\nKRX_ID=id\nKRX_PW=pw\n", encoding="utf-8")
    p = _import_api(tmp_path, str(f))
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [str(f), "ql-dummy"]


def _code_lines(path: Path) -> list[tuple[int, str]]:
    return [(i, ln) for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines())
            if ln.strip() and not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_entry_point_pins_ql_env_before_first_use(name: str) -> None:
    lines = _code_lines(SCRIPTS / name)
    pins = [i for i, ln in lines if ln.split("#", 1)[0].strip() == FIXED]
    assert len(pins) == 1, f"{name}: 최상위 `{FIXED}` 줄이 정확히 하나여야 한다"
    assert not (SCRIPTS / name).read_text(encoding="utf-8").splitlines()[pins[0]].startswith(" ")
    use = re.compile(r"\.venv/bin/python|\$PY\b|\$\{PY\}|scripts/")
    first = next(i for i, ln in lines if use.search(ln))
    assert pins[0] < first, f"{name}: QL_ENV 고정이 첫 python·스크립트 호출보다 뒤에 있다"


def test_scripts_assign_ql_env_only_to_fixed_path() -> None:
    """기본 경로를 단 `${QL_ENV:-<경로>}` 나 다른 경로 대입이 없다 — 대입은 고정 줄뿐이다
    (`${QL_ENV:-}` 처럼 빈 기본값으로 비었는지 보는 것은 된다)."""
    bad = [f"{p.name}:{i + 1}: {ln.strip()}" for p in sorted(SCRIPTS.glob("*.sh"))
           for i, ln in _code_lines(p)
           if re.search(r"\bQL_ENV=|\$\{QL_ENV:?[-=][^}]", ln)
           and ln.split("#", 1)[0].strip() != FIXED]
    assert bad == []


def test_readme_cron_scripts_are_entry_points() -> None:
    """README 크론 원문(```cron 블록)이 부르는 scripts/*.sh 가 전부 ENTRY_POINTS 에 있다
    — 새 크론 진입점 누락 방지."""
    text = (DB_ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```cron\n(.*?)```", text, flags=re.S)
    assert blocks
    called = {m for b in blocks for m in re.findall(r"scripts/([\w.]+\.sh)", b)}
    assert called and called <= set(ENTRY_POINTS), called - set(ENTRY_POINTS)


def test_no_old_system_env_reference_in_src_and_scripts() -> None:
    """보조 검사 — 한 줄에 옛 시스템 폴더 이름과 `.env`(`.environ` 제외)가 함께 나오면 실패."""
    env_pat = re.compile(r"\.env(?![A-Za-z_])")
    hits = []
    for root in (SRC, SCRIPTS):
        for p in sorted(root.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.name.startswith("."):
                continue
            for i, ln in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines()):
                if OLD in ln and env_pat.search(ln):
                    hits.append(f"{p.relative_to(DB_ROOT)}:{i + 1}")
    assert hits == []
