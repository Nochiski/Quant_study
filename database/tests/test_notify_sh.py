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


def _run(tmp_path: Path, *, telegram: bool, level: str = "crit", ql_env: bool = True,
         body: str = "본문 첫 줄\n둘째 줄", lc_all: str | None = None,
         ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """ql_env=False 면 QL_ENV 를 빼고, HOME 의 옛 시스템 폴더에 토큰이 든 더미 파일을
    둔다(폴백 대조)."""
    home = tmp_path / "home"
    ql_home = tmp_path / "ql"
    bin_dir = tmp_path / "bin"
    for d in (home, ql_home, bin_dir):
        d.mkdir()
    if not ql_env:
        (home / "kael-system-v3").mkdir()
        (home / "kael-system-v3" / ".env").write_text("BOT_TOKEN=old-token\nCHAT_ID_LOG=999\n",
                                                       encoding="utf-8")
    env_file = tmp_path / "fake.env"
    env_file.write_text("BOT_TOKEN=fake-token\nCHAT_ID_LOG=12345\n", encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    curl_log = tmp_path / "curl_calls.txt"
    env = {k: v for k, v in os.environ.items() if k not in ("QL_NOTIFY_TELEGRAM", "QL_ENV")}
    env.update(HOME=str(home), QL_HOME=str(ql_home),
               FAKE_CURL_LOG=str(curl_log), PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}")
    if ql_env:
        env["QL_ENV"] = str(env_file)
    if telegram:
        env["QL_NOTIFY_TELEGRAM"] = "1"
    if lc_all is not None:
        env["LC_ALL"] = lc_all
    proc = subprocess.run(["bash", str(SCRIPT), level, "병합 시험", body],
                          env=env, capture_output=True, text=True, errors="replace", timeout=60,
                          check=False)
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


@pytest.mark.parametrize("pad", [0, 1, 2])
def test_본문_900자_자르기는_C_로캘에서도_글자_중간을_끊지_않는다(tmp_path: Path, pad: int) -> None:
    """크론처럼 로캘이 C 이면 `${BODY:0:900}` 이 바이트로 잘린다 — notify.log 가 깨진 UTF-8 이 되지 않는다(#630).

    앞 ASCII 를 0·1·2 자로 바꿔 900바이트 경계가 3바이트 글자의 세 자리에 모두 떨어지게 한다.
    """
    proc, notify_log, _ = _run(tmp_path, telegram=False, lc_all="C", body="a" * pad + "가" * 400)
    assert proc.returncode == 0, proc.stderr
    line = notify_log.read_bytes().decode("utf-8")
    assert "가" * 100 in line


def test_대조_켜면_가짜_curl_이_한_번_불린다(tmp_path: Path) -> None:
    """대역이 실제로 curl 을 가로채는지 확인한다 — 이것이 없으면 위 테스트의 '호출 0' 이 공허할 수 있다."""
    proc, notify_log, curl_log = _run(tmp_path, telegram=True)
    assert proc.returncode == 1               # 가짜 curl 이 실패 응답을 낸다
    calls = curl_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and "api.telegram.org/botfake-token/sendMessage" in calls[0]
    assert len(notify_log.read_text(encoding="utf-8").splitlines()) == 1   # 보내는 날도 기록은 남는다(N-73)


@pytest.mark.parametrize("level", ["crit", "warn", "info"])
def test_기본값은_QL_ENV_없이도_rc_0_로그_한_줄(tmp_path: Path, level: str) -> None:
    """RG-C7-4 — 비밀 파일은 텔레그램 분기에서만 요구한다. 기본(로그만) 분기는 QL_ENV 없이
    돈다."""
    proc, notify_log, curl_log = _run(tmp_path, telegram=False, level=level, ql_env=False)
    assert proc.returncode == 0, proc.stderr
    assert len(notify_log.read_text(encoding="utf-8").splitlines()) == 1
    assert not curl_log.exists()


def test_텔레그램_분기는_QL_ENV_없으면_옛_파일로_넘어가지_않고_rc_2(tmp_path: Path) -> None:
    """RG-C7-4 — HOME 의 옛 시스템 폴더에 토큰이 있어도 읽지 않는다(옛 폴백이면 curl 이
    불려 rc 1)."""
    proc, notify_log, curl_log = _run(tmp_path, telegram=True, ql_env=False)
    assert proc.returncode == 2
    assert "QL_ENV" in proc.stderr
    assert not curl_log.exists()
    assert len(notify_log.read_text(encoding="utf-8").splitlines()) == 1   # 못 보내도 기록은 남는다(N-73)


# ── 서버 전용 설정(local/notify.env)이 고른 등급만 텔레그램으로(N-73 — 11-21 사용자 '실패도 ai playground 채널에') ──────
_FAKE_CURL_OK = _FAKE_CURL.replace('{"ok":false}', '{"ok":true}')
_CONF = "QL_NOTIFY_TELEGRAM_LEVELS=crit\nQL_NOTIFY_CHAT_VAR=CHAT_ID_AIPLAYGROUND\n"
_ENV = "BOT_TOKEN=fake-token\nCHAT_ID_LOG=12345\nCHAT_ID_AIPLAYGROUND=-100777\n"


class Box:
    """임시 QL_HOME 하나 — 같은 상자에서 notify.sh 를 여러 번 부른다(쿨다운 · 로그 줄 수)."""

    def __init__(self, tmp_path: Path, *, conf: str | None = _CONF, env_text: str = _ENV, curl_ok: bool = True):
        self.home, self.ql, bin_dir = tmp_path / "home", tmp_path / "ql", tmp_path / "bin"
        self.stamps = tmp_path / "stamps"
        for d in (self.home, self.ql, bin_dir, self.stamps):
            d.mkdir()
        if conf is not None:
            (self.ql / "local").mkdir()
            (self.ql / "local" / "notify.env").write_text(conf, encoding="utf-8")
        self.env_file = tmp_path / "fake.env"
        self.env_file.write_text(env_text, encoding="utf-8")
        curl = bin_dir / "curl"
        curl.write_text(_FAKE_CURL_OK if curl_ok else _FAKE_CURL, encoding="utf-8")
        curl.chmod(0o755)
        self.curl_log = tmp_path / "curl_calls.txt"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("QL_")}
        self.env.update(HOME=str(self.home), QL_HOME=str(self.ql), QL_ENV=str(self.env_file),
                        QL_NOTIFY_STAMP_DIR=str(self.stamps), FAKE_CURL_LOG=str(self.curl_log),
                        PATH=f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}")

    def call(self, level: str, title: str = "daily_build 실패: build_morning(rc=2)",
             body: str = "로그 logs/daily_build_20261121.log") -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(SCRIPT), level, title, body], env=self.env, capture_output=True,
                              text=True, errors="replace", timeout=60, check=False)

    @property
    def curls(self) -> list[str]:
        return self.curl_log.read_text(encoding="utf-8").splitlines() if self.curl_log.exists() else []

    def lines(self, name: str = "notify.log") -> list[str]:
        f = self.ql / "logs" / name
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def test_설정이_고른_등급_crit_만_그_채널로_보내고_기록은_등급마다_남긴다(tmp_path: Path) -> None:
    """재현(11-21 아침 확정판 실패 · 11-24 저녁 엑셀 미발송): 실패가 notify.log 에만 남아 사람이 몇 시간 뒤에야 알았다.
    옛 코드는 설정 파일을 읽지 않아 crit 도 보내지 않는다."""
    box = Box(tmp_path)
    for level in ("crit", "warn", "info"):
        assert box.call(level, title=f"제목 {level}").returncode == 0
    assert len(box.curls) == 1
    assert "api.telegram.org/botfake-token/sendMessage" in box.curls[0]
    assert "chat_id=-100777" in box.curls[0] and "12345" not in box.curls[0]     # 설정이 가리킨 채팅 변수의 값
    assert "[quant-ledger] 제목 crit" in box.curls[0] and "KST" in box.curls[0]   # 본문 앞에 KST 시각
    assert [ln.split(" ")[1] for ln in box.lines()] == ["crit", "warn", "info"]
    assert box.lines("notify_failed.log") == []


