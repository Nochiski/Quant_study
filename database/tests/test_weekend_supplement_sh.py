"""scripts/weekend_supplement.sh 와 daily_build.sh 끝 훅 — 주말 보강판(N-45 · DECISIONS §7 #8 · 엑셀 확정분 E5).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 스크립트(daily_build·model_daily·weekend_supplement·raw_lock·
postclose_conf)와 대역 `.venv/bin/python`·`scripts/notify.sh`·`scripts/build_morning.sh`·`scripts/postclose_chain.sh`
를 두고 돌린다(test_model_daily_sh 와 같은 방식). 대역 python 은 `-m` 모듈과 인자를 calls.txt 에 적고 rc 를 환경변수로
돌려준다 — 보강판 단계(인자에 ' supplement ')는 SFI_RC·SMODEL_RC·SDELIVER_RC, 아침판은 FI_RC. 실제 factor_inputs·
model·텔레그램은 돌지 않는다. deliver 는 REAL_DELIVER(가짜 전송 sitecustomize 폴더)를 준 테스트만 진짜 CLI 로 돈다
(`test_supplement_deliver_step_is_the_real_cli`). `-c` 중 주말 보강판 판정
(`supplement` 가 든 것 — 대상일·수집 ok·완료·런 로그·고정 판)과 heredoc(`python -`, fi 게이트 경고)은 진짜 python·진짜
`src/` 로 넘겨 임시 루트의 달력(`data/calendar`)·런 로그(`data/raw/daily_run.db`)·인계 이력·fi 판 manifest 를 읽고 쓴다.
KRX 단계의 sqlite3 와 빌드 락 flock 은 PATH 대역이다(test_model_daily_sh 의 대역 그대로). 원장 락은 QL_RAW_LOCK_HELD=1.

날짜는 실제 휴장: D = 2026-10-08(목), W = 10-09(금 한글날 — 휴장 연속 첫 휴일). 둘 다 오늘보다 앞이라 미래 날짜 거부에
걸리지 않는다. 오늘(KST)은 QL_TODAY 로 W 를 준다(테스트 전용).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
import test_model_daily_sh as tmd
from daily import runlog

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = DB_ROOT / "scripts"
D, W = "20261008", "20261009"
HOLIDAYS_2026 = ["20260924", "20260925", "20261005", "20261009", "20261225", "20261231"]
ON = "SUPPLEMENT_ENABLED=1\n"
RUN_DB = "data/raw/daily_run.db"
SUPP_STEPS = [f"factor_inputs build --date {D} --basis supplement --wise-asof {W} "
              f"--builds-from data/deliver/history/{D}_morning.json",
              f"model build --date {D} --basis supplement",
              f"deliver model-daily --date {D} --basis supplement --send"]
MORNING_STEPS = [f"factor_inputs build --date {D} --basis morning",
                 f"model build --date {D} --basis morning",
                 f"deliver model-daily --date {D} --basis morning --send"]
FI_GATES_WARN = [{"name": "FG-fresh", "status": "pass", "detail": "warn: grace 31종목 > 30",
                  "metrics": {"warn": True}}]

_PY = """#!/usr/bin/env bash
if [ "$1" = "-" ]; then
  PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@"
fi
if [ "$1" = "-c" ]; then
  case "$2" in
    *supplement*) PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@" ;;
    *_morning.json*) exit "${GUARD_RC:-1}" ;;
    *prev_trading_day*) echo "${CHAIN_D:-20261008}" ;;
  esac
  exit 0
fi
if [ "$1" = "-m" ]; then
  mod="$2"; shift 2
  echo "$mod $*" >> "$QL_HOME/calls.txt"
  supp=""; case " $* " in *" supplement "*) supp=1 ;; esac
  case "$mod" in
    factor_inputs) if [ -n "$supp" ]; then exit "${SFI_RC:-0}"; fi; exit "${FI_RC:-0}" ;;
    model) if [ -n "$supp" ]; then exit "${SMODEL_RC:-0}"; fi; exit 0 ;;
    deliver) if [ -n "$supp" ]; then
               # REAL_DELIVER = sitecustomize(텔레그램 가짜 전송) 폴더 — 주면 진짜 deliver CLI 를 돈다
               if [ -n "${REAL_DELIVER:-}" ]; then
                 PYTHONPATH="$REAL_SRC:$REAL_DELIVER" exec "$REAL_PY" -m deliver "$@"
               fi
               exit "${SDELIVER_RC:-0}"
             fi; exit 0 ;;
  esac
