#!/usr/bin/env bash
# 워치독 — 예정 시각까지 체인 보고가 없거나 실패면 crit. 플랜 v2 Task A.4 / 결정 V2-7(조용한 실패 금지).
#   사용: scripts/watchdog.sh <evening_ledger|evening_build|morning_build|wics_weekly>
#   예정 크론(서버 TZ=UTC. 등록은 오케스트레이터가 한다):
#     50 12 * * 1-5 cd /home/kael/quant-ledger && scripts/watchdog.sh evening_ledger   # 21:50 KST (결정 11: 키움 저녁 수집 21:05)
#     30 14 * * 1-5 cd /home/kael/quant-ledger && scripts/watchdog.sh evening_build    # 23:30 KST (D02: 빌드 시작 한도 21:45 + stage 실측 43~66분 + equity 9~11분 = 상한 23:06. 옛 23:00 은 한도에 시작한 정상 판을 오탐했다)
#     30 2 * * 6    cd /home/kael/quant-ledger && scripts/watchdog.sh wics_weekly      # 토 11:30 KST — 금요일 dt WICS 스냅샷 38코드(행>0)
#     0 1 * * *     cd /home/kael/quant-ledger && scripts/watchdog.sh morning_build    # 10:00 KST 매일 (D03: 08:10 시작 + 실측 종료 09:23~09:30, krx_step 재시도 1회 +10분까지 흡수. 옛 09:45 은 여유 14.6분) — 금요일 판은 토요일에 지어지고 판정 기준은 "대상일 다음 날 08:00" 이라 실행일의 휴장 여부와 무관(검수 R4-07)
#   판정 근거는 체인이 남긴 산출물뿐이다 — 원장·API 를 건드리지 않으므로 raw 락도 잡지 않는다.
#   휴장일(오늘 KST)은 info 후 rc 0. 스코어 워치독은 페이즈 C 에서 case 에 추가한다.
set -uo pipefail
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python
CHECK="${1:?usage: watchdog.sh <evening_ledger|evening_build|morning_build>}"
case "$CHECK" in
  evening_ledger) TITLE_OK="watchdog evening_ledger 정상"; TITLE_BAD="watchdog: 21:50 까지 저녁 원장 보고 없음/실패" ;;
  evening_build)  TITLE_OK="watchdog evening_build 정상";  TITLE_BAD="watchdog: 23:30 까지 잠정판 보고 없음/실패" ;;
  morning_build)  TITLE_OK="watchdog morning_build 정상";  TITLE_BAD="watchdog: 10:00 까지 확정 빌드 보고 없음/실패" ;;
  wics_weekly)    TITLE_OK="watchdog wics_weekly 정상";    TITLE_BAD="watchdog: 토 11:30 까지 WICS 주간 스냅샷 없음/불완전" ;;
  *) echo "unknown check: $CHECK (allowed: evening_ledger, evening_build, morning_build, wics_weekly)" >&2; exit 2 ;;
esac
TODAY=$(TZ=Asia/Seoul date +%Y%m%d)
# 오늘이 거래일인가 — 캘린더를 못 읽으면 1(거래일)로 본다. 조용히 넘어가는 쪽이 아니라 판정하는 쪽으로 기운다.
TRADING=$($PY -c 'import datetime as dt, sys
from daily import calendar as c
d = sys.argv[1]
print(1 if c.load().is_trading_day(dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))) else 0)' "$TODAY" 2>/dev/null || echo 1)
# 저녁 두 검사는 "오늘" 을 판정하므로 휴장이면 건너뛴다. morning_build 는 직전 거래일의 확정판을 보므로 매일 돈다.
# wics_weekly 는 토요일(휴장) 검사라 거래일 가드 밖이다 — 직전 거래일(금요일) 스냅샷을 본다.
if [ "$CHECK" != "morning_build" ] && [ "$CHECK" != "wics_weekly" ] && [ "$TRADING" != "1" ]; then
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
    # DART 는 30~45분짜리라 판정 시각에 아직 돌고 있을 수 있다 — rc 는 적기만 하고 판정에서는 뺀다.
    bad = [k for k in ("kiwoom_rc", "wise_rc") if rep.get(k) != 0]
    if bad:
        out("", f"실패/미완 단계 {', '.join(bad)} — {summary}", 1)
    # finished_at 은 UTC 다(daily_evening.sh 가 dt.datetime.now(dt.UTC) 로 쓴다) — 제목 시각은 KST 로 바꾼다.
    # 아래 evening_build 와 같은 규약이다. 문자열을 그대로 자르면 21:19 종료가 12:19 로 보인다(D06).
    try:
        stamp = dt.datetime.strptime(fin, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc).astimezone(KST).strftime("%H:%M")
    except ValueError:
        stamp = ""
    out(stamp, summary, 0)

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
    # 확정판은 대상일 D 의 다음 달력일 08:10 체인이 짓는다(KRX T+1 08:00). 금요일 판은 토요일에 지어지므로
    # 기준은 "오늘 08:00" 이 아니라 "D+1 08:00" 이다 — 월요일에 금요일 판을 다시 판정해도 오탐하지 않는다.
    d_prev_d = dt.date(int(d_prev[:4]), int(d_prev[4:6]), int(d_prev[6:8]))
    expect_d = d_prev_d + dt.timedelta(days=1)
    thresh = dt.datetime.combine(expect_d, dt.time(8, 0), KST).timestamp()
    if mtime < thresh:
        out("", f"{path} 가 {expect_d:%m-%d} 08:00 KST 이후 갱신되지 않았다 (마지막 {when} KST) — D={d_prev} 확정 빌드 미실행 의심", 1)
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
    # 원장 건전성이 OK 여도 확정 빌드(build_morning)가 죽으면 판이 없다 — 인계 파일까지 본다(플랜 A.4).
    lpath = "data/deliver/latest_morning.json"
    if not os.path.exists(lpath):
        out("", f"원장 OK 이지만 {lpath} 없음 — 08:10 확정 빌드가 돌지 않았거나 인계 파일을 쓰지 못했다{warn_txt}", 1)
    try:
        with open(lpath, encoding="utf-8") as f:
            lat = json.load(f)
    except (OSError, ValueError) as e:
        out("", f"{lpath} 를 읽을 수 없다 ({type(e).__name__}: {e})", 1)
    lhealth = lat.get("health") if isinstance(lat.get("health"), dict) else {}
    lgen = str(lat.get("generated_at") or "")
    lsum = (f"확정판 date={lat.get('date') or '결측'} generated_at={lgen or '결측'} "
            f"stage={rc_txt(lhealth.get('stage'))} equity={rc_txt(lhealth.get('equity'))} "
            f"(stage {len(lat.get('stage_builds') or {})}표 · equity {len(lat.get('equity_builds') or {})}표)")
    if str(lat.get("date") or "") != d_prev:
        out("", f"원장 OK 이지만 확정판 인계 파일이 D={d_prev} 것이 아니다 — {lsum}{warn_txt}", 1)
    lbad = [k for k in ("stage", "equity") if lhealth.get(k) != "ok"]
    if lbad:
        out("", f"확정판 건전성 실패 {', '.join(lbad)} — {lsum}{warn_txt}", 1)
    out(when.split(" ")[-1], f"D={d_prev} 원장 건전성 OK ({when} KST) · {lsum}{warn_txt}", 0)