def test_같은_제목은_crit_도_30분에_한_번만_보내고_기록은_매번_남긴다(tmp_path: Path) -> None:
    box = Box(tmp_path)
    first, second = box.call("crit"), box.call("crit")
    assert first.returncode == 0 and second.returncode == 0
    assert len(box.curls) == 1 and "cooldown" in second.stdout
    assert len(box.lines()) == 2
    assert box.call("crit", title="다른 제목").returncode == 0 and len(box.curls) == 2


def test_건너뛸_제목_정규식에_맞으면_보내지_않는다(tmp_path: Path) -> None:
    """일일 리포트 crit 은 다른 crit 의 요약이라 겹친다 — 서버 설정의 정규식으로 뺀다."""
    box = Box(tmp_path, conf=_CONF + "QL_NOTIFY_SKIP_TITLES='^일일 리포트'\n")
    assert box.call("crit", title="일일 리포트 20261124").returncode == 0
    assert box.curls == [] and len(box.lines()) == 1
    assert box.call("crit", title="저녁 판 실패").returncode == 0 and len(box.curls) == 1


def test_비밀_파일에_그_채팅_변수가_없으면_rc_2_이고_실패_장부와_기록이_남는다(tmp_path: Path) -> None:
    box = Box(tmp_path, env_text="BOT_TOKEN=fake-token\nCHAT_ID_LOG=12345\n")
    proc = box.call("crit")
    assert proc.returncode == 2 and "CHAT_ID_AIPLAYGROUND" in proc.stderr
    assert box.curls == [] and len(box.lines()) == 1 and len(box.lines("notify_failed.log")) == 1
    assert "fake-token" not in proc.stderr + proc.stdout + "".join(box.lines("notify_failed.log"))


def test_전송_실패는_rc_1_이고_실패_장부에_남으며_다음_호출은_다시_보낸다(tmp_path: Path) -> None:
    box = Box(tmp_path, curl_ok=False)
    assert box.call("crit").returncode == 1 and box.call("crit").returncode == 1
    assert len(box.curls) == 2 and len(box.lines()) == 2 and len(box.lines("notify_failed.log")) == 2


@pytest.mark.parametrize("conf", [None, "QL_NOTIFY_CHAT_VAR=CHAT_ID_AIPLAYGROUND\n"])
def test_설정_파일이_없거나_등급이_비면_지금처럼_기록만_한다(tmp_path: Path, conf: str | None) -> None:
    """배포만으로는 동작이 바뀌지 않는다 — 서버 전용 파일을 놓아야 보내고, 지우면 곧바로 꺼진다."""
    box = Box(tmp_path, conf=conf)
    box.env["QL_NOTIFY_TELEGRAM_LEVELS"] = "crit"                 # 환경 변수로는 켜지지 않는다 — 파일만 본다
    proc = box.call("crit")
    assert proc.returncode == 0 and "logged only" in proc.stdout
    assert box.curls == [] and len(box.lines()) == 1