fi
exit 0
"""


class Run(NamedTuple):
    rc: int
    out: str
    calls: list[str]                 # 대역 python -m 호출 "모듈 인자…"(+ 대역 스크립트 줄)
    notify: list[str]                # "등급|제목|본문"
    board: list[tuple[str, str, str | None]]   # 런 로그 supplement_board (date, status, detail) — 오래된 것부터
    flock: list[str]

    def titles(self, level: str | None = None) -> list[str]:
        return [n.split("|")[1] for n in self.notify if level is None or n.startswith(level + "|")]

    @property
    def supp_calls(self) -> list[str]:
        return [c for c in self.calls if " supplement" in c]


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _home(home: Path, *, conf: str | None = ON, history: dict | None = None,
          wise: str | None = "ok", fi_gates: list[dict] | None = None,
          stub_supplement: str | None = None) -> Path:
    """conf = config/supplement.env 내용(None 이면 없음) · history = <D>_morning.json 내용(None 이면 없음 — 기본은
    health ok 판) · wise = 오늘(W) 보강 WISE 수집 런 상태(None 이면 기록 없음) · fi_gates = 보강 fi 판 manifest 의 게이트
    · stub_supplement = weekend_supplement.sh 를 이 본문의 대역으로 바꾼다."""
    root = home / "quant-ledger"
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    for name in ("daily_build.sh", "model_daily.sh", "weekend_supplement.sh", "raw_lock.sh",
                 "postclose_conf.sh"):
        _write(root / "scripts" / name, (SCRIPTS / name).read_text(encoding="utf-8"), 0o755)
    (root / "logs").mkdir(exist_ok=True)
    order = 'echo "$(basename "$0") $*" >> "$HOME/quant-ledger/calls.txt"\n'
    _write(root / ".venv/bin/python", _PY, 0o755)
    _write(root / "scripts/notify.sh", '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n', 0o755)
    _write(root / "scripts/build_morning.sh", tmd._BUILD, 0o755)
    _write(root / "scripts/postclose_chain.sh", "#!/usr/bin/env bash\n" + order + "exit 0\n", 0o755)
    if stub_supplement is not None:
        _write(root / "scripts/weekend_supplement.sh",
               "#!/usr/bin/env bash\n" + 'echo "weekend_supplement.sh $* QL_FORCE=${QL_FORCE:-}" '
               '>> "$HOME/quant-ledger/calls.txt"\n' + stub_supplement, 0o755)
    _write(home / "fakebin/sqlite3", tmd._SQLITE, 0o755)
    _write(home / "fakebin/flock", tmd._FLOCK, 0o755)
    if conf is not None:
        _write(root / "config/supplement.env", conf)
    _write(root / "data/calendar/kis_holidays_2026.json",
           json.dumps({"year": 2026, "holidays": HOLIDAYS_2026}))
    if history is None:
        history = {"date": D, "basis": "morning", "stage_builds": {"stg_consensus_annual": "m_x"},
                   "equity_builds": {"price_daily": "m_y"}, "health": {"stage": "ok", "equity": "ok"}}
    if history:
        _write(root / f"data/deliver/history/{D}_morning.json", json.dumps(history))
    for basis, gates in (("morning", []), ("supplement", fi_gates or [])):
        _write(root / f"data/factor_inputs/_runs/{D}_{basis}.json",
               json.dumps({"status": "ok", "basis": basis, "gates": gates}))
    db = root / RUN_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    runlog.recent(db, limit=1)
    if wise is not None:
        runlog.finish(db, runlog.start(db, date=W, source="supplement_wise"), status=wise,
                      detail=f"D={D} wise_rc=0 fail_calls=0")
    return root


def _run(home: Path, script: str, *args: str, held: bool = True, **env_extra: str | int) -> Run:
    root = home / "quant-ledger"
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"),
               PATH=f"{home / 'fakebin'}:{os.environ['PATH']}",
               QL_RAW_LOCK_HELD="1", QL_RAW_LOCK_FILE=str(home / "raw.lock"),
               QL_BUILD_LOCK_FILE=str(home / "build.lock"), FLOCK_LOG=str(home / "flock.txt"),
               REAL_PY=sys.executable, REAL_SRC=str(DB_ROOT / "src"), QL_TODAY=W)
    for k in ("QL_BUILD_LOCK_HELD", "QL_SKIP_KW", "QL_FORCE"):
        env.pop(k, None)
    if held:
        env["QL_BUILD_LOCK_HELD"] = "1"
    env.update({k.upper() if k.islower() else k: str(v) for k, v in env_extra.items()})
    p = subprocess.run(["bash", str(root / "scripts" / script), *args], env=env,
                       capture_output=True, text=True, timeout=120, check=False)

    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    board = [(r.date, r.status, r.detail)
             for r in reversed(runlog.recent(root / RUN_DB, source="supplement_board"))]
    return Run(p.returncode, p.stdout + p.stderr, lines(root / "calls.txt"),
               lines(root / "notify.txt"), board, lines(home / "flock.txt"))


# ── weekend_supplement.sh ─────────────────────────────────────────────────────
def test_supplement_runs_fi_model_deliver_once_on_the_pinned_board(tmp_path: Path) -> None:
    """fi(basis supplement · --wise-asof W · --builds-from D 아침 확정판 이력) → 모델 → 엑셀(--send)이 순서대로
    한 번씩. 런 로그 supplement_board(date = D) ok · 완료 info."""
    _home(tmp_path)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert r.rc == 0, r.out
    assert r.calls == SUPP_STEPS
    assert r.board == [(D, "ok", f"W={W}")]
    assert r.notify == [f"info|주말 보강판 완료|D={D} W={W} basis=supplement | fi → 모델 → 엑셀 발송"]
    assert f"주말 보강판 완료 D={D} W={W}" in r.out


# 가짜 텔레그램 — deliver 의 urllib 전송을 가로채 캡션을 FAKE_TG_LOG 에 적고 ok 를 돌려준다(네트워크 없음)
_SITE = '''import io, json, os, re, urllib.request


def _fake(req, timeout=None):
    cap = re.search(rb'name="caption"\\r\\n\\r\\n(.*?)\\r\\n--', req.data, re.S)
    with open(os.environ["FAKE_TG_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"caption": cap.group(1).decode("utf-8") if cap else None},
                           ensure_ascii=False) + "\\n")
    return io.BytesIO(b'{"ok": true}')


urllib.request.urlopen = _fake
'''


def test_supplement_deliver_step_is_the_real_cli(tmp_path: Path) -> None:
    """E5b — deliver 단계를 대역 대신 진짜 `python -m deliver model-daily --basis supplement --send` 로 돈다(텔레그램만
    가짜 — sitecustomize 가 urlopen 을 가로챈다). 합성 모델 판 D(10-08 아침 + 같은 점수의 보강판) · 직전 거래일 10-07
    아침판. 보강판 엑셀 · 캡션 '주말 보강' · 발송 장부 (D, supplement) 줄이 생기고, 같은 장부의 D 아침 줄이 있어도
    막히지 않는다."""
    from test_deliver_excel import write_fi_day, write_model_day
    root = _home(tmp_path)
    model, fi = root / "data/model", root / "data/factor_inputs"
    for day in ("2026-10-07", "2026-10-08"):
        write_model_day(model, day, write_fi_day(fi, day))
    meta = json.loads((model / f"_runs/{D}_morning.json").read_text(encoding="utf-8"))
    _write(model / f"_runs/{D}_supplement.json", json.dumps({**meta, "basis": "supplement"}))
    ledger = root / "data/deliver/sent_model_daily.jsonl"
    _write(ledger, json.dumps({"date": "2026-10-08", "basis": "morning", "correction": 0}) + "\n")
    _write(root / ".env", "BOT_TOKEN=fake-token\nCHAT_ID_AIPLAYGROUND=-100\n")
    _write(tmp_path / "site/sitecustomize.py", _SITE)
    tg = tmp_path / "tg.jsonl"
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W,
             REAL_DELIVER=str(tmp_path / "site"), FAKE_TG_LOG=str(tg))
    assert r.rc == 0, r.out
    assert r.calls == SUPP_STEPS and r.board == [(D, "ok", f"W={W}")]
    sent = [json.loads(x) for x in tg.read_text(encoding="utf-8").splitlines()]
    assert len(sent) == 1
    assert sent[0]["caption"].splitlines()[0].startswith("[모델 점수] 2026-10-08 주말 보강 · ")
    assert (root / "data/deliver/daily/model_scores_20261008_supplement.xlsx").exists()
    entries = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert [(e["date"], e["basis"]) for e in entries] == [("2026-10-08", "morning"),
                                                        ("2026-10-08", "supplement")]
    assert "fake-token" not in r.out


def test_supplement_is_once_per_d_unless_forced(tmp_path: Path) -> None:
    """D 당 한 번 — 두 번째 실행은 단계 없이 info 로 끝난다(rc 0). --force · QL_FORCE=1 이면 다시 돈다."""
    _home(tmp_path)
    first = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    again = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert first.rc == again.rc == 0, again.out
    assert again.calls == SUPP_STEPS                       # 두 번째는 단계를 더하지 않았다
    assert again.titles()[-1] == "주말 보강판 이미 완료 — 건너뜀" and "다시 하려면 --force" in again.out
    forced = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W, "--force")
    env_forced = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W, QL_FORCE=1)
    assert forced.rc == env_forced.rc == 0
    assert env_forced.calls == SUPP_STEPS * 3
    assert [b[1] for b in env_forced.board] == ["ok", "ok", "ok"]


@pytest.mark.parametrize(("failing", "rc_env"), [("factor_inputs", "sfi_rc"), ("model", "smodel_rc"),
                                                ("deliver", "sdeliver_rc")])
def test_supplement_step_failure_stops_later_steps_and_is_crit(tmp_path: Path, failing: str,
                                                               rc_env: str) -> None:
    """한 단계가 실패하면 뒤 단계는 돌지 않는다. crit 1건 · 런 로그 failed · rc 1. 다음 실행은 다시 돈다(ok 가 아니다)."""
    _home(tmp_path)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W, **{rc_env: 2})
    n = [s.split()[0] for s in SUPP_STEPS].index(failing) + 1
    assert r.rc == 1, r.out
    assert r.calls == SUPP_STEPS[:n]
    assert r.titles("crit") == [f"주말 보강판 실패: {failing}(rc=2)"]
    assert r.board == [(D, "failed", f"W={W} failed={failing}(rc=2)")]
    retry = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert retry.rc == 0 and retry.calls == SUPP_STEPS[:n] + SUPP_STEPS
    assert [b[1] for b in retry.board] == ["failed", "ok"]


@pytest.mark.parametrize(("history", "why"), [
    ({}, "인계 이력(--builds-from)이 없다"),
    ({"date": D, "basis": "morning", "stage_builds": {"a": "m_x"}, "equity_builds": {"b": "m_y"},
      "health": {"stage": "ok", "equity": "fail"}}, "쓸 수 있는 판이 아니다"),
    ({"date": "20261007", "basis": "morning", "stage_builds": {"a": "m_x"},
      "equity_builds": {"b": "m_y"}, "health": {"stage": "ok", "equity": "ok"}}, "date=20261007"),
])
def test_supplement_without_a_usable_d_board_is_a_warn(tmp_path: Path, history: dict,
                                                       why: str) -> None:
    """D 아침 확정판 이력이 없거나 health 실패·다른 날짜면 아무 단계도 돌지 않는다 — warn 1건 · 런 로그 failed · rc 1
    (정규 판 실패 알림은 08:10 체인 몫)."""
    _home(tmp_path, history=history)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert r.rc == 1, r.out
    assert r.calls == []
    assert r.titles() == ["주말 보강판 안 지음 — D 확정판 없음"] and why in r.notify[0]
    assert [b[1] for b in r.board] == ["failed"]


@pytest.mark.parametrize("conf", [None, "SUPPLEMENT_ENABLED=0\n"])
def test_supplement_switch_off_does_nothing(tmp_path: Path, conf: str | None) -> None:
    _home(tmp_path, conf=conf)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert r.rc == 0 and r.calls == [] and r.board == [], r.out
    assert r.titles() == ["주말 보강판 꺼짐 — 건너뜀"]


def _kst(offset: int) -> str:
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9, days=offset)).strftime("%Y%m%d")


@pytest.mark.parametrize("args", [(), ("--date", D), ("--wise-asof", W), ("--date", D, "--wise-asof", D),
                                  ("--date", D, "--wise-asof", "2026-10-09"),
                                  ("--date", D, "--wise-asof", "<future>"), ("--bogus",)])
def test_supplement_argument_errors_are_rc2(tmp_path: Path, args: tuple[str, ...]) -> None:
    """D·W 가 없거나 형식이 틀리거나 W ≤ D · W 가 오늘(KST) 뒤면 아무것도 하지 않고 rc 2(알림 없음)."""
    _home(tmp_path)
    args = tuple(_kst(2) if a == "<future>" else a for a in args)
    r = _run(tmp_path, "weekend_supplement.sh", *args)
    assert r.rc == 2, r.out
    assert (r.calls, r.notify, r.board) == ([], [], [])


def test_supplement_fi_gate_warning_is_one_warn_line(tmp_path: Path) -> None:
    """보강 fi 판 manifest 의 게이트 경고(metrics.warn)는 warn 한 줄 — 판정·발송은 그대로."""
    _home(tmp_path, fi_gates=FI_GATES_WARN)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert r.rc == 0 and r.calls == SUPP_STEPS, r.out
    assert r.titles() == ["주말 보강판 fi 게이트 경고", "주말 보강판 완료"]
    assert "FG-fresh" in r.notify[0]


def test_supplement_unreadable_run_log_is_crit_and_builds_nothing(tmp_path: Path) -> None:
    """런 로그를 못 읽으면 보냈는지 모른다 — 짓지 않고 crit · rc 1."""
    root = _home(tmp_path, wise=None)
    (root / RUN_DB).write_bytes(b"not a sqlite database" * 100)
    # `_run` 은 끝에 런 로그를 읽으므로(여기선 깨져 있다) 직접 부른다
    env = dict(os.environ, HOME=str(tmp_path), QL_BUILD_LOCK_HELD="1", REAL_PY=sys.executable,
               REAL_SRC=str(DB_ROOT / "src"), PATH=f"{tmp_path / 'fakebin'}:{os.environ['PATH']}")
    env.pop("QL_FORCE", None)
    p = subprocess.run(["bash", str(root / "scripts/weekend_supplement.sh"), "--date", D,
                        "--wise-asof", W], env=env, capture_output=True, text=True, timeout=60,
                       check=False)
    assert p.returncode == 1, p.stdout + p.stderr
    notify = (root / "notify.txt").read_text(encoding="utf-8").splitlines()
    assert [n.split("|")[:2] for n in notify] == [["crit", "주말 보강판 실패: 완료 판정 불가"]]
    assert not (root / "calls.txt").exists()


def test_supplement_waits_for_the_build_lock(tmp_path: Path) -> None:
    """빌드 락이 쥐여 있으면 비대기로 확인한 뒤 한도 없는 대기형으로 잡고(P9) 이어서 돈다 — info 1건."""
    _home(tmp_path)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W, held=False,
             FLOCK_N_RC=1)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]
    assert r.titles() == ["주말 보강판 빌드 락 대기", "주말 보강판 완료"]
    assert r.calls == SUPP_STEPS


# ── daily_build.sh 끝 훅 ─────────────────────────────────────────────────────
def test_daily_build_chains_the_supplement_after_the_regular_board(tmp_path: Path) -> None:
    """G1 — 스위치 켜짐 · 오늘 W = D 다음 첫 휴일 · 보강 WISE 수집 ok → 정규 체인(확정판 → 아침 모델 단계 → 아침 잇기 →
    조용한 손실 검사)이 다 끝난 뒤 보강판(fi·모델·엑셀 basis supplement)이 이어서 돈다. 정규 체인 rc·완료 info 는
    그대로이고 요약에 보강판 종료 줄이 실린다."""
    _home(tmp_path)
    r = _run(tmp_path, "daily_build.sh", "--date", D)
    assert r.rc == 0, r.out
    mods = [c.split()[0] for c in r.calls]
    assert mods == [*tmd.LEDGER_STEPS, "build_morning", "factor_inputs", "model", "deliver",
                    "postclose_chain.sh", "daily.silent_loss", "factor_inputs", "model", "deliver"]
    assert [c for c in r.calls if c.split()[0] in ("factor_inputs", "model", "deliver")] == [
        *MORNING_STEPS, *SUPP_STEPS]
    assert r.board == [(D, "ok", f"W={W}")]
    assert "주말 보강판 완료" in r.titles("info")
    done = [n for n in r.notify if n.startswith("info|daily_build 완료|")]
    assert len(done) == 1 and "──── 주말 보강판 종료 rc=0" in done[0]
    assert not r.titles("warn") and not r.titles("crit")


@pytest.mark.parametrize(("conf", "today", "chain_d"), [
    (None, W, D),                         # 스위치 없음(저장소 값과 같다)
    ("SUPPLEMENT_ENABLED=0\n", W, D),
    (ON, "20261010", D),                  # 토요일 — 한글날 다음이라 첫 휴일이 아니다
    (ON, "20261008", D),                  # 거래일
    (ON, W, "20261007"),                  # 이 체인의 D 가 보강 대상 D 가 아니다(손 --date)
])
def test_daily_build_runs_no_supplement_outside_its_day(tmp_path: Path, conf: str | None,
                                                        today: str, chain_d: str) -> None:
    _home(tmp_path, conf=conf)
    r = _run(tmp_path, "daily_build.sh", "--date", chain_d, QL_TODAY=today)
    assert r.rc == 0, r.out
    assert r.supp_calls == [] and r.board == []
    assert not [t for t in r.titles() if "보강" in t]


@pytest.mark.parametrize("wise", [None, "failed", "running"])
def test_daily_build_skips_the_supplement_when_the_wise_fetch_is_not_ok(tmp_path: Path,
                                                                        wise: str | None) -> None:
    """06:00 체인 끝 보강 WISE 수집이 ok 가 아니면(기록 없음·실패·끝나지 않음) 보강판을 짓지 않고 warn 1건."""
    _home(tmp_path, wise=wise)
    r = _run(tmp_path, "daily_build.sh", "--date", D)
    assert r.rc == 0, r.out
    assert r.supp_calls == [] and r.board == []
    assert r.titles("warn") == ["주말 보강판 안 지음 — 보강 WISE 수집 ok 아님"]
    assert (wise or "기록 없음") in [n for n in r.notify if n.startswith("warn|")][0]


def test_daily_build_supplement_failure_never_changes_the_chain(tmp_path: Path) -> None:
    """보강판 단계 실패는 보강판의 crit 1건뿐 — 정규 체인 rc(0)·완료 info·아침 모델 단계는 그대로이고 daily_build 가
    따로 warn 을 더하지 않는다(rc 1 = 스스로 알림을 남김)."""
    _home(tmp_path)
    r = _run(tmp_path, "daily_build.sh", "--date", D, sfi_rc=2)
    assert r.rc == 0, r.out
    assert r.supp_calls == SUPP_STEPS[:1]
    assert r.titles("crit") == ["주말 보강판 실패: factor_inputs(rc=2)"]
    assert "daily_build 완료" in r.titles("info") and not r.titles("warn")


@pytest.mark.parametrize("rc", [2, 3, 4])
def test_daily_build_warns_when_the_supplement_dies_without_its_own_alert(tmp_path: Path,
                                                                         rc: int) -> None:
    _home(tmp_path, stub_supplement=f"exit {rc}\n")
    r = _run(tmp_path, "daily_build.sh", "--date", D)
    assert r.rc == 0, r.out
    assert r.titles("warn") == [f"daily_build 주말 보강판 rc={rc}"]


def test_daily_build_supplement_is_once_even_with_ql_force(tmp_path: Path) -> None:
    """같은 휴일에 08:10 체인을 다시 돌려도(QL_FORCE=1 — 정규 판 재빌드) 보강판은 D 당 한 번이다 — 체인은
    QL_FORCE 를 넘기지 않는다."""
    _home(tmp_path)
    first = _run(tmp_path, "daily_build.sh", "--date", D)
    again = _run(tmp_path, "daily_build.sh", "--date", D, QL_FORCE=1)
    assert first.rc == again.rc == 0, again.out
    assert again.supp_calls == SUPP_STEPS
    assert "주말 보강판 이미 완료 — 건너뜀" in again.titles("info")


def test_daily_build_passes_d_and_today_and_clears_ql_force(tmp_path: Path) -> None:
    _home(tmp_path, stub_supplement="exit 0\n")
    r = _run(tmp_path, "daily_build.sh", "--date", D, QL_FORCE=1)
    assert r.rc == 0, r.out
    assert f"weekend_supplement.sh --date {D} --wise-asof {W} QL_FORCE=" in r.calls


def test_daily_build_skip_day_still_chains_the_supplement(tmp_path: Path) -> None:
    """D 확정판이 이미 있어 정규 체인을 건너뛴 날(크론 경로 · 가드)도 첫 휴일이면 보강판을 잇는다(아직이면)."""
    _home(tmp_path)
    r = _run(tmp_path, "daily_build.sh", GUARD_RC=0, CHAIN_D=D)
    assert r.rc == 0, r.out
    assert r.calls == SUPP_STEPS
    assert f"daily_build 건너뜀(D={D} 확정판 완료)" in r.titles("info")


def test_daily_build_dry_run_never_chains_the_supplement(tmp_path: Path) -> None:
    _home(tmp_path)
    r = _run(tmp_path, "daily_build.sh", "--date", D, "--dry-run")
    assert r.rc == 0, r.out
    assert r.supp_calls == [] and r.board == [] and r.notify == []


def test_daily_build_failure_summary_is_not_pushed_out_by_the_supplement(tmp_path: Path) -> None:
    """확정 빌드가 실패한 날(그날 앞서 지은 D 확정판 이력은 남아 보강판이 돈 경우)에도 crit 요약의 확정판 줄은 종전과
    같다 — 보강판 안쪽 단계 줄이 tail 창을 밀어내지 않고, 보강판 종료 줄 하나만 앞에 실린다."""
    _home(tmp_path)
    r = _run(tmp_path, "daily_build.sh", "--date", D, build_rc=2)
    assert r.rc == 2, r.out
    crit = [n for n in r.notify if n.startswith("crit|daily_build 실패: build_morning(rc=2)|")]
    assert len(crit) == 1
    body = crit[0].split("|", 2)[2]
    assert body.startswith("──── 주말 보강판 종료 rc=0") and "──── krx 종료 rc=0" in body
    assert "factor_inputs 종료" not in body and "deliver 종료" not in body


def test_supplement_done_check_runs_under_the_build_lock(tmp_path: Path) -> None:
    """완료 판정은 빌드 락을 잡은 뒤에 한다 — 체인과 손 실행이 함께 '아직'을 보고 둘 다 짓는(보내는) 일을 막는다.
    앞 실행이 끝나 락이 풀리면 뒤 실행은 '이미 완료'로 끝난다."""
    _home(tmp_path)
    first = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W)
    assert first.rc == 0, first.out
    (tmp_path / "flock.txt").unlink(missing_ok=True)
    (tmp_path / "quant-ledger" / "notify.txt").unlink()
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W, held=False,
             FLOCK_N_RC=1)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]
    assert r.titles() == ["주말 보강판 빌드 락 대기", "주말 보강판 이미 완료 — 건너뜀"]
    assert r.calls == SUPP_STEPS


def test_supplement_done_check_crash_is_not_read_as_not_yet(tmp_path: Path) -> None:
    """완료 판정 파이썬이 죽으면(import 실패 등 — rc 1) '아직'으로 읽지 않는다 — 짓지 않고 crit · rc 1."""
    _home(tmp_path)
    r = _run(tmp_path, "weekend_supplement.sh", "--date", D, "--wise-asof", W,
             REAL_SRC=str(tmp_path / "no_src"))
    assert r.rc == 1, r.out
    assert r.calls == [] and r.titles("crit") == ["주말 보강판 실패: 완료 판정 불가"]
