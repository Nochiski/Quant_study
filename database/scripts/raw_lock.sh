# 원장 락(raw 락) 획득 — 원장을 쓰는 체인 스크립트가 source 해서 부르는 공용 조각(배포 묶음 5-3, B-52·B-56).
#   쓰는 곳: daily_ledger.sh(06:00) · daily_build.sh(08:10) · daily_evening.sh(18:05) · daily_wise.sh · wics_weekly.sh
#   사용(루트로 cd 하고 인자를 읽은 뒤): . scripts/raw_lock.sh; raw_lock_acquire <이름> "$DRY" [대기 알림 본문] || exit $?
#   · QL_RAW_LOCK_HELD 가 있으면(부모가 쥐고 물려줌) 원장 락·대기자 락 둘 다 열지 않는다.
#   · 원장 락(fd 9)이 비었으면 바로 잡는다. 잡혀 있으면 끝날 때까지 기다렸다 이어서 돈다 — 시간 한도·재시도
#     시각 없음(N-23 ①, P9). 늦어짐·멈춤 경보는 워치독 몫이다(감시이지 제어가 아니다).
#   · 스크립트마다 대기자는 하나(N-27 ⑥): 기다리기 전에 대기자 락 `<원장 락>.<이름>.wait`(fd 8)을 비대기로
#     잡는다. 못 잡으면 같은 스크립트가 이미 기다리는 중이라 info 를 남기고 rc 3 — 풀리는 순간 같은 체인이
#     여러 번 도는 일(B-52 ③)을 막는다. 원장 락을 잡으면 대기자 락은 놓는다(다음 실행이 대기자가 될 수 있게).
#   · 대기형 flock 이 실패하면 warn 을 남기고 rc 3 — 락 없이 원장을 쓰지 않는다.
#   · 실제로 기다린 경우, 대기 시작 직전과 락을 잡은 직후의 KST 날짜가 다르면(자정을 넘는 점유) crit 을 남기고
#     원장 락을 놓고 rc 3 — 수집 대상일(D·오늘)이 대기 뒤에 정해져 그날 값을 그대로 받을 수 없다(B-51, P1).
#     다시 돌릴지는 사람이 정한다.
#   · 알림은 notify.sh(logs/notify.log 기록만)이고, 둘째 인자(dry-run 표시)가 비어 있지 않으면 남기지 않는다.
#     대기 시작·끝 줄은 실시간 출력(크론 cron_*.log)에 나온다.
#   · 끝나면 LOCK(경로)·LOCK_WAITED(기다렸으면 "N초 (시작 ~ 끝)", 아니면 빈 값)가 남고 QL_RAW_LOCK_HELD=1 을 export.
#   원장 락 래퍼(`flock <원장 락> <스크립트>`) 안에서 부르지 않는다 — 같은 파일을 새로 열어 자기 자신을 기다린다.
#   QL_RAW_LOCK_FILE 은 테스트 전용(락 파일 경로 덮어쓰기)이다 — 운영 크론·대화형 셸에 남겨 두지 않는다(B-56).
#   QL_RAW_LOCK_WAKE_DATE(YYYY-MM-DD)도 테스트 전용 — 락을 잡은 직후의 KST 날짜를 덮어쓴다. 운영에선 비워 둔다.
LOCK="${QL_RAW_LOCK_FILE:-/tmp/quant_ledger_raw.lock}"
LOCK_WAITED=""
raw_lock_acquire() {
  local name="$1" dry="${2:-}" note="${3:-}" waiter w0 w0_kst w1_kst day0 day1
  if [ -n "${QL_RAW_LOCK_HELD:-}" ]; then return 0; fi
  exec 9>"$LOCK"
  if ! flock -n 9; then
    waiter="$LOCK.$name.wait"
    exec 8>"$waiter"
    if ! flock -n 8; then
      echo "[$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')] $name 이미 대기 중인 실행 있음 — 이번 실행 건너뜀($waiter)"
      [ -z "$dry" ] && scripts/notify.sh info "$name 이미 대기 중인 실행 있음 — 이번 실행 건너뜀" \
        "다른 원장 작업이 $LOCK 을 쥐고 있고 같은 스크립트의 앞 실행이 이미 기다린다($waiter) — 풀리면 그 실행이 이어서 돈다"
      return 3
    fi
    w0=$(date +%s); w0_kst=$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'); day0=$(TZ=Asia/Seoul date +%Y-%m-%d)
    echo "[$w0_kst] $name 원장 락 대기 시작 — 다른 원장 작업이 $LOCK 을 쥐고 있다(끝나면 이어서 돈다)"
    [ -z "$dry" ] && scripts/notify.sh info "$name 원장 락 대기" \
      "시작 $w0_kst — ${note:-다른 원장 작업이 $LOCK 을 쥐고 있다. 끝나면 이어서 돈다}"
    if ! flock 9; then
      [ -z "$dry" ] && scripts/notify.sh warn "$name 락 실패" "$LOCK 을 기다리다 flock 이 실패했다 — 이번 실행 건너뜀"
      return 3
    fi
    flock -u 8; exec 8>&-
    w1_kst=$(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST')
    day1="${QL_RAW_LOCK_WAKE_DATE:-$(TZ=Asia/Seoul date +%Y-%m-%d)}"
    if [ "$day1" != "$day0" ]; then
      echo "[$w1_kst] $name 원장 락 대기 중 날짜가 바뀜(시작 $day0 → 지금 $day1) — 이번 실행 중단"
      [ -z "$dry" ] && scripts/notify.sh crit "$name 원장 락 대기 중 날짜가 바뀜(시작 $day0 → 지금 $day1) — 이번 실행 중단" \
        "대기 시작 $w0_kst · 락 획득 $w1_kst | 수집 대상일 기준이 바뀌어 그날 값을 그대로 받을 수 없다 — 다시 돌릴지는 사람 판단(README '원장 락')"
      flock -u 9; exec 9>&-
      return 3
    fi
    LOCK_WAITED="$(( $(date +%s) - w0 ))초 ($w0_kst ~ $w1_kst)"
    echo "[$w1_kst] $name 원장 락 대기 끝 — $LOCK_WAITED"
  fi
  export QL_RAW_LOCK_HELD=1
}
