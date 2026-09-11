#!/usr/bin/env bash
# 워치독 — 예정 시각까지 체인 보고가 없거나 실패면 crit. 플랜 v2 Task A.4 / 결정 V2-7(조용한 실패 금지).
#   사용: scripts/watchdog.sh <evening_ledger|evening_build|morning_build>
#   예정 크론(서버 TZ=UTC. 등록은 오케스트레이터가 한다):
#     50 9 * * 1-5  cd /home/kael/quant-ledger && scripts/watchdog.sh evening_ledger   # 18:50 KST
#      0 10 * * 1-5 cd /home/kael/quant-ledger && scripts/watchdog.sh evening_build    # 19:00 KST
#     15 0 * * 2-6  cd /home/kael/quant-ledger && scripts/watchdog.sh morning_build    # 09:15 KST
#   판정 근거는 체인이 남긴 산출물뿐이다 — 원장·API 를 건드리지 않으므로 raw 락도 잡지 않는다.
#   휴장일(오늘 KST)은 info 후 rc 0. 스코어 워치독은 페이즈 C 에서 case 에 추가한다.
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
CHECK="${1:?usage: watchdog.sh <evening_ledger|evening_build|morning_build>}"
case "$CHECK" in
  evening_ledger) TITLE_OK="watchdog evening_ledger 정상"; TITLE_BAD="watchdog: 18:50 까지 저녁 원장 보고 없음/실패" ;;
  evening_build)  TITLE_OK="watchdog evening_build 정상";  TITLE_BAD="watchdog: 19:00 까지 잠정판 보고 없음/실패" ;;
  morning_build)  TITLE_OK="watchdog morning_build 정상";  TITLE_BAD="watchdog: 09:15 까지 확정 빌드 보고 없음/실패" ;;
  *) echo "unknown check: $CHECK (allowed: evening_ledger, evening_build, morning_build)" >&2; exit 2 ;;
esac
TODAY=$(TZ=Asia/Seoul date +%Y%m%d)
# 오늘이 거래일인가 — 캘린더를 못 읽으면 1(거래일)로 본다. 조용히 넘어가는 쪽이 아니라 판정하는 쪽으로 기운다.
TRADING=$($PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
print(1 if c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))) else 0)' "$TODAY" 2>/dev/null || echo 1)
if [ "$TRADING" != "1" ]; then
  scripts/notify.sh info "watchdog $CHECK — 휴장" "$TODAY(KST)는 거래일이 아니다 — 판정 건너뜀"
  exit 0
