"""scripts/notify.sh — 텔레그램 로그 알림 끔(2026-10-01 사용자 지시, 결정 Q-4 병합 GM-a ③).

기본값(`QL_NOTIFY_TELEGRAM` 없음)이면 crit·warn·info 어느 등급도 텔레그램으로 보내지 않고 `logs/notify.log` 에 한 줄만
남긴다. 진짜 notify.sh 를 임시 `QL_HOME`·`HOME` 위에서 돌리고, 가짜 `.env`(토큰·채팅 id 가 들어 있다)를
`QL_ENV` 로 준다 — 끔 분기가 토큰이 있어도 보내지 않는지를 본다. PATH 맨 앞의 가짜 `curl` 은 불릴
때마다 인자를 파일에 적는다. flock 이 필요 없어 맥에서도 돈다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "notify.sh"

# 가짜 curl — 호출 하나를 한 줄로 남기고(본문 인자의 줄바꿈은 공백으로) 실패 응답을 낸다. 성공 응답을
# 내면 notify.sh 가 쿨다운 파일을 /tmp 에 만들기 때문에(대조 테스트가 저장소 밖을 건드리지 않게) 실패
# 응답으로 둔다.
_FAKE_CURL = """#!/usr/bin/env bash
args="$*"
printf '%s\\n' "${args//$'\\n'/ }" >> "$FAKE_CURL_LOG"
printf '{"ok":false}'
"""


def _run(tmp_path: Path, *, telegram: bool, level: str = "crit",
         ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    home = tmp_path / "home"
    ql_home = tmp_path / "ql"
    bin_dir = tmp_path / "bin"
    for d in (home, ql_home, bin_dir):
        d.mkdir()
    env_file = tmp_path / "fake.env"
    env_file.write_text("BOT_TOKEN=fake-token\nCHAT_ID_LOG=12345\n", encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    curl_log = tmp_path / "curl_calls.txt"
    env = {k: v for k, v in os.environ.items() if k != "QL_NOTIFY_TELEGRAM"}
    env.update(HOME=str(home), QL_HOME=str(ql_home), QL_ENV=str(env_file),
               FAKE_CURL_LOG=str(curl_log), PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}")
    if telegram:
        env["QL_NOTIFY_TELEGRAM"] = "1"
    proc = subprocess.run(["bash", str(SCRIPT), level, "병합 시험", "본문 첫 줄\n둘째 줄"],
                          env=env, capture_output=True, text=True, timeout=60, check=False)
    return proc, ql_home / "logs" / "notify.log", curl_log


@pytest.mark.parametrize("level", ["crit", "warn", "info"])
def test_기본값은_어느_등급도_텔레그램을_부르지_않고_notify_log_에_한_줄만_남긴다(
        tmp_path: Path, level: str) -> None:
    proc, notify_log, curl_log = _run(tmp_path, telegram=False, level=level)
    assert proc.returncode == 0, proc.stderr
    lines = notify_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].split(" ", 1)[1] == f"{level} 병합 시험 | 본문 첫 줄 둘째 줄"
    assert not curl_log.exists()          # 가짜 curl 호출 0
    assert "logged only" in proc.stdout


def test_대조_켜면_가짜_curl_이_한_번_불린다(tmp_path: Path) -> None:
    """대역이 실제로 curl 을 가로채는지 확인한다 — 이것이 없으면 위 테스트의 '호출 0' 이 공허할 수 있다."""
    proc, notify_log, curl_log = _run(tmp_path, telegram=True)
    assert proc.returncode == 1               # 가짜 curl 이 실패 응답을 낸다
    calls = curl_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and "api.telegram.org/botfake-token/sendMessage" in calls[0]
    assert not notify_log.exists()