if check == "wics_weekly":
    # 플랜 wics-weekly: 토 03:00 잡(10:00 재시도)이 금요일 dt 스냅샷을 38코드 전부(행>0) 남겼는가.
    # 크론 자체가 안 돈 경우는 잡의 알림이 없으므로 여기서 잡는다(결정 V2-7 조용한 실패 금지).
    import sqlite3

    from daily import calendar as cal_mod
    from wics_snapshot import L1, L2
    d_prev = cal_mod.load().prev_trading_day(today_d).strftime("%Y%m%d")
    path = "data/raw/wiseindex.db"
    if not os.path.exists(path):
        out("", f"{path} 없음 — WICS 원장이 없다", 1)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n_ok = int(con.execute("SELECT count(DISTINCT sec_cd) FROM wics_raw WHERE dt = ? AND http_status = 200 "
                               "AND n_rows > 0", (d_prev,)).fetchone()[0])
        n_rows = int(con.execute("SELECT coalesce(sum(n_rows), 0) FROM wics_raw WHERE dt = ? AND http_status = 200",
                                 (d_prev,)).fetchone()[0])
        last = con.execute("SELECT max(collected_at) FROM wics_raw WHERE dt = ?", (d_prev,)).fetchone()[0]
        n_fail = int(con.execute("SELECT count(*) FROM wics_call_log WHERE dt = ? AND status = 'fail'",
                                 (d_prev,)).fetchone()[0])
    finally:
        con.close()
    expected = len(L1) + len(L2)
    summary = f"dt={d_prev} 코드 {n_ok}/{expected}(행>0) 행 {n_rows} 마지막 수집 {last or '결측'}(UTC) 실패콜 {n_fail}"
    if n_ok < expected:
        out("", f"WICS 스냅샷 없음/불완전 — {summary} · 03:00·10:00 잡이 안 돌았거나 빈 응답. 로그 logs/wics_weekly_{d_prev}.log", 1)
    try:
        stamp = dt.datetime.strptime(str(last), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.timezone.utc).astimezone(KST).strftime("%H:%M")
    except ValueError:
        stamp = ""
    out(stamp, summary, 0)

out("", f"판정 로직이 없는 check: {check}", 1)
PY
)
RC=$?
STAMP=$(printf '%s\n' "$OUT" | head -1)
BODY=$(printf '%s\n' "$OUT" | tail -n +2 | tr '\n' ' ' | cut -c1-900)
# 알림 자체가 죽으면 판정 결과도 같이 사라진다 — notify.sh 가 남긴 실패 줄을 세어 본문에 싣는다(D04).
NOTIFY_FAILED=$(awk -v since="$(date -u -d '24 hours ago' +%FT%TZ)" '$1 >= since' logs/notify_failed.log 2>/dev/null | wc -l | tr -d ' ')
BODY="$BODY | 알림 실패(24h) ${NOTIFY_FAILED:-0}건"
if [ "$RC" -eq 0 ]; then
  scripts/notify.sh info "$TITLE_OK ${STAMP:-$(TZ=Asia/Seoul date +%H:%M)}" "$BODY"
  exit 0
fi
scripts/notify.sh crit "$TITLE_BAD" "$BODY"
exit 2