fi
# 판정은 파이썬이 한다(jq 없음). 1줄 = 알림 제목에 붙일 시각, 2줄~ = 본문. rc 0 = 정상, 1 = 이상.
OUT=$($PY - "$CHECK" "$TODAY" <<'PY'
import datetime as dt
import json
import os
import sys

KST = dt.timezone(dt.timedelta(hours=9))
check, today = sys.argv[1], sys.argv[2]
today_d = dt.date(int(today[:4]), int(today[4:6]), int(today[6:8]))


def out(stamp: str, body: str, rc: int) -> None:
    print(stamp)
    print(body)
    raise SystemExit(rc)


def rc_txt(v: object) -> str:
    return "결측" if v is None else str(v)


if check == "evening_ledger":
    path = "data/deliver/ledger_evening.json"
    if not os.path.exists(path):
        out("", f"{path} 없음 — 18:05 저녁 슬롯이 돌지 않았거나 보고 파일을 쓰지 못했다", 1)
    try:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        out("", f"{path} 를 읽을 수 없다 ({type(e).__name__}: {e})", 1)
    fin = str(rep.get("finished_at") or "")
    summary = (f"date={rep.get('date') or '결측'} finished_at={fin or '결측'} "
               f"kiwoom_rc={rc_txt(rep.get('kiwoom_rc'))} wise_rc={rc_txt(rep.get('wise_rc'))} "
               f"dart_rc={rc_txt(rep.get('dart_rc'))}(판정 제외) "
               f"kiwoom_done_at={rep.get('kiwoom_done_at') or '결측'} wise_done_at={rep.get('wise_done_at') or '결측'}")
    if str(rep.get("date") or "") != today:
        out("", f"보고 파일이 오늘({today}) 것이 아니다 — {summary}", 1)
    # DART 는 30분짜리라 18:50 에 아직 돌고 있을 수 있다 — rc 는 적기만 하고 판정에서는 뺀다.
    bad = [k for k in ("kiwoom_rc", "wise_rc") if rep.get(k) != 0]
    if bad:
        out("", f"실패/미완 단계 {', '.join(bad)} — {summary}", 1)
    out(fin.replace(" ", "T").split("T")[-1][:5], summary, 0)

if check == "evening_build":
    # 18:15 잠정 빌드가 남긴 인계 파일. stage·equity 둘 다 ok 여야 Kael-alpha 가 스코어를 낼 수 있다.
    path = "data/deliver/latest_evening.json"
    if not os.path.exists(path):
        out("", f"{path} 없음 — 18:15 잠정 빌드가 돌지 않았거나 인계 파일을 쓰지 못했다", 1)
    try:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        out("", f"{path} 를 읽을 수 없다 ({type(e).__name__}: {e})", 1)
    health = rep.get("health") if isinstance(rep.get("health"), dict) else {}
    elapsed = rep.get("elapsed_s") if isinstance(rep.get("elapsed_s"), dict) else {}
    gen = str(rep.get("generated_at") or "")
    summary = (f"date={rep.get('date') or '결측'} generated_at={gen or '결측'} "
               f"stage={rc_txt(health.get('stage'))} equity={rc_txt(health.get('equity'))} "
               f"snapshot={rep.get('stage_snapshot_id') or '결측'} "
               f"stage {rc_txt(elapsed.get('stage'))}s · equity {rc_txt(elapsed.get('equity'))}s "
               f"(stage {len(rep.get('stage_builds') or {})}표 · equity {len(rep.get('equity_builds') or {})}표)")
    if str(rep.get("date") or "") != today:
        out("", f"인계 파일이 오늘({today}) 것이 아니다 — {summary}", 1)
    bad = [k for k in ("stage", "equity") if health.get(k) != "ok"]
    if bad:
        out("", f"건전성 실패 {', '.join(bad)} — {summary}", 1)
    try:    # generated_at 은 UTC 다 — 제목에 다는 시각은 KST 로 바꾼다(19:00 판정인데 10:00 으로 보이면 안 된다)
        stamp = dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc).astimezone(KST).strftime("%H:%M")
    except ValueError:
        stamp = ""
    out(stamp, summary, 0)

if check == "morning_build":
    from daily import calendar as cal_mod
    d_prev = cal_mod.load().prev_trading_day(today_d).strftime("%Y%m%d")
    path = f"logs/health/{d_prev}.json"
    if not os.path.exists(path):
        out("", f"{path} 없음 — 08:10 확정 빌드가 D={d_prev} 건전성 리포트를 쓰지 못했다", 1)
    mtime = os.path.getmtime(path)
    when = dt.datetime.fromtimestamp(mtime, KST).strftime("%m-%d %H:%M")
    thresh = dt.datetime.combine(today_d, dt.time(8, 0), KST).timestamp()
    if mtime < thresh:
        out("", f"{path} 가 오늘 08:00 KST 이후 갱신되지 않았다 (마지막 {when} KST) — 확정 빌드 미실행 의심", 1)
    try:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        out("", f"{path} 를 읽을 수 없다 ({type(e).__name__}: {e})", 1)
    checks = [c for c in rep.get("checks", []) if isinstance(c, dict)]
    fails = [str(c.get("name")) for c in checks if c.get("status") == "fail" and c.get("level") in ("required", "halt")]
    warns = [str(c.get("name")) for c in checks if c.get("status") == "fail" and c.get("level") == "warn"]
    warn_txt = f" | 경고 {', '.join(warns)}" if warns else ""
    if rep.get("ok") is not True:
        out("", f"건전성 FAIL D={d_prev} ({when} KST) 실패 항목: {', '.join(fails) or '미상'}{warn_txt}", 1)
    out(when.split(" ")[-1], f"D={d_prev} 건전성 OK ({when} KST){warn_txt}", 0)

out("", f"판정 로직이 없는 check: {check}", 1)
PY
)
RC=$?
STAMP=$(printf '%s\n' "$OUT" | head -1)
BODY=$(printf '%s\n' "$OUT" | tail -n +2 | tr '\n' ' ' | cut -c1-900)
if [ "$RC" -eq 0 ]; then
  scripts/notify.sh info "$TITLE_OK ${STAMP:-$(TZ=Asia/Seoul date +%H:%M)}" "$BODY"
  exit 0
fi
scripts/notify.sh crit "$TITLE_BAD" "$BODY"
exit 2
