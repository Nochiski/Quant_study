#!/usr/bin/env bash
# 잠정·확정 빌드 공통 체인 — 플랜 v2 §4 Task B.1. 저녁(잠정)·아침(확정)이 같은 순서를 돈다.
#   사용: scripts/build_chain.sh <evening|morning> --date YYYYMMDD [--dry-run]
#   호출: scripts/build_evening.sh (18:15 KST 크론) · scripts/build_morning.sh (daily_build.sh 안)
#   순서: 빌드 락 → 원장 스냅샷 5 DB → stage 전량(basis) → stage 건전성 C1~C5
#         → equity 전량(basis) → equity catalog → 인계 JSON → 스냅샷·_pinned GC → 알림
#   저녁·아침의 차이는 basis(빌드 id 접두어 e_/m_)·대상일·로그 디렉토리뿐이라 한 파일에 둔다.
#   두 체인이 갈라지면 잠정판과 확정판이 조용히 다른 규칙으로 지어진다.
#   로그: logs/<basis>/build_<D>.log (단계별 상세는 기존 위치 logs/stage_all/·logs/equity/ 그대로)
#   알림: 성공 info "잠정판 준비 hh:mm (stage n분·equity n분)" · 실패 단계에서 crit (결정 V2-7)
set -uo pipefail
cd /home/kael/quant-ledger || { echo "quant-ledger 홈으로 이동 실패 — 잘못된 디렉토리에서 빌드하지 않는다" >&2; exit 4; }
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
PY=.venv/bin/python

BASIS="${1:?usage: build_chain.sh <evening|morning> --date YYYYMMDD [--dry-run]}"; shift
case "$BASIS" in
  evening) LABEL="잠정판" ;;
  morning) LABEL="확정판" ;;
  *) echo "unknown basis: $BASIS (allowed: evening, morning)" >&2; exit 2 ;;
esac
D=""; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --date) D="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
D="${D:-$(TZ=Asia/Seoul date +%Y%m%d)}"
kst() { TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST'; }

if [ -n "$DRY" ]; then
  # 계획만 출력한다 — 락·스냅샷·빌드·알림 전부 없음 (결정 V2-7 의 유일한 예외)
  echo "════ dry-run build_chain basis=$BASIS D=$D $(kst) ════"
  echo "  1. 빌드 락 /tmp/quant_ledger_build.lock flock -n (자식은 QL_BUILD_LOCK_HELD=1)"
  echo "  2. 스냅샷: stage.snapshot.make_snapshot(data/raw 5 DB → data/snapshots/snap_<ts>)"
  echo "  3. stage:  scripts/run_stage_all.sh <snap> --basis $BASIS   (66표, ≈22.5분)"
  echo "  4. 건전성: $PY -m stage.health --basis $BASIS --date $D --out logs/health/stage_${D}_${BASIS}.json"
  echo "  5. equity: scripts/equity_rebuild_all.sh $BASIS --basis $BASIS   (29표, ≈8분)"
  echo "  6. 카탈로그: $PY -m equity catalog"
  echo "  7. 인계:   data/deliver/latest_${BASIS}.json + data/deliver/history/${D}_${BASIS}.json"
  echo "  8. GC:     stage.snapshot.gc(keep=6, protect=현재 판이 선 snapshot_id) + equity.inputs.gc_pinned(이력 30일·월말 보호)"
  echo "  9. 알림:   info \"$LABEL 준비 hh:mm\" (실패 단계에서 crit)"
  echo "════ dry-run 종료 (원장·스냅샷·판 무변경, 알림 없음) ════"
  exit 0
fi

LOCK=/tmp/quant_ledger_build.lock
if [ -z "${QL_BUILD_LOCK_HELD:-}" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    scripts/notify.sh warn "$LABEL 빌드 락 실패" "다른 빌드가 $LOCK 을 쥐고 있다 — basis=$BASIS D=$D 이번 실행 건너뜀"
    exit 3
  fi
  export QL_BUILD_LOCK_HELD=1
fi

mkdir -p "logs/$BASIS" logs/health data/deliver/history
LOG="logs/$BASIS/build_${D}.log"
RUN=$(mktemp)
FAILED=""
SNAP=""; STARTED_ISO=""; STAGE_S=0; EQUITY_S=0; H_STAGE="fail"; H_EQUITY="fail"
step() {
  local name="$1"; shift
  echo "──── $name 시작 $(kst) ────"
  "$@"; local rc=$?
  echo "──── $name 종료 rc=$rc $(kst) ────"
  if [ "$rc" -ne 0 ]; then FAILED="$FAILED $name(rc=$rc)"; return 1; fi
  return 0
}
snapshot_step() {
  # 5 DB 전부 뜬다. stg_price_daily 의 G9 가 kiwoom 원장을 직접 조인하므로 부분 스냅샷은 금지
  # (STAGE_DESIGN §2). 저녁 체인은 DART 갈래까지 끝난 뒤에 호출되므로 dart.db 쓰기와 겹치지 않는다.
  local out; out=$(mktemp)
  $PY - "$out" <<'PY'
import sys
from pathlib import Path

from stage import rules, snapshot

raw = {db: Path("data/raw") / name for db, name in rules.LEDGER_FILES.items()}
snap = snapshot.make_snapshot(raw, Path("data/snapshots"))
sizes = " ".join(f"{k}={v.bytes / 1e9:.2f}GB" for k, v in snap.files.items())
print(f"  스냅샷 {snap.snapshot_id} {sizes}")
Path(sys.argv[1]).write_text(snap.snapshot_id, encoding="utf-8")
PY
  local rc=$?
  SNAP=$(cat "$out" 2>/dev/null)
  rm -f "$out"
  [ "$rc" -eq 0 ] && [ -n "$SNAP" ]
}
stage_step() {
  # run_stage_all 은 표가 폐기돼도 rc 0 으로 끝까지 간다 — 판정은 다음 단계(stage.health)가 한다.
  scripts/run_stage_all.sh "$SNAP" --basis "$BASIS" || return 1
  tail -n 3 logs/stage_all/summary.tsv
}
health_step() {
  local skip; skip=$(cat logs/stage_all/skipped.txt 2>/dev/null)
  $PY -m stage.health --stage-root data/stage --basis "$BASIS" --date "$D" \
     --out "logs/health/stage_${D}_${BASIS}.json" --skip "${skip:-}" --started-at "$STARTED_ISO"
}
equity_step() { scripts/equity_rebuild_all.sh "$BASIS" --basis "$BASIS"; }
catalog_step() { $PY -m equity catalog; }
deliver_step() {
  # Kael-alpha·워치독이 읽는 인계 파일. latest_* 는 덮어쓰고 history/ 는 영구 보관한다(B.3 ①층).
  $PY - "$D" "$BASIS" "$SNAP" "$STAGE_S" "$EQUITY_S" "$H_STAGE" "$H_EQUITY" <<'PY'
import datetime as dt
import json
import os
import sys
from pathlib import Path

date, basis, snap, stage_s, equity_s, h_stage, h_equity = sys.argv[1:8]


def current_builds(root: Path) -> dict[str, str]:
    """표 → 현재 판 build_id. MANIFEST 포인터가 정본이다(맨 glob 금지 계약)."""
    out: dict[str, str] = {}
    for path in sorted(root.glob("*/MANIFEST.json")):
        cur = json.loads(path.read_text(encoding="utf-8")).get("current_build")
        if cur:
            out[path.parent.name] = cur
    return out


home = os.environ.get("QL_HOME", ".")
payload = {
    "date": date,
    "basis": basis,
    "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "stage_snapshot_id": snap,
    "stage_builds": current_builds(Path("data/stage")),
    "equity_builds": current_builds(Path("data/equity")),
    "equity_root": os.path.join(home, "data", "equity"),
    "elapsed_s": {"stage": int(stage_s), "equity": int(equity_s)},
    "health": {"stage": h_stage, "equity": h_equity},
}
text = json.dumps(payload, ensure_ascii=False, indent=1)
Path("data/deliver/history").mkdir(parents=True, exist_ok=True)
Path(f"data/deliver/latest_{basis}.json").write_text(text, encoding="utf-8")
Path(f"data/deliver/history/{date}_{basis}.json").write_text(text, encoding="utf-8")
print(f"  인계 latest_{basis}.json stage {len(payload['stage_builds'])}표 "
      f"equity {len(payload['equity_builds'])}표 snapshot={snap}")
PY
}
gc_step() {
  # ① 원장 스냅샷 GC(keep=6, 현재 stage 판이 선 스냅샷 보호)
  # ② equity `_pinned/` GC(플랜 v2 §4 B.3 ③) — 인계 이력 `data/deliver/history/*.json` 이 가리키는
  #    stage 판을 30일간, 월말 확정판(그 달의 마지막 morning 판)은 영구 보호한다. 참조된 stage 판이
  #    남아 있으면 equity 판이 keep 밖으로 지워진 뒤에도 같은 입력으로 재빌드(EG5a)해 스코어를 되짚을 수 있다.
  $PY - <<'PY'
import datetime as dt
import json
from pathlib import Path

from equity import inputs
from stage import snapshot

protect = snapshot.current_snapshot_ids(Path("data/stage"))
r = snapshot.gc(Path("data/snapshots"), keep=snapshot.KEEP_DEFAULT, protect=protect)
print(f"  {r.summary()} (보호 {len(protect)}판)")

KEEP_DAYS = 30
today = dt.date.today()
by_month: dict[str, tuple[str, Path]] = {}      # 월 → 그 달의 마지막 morning 인계(월말 확정판)
recent: list[Path] = []
for path in sorted(Path("data/deliver/history").glob("*.json")):
    stem = path.stem                             # <YYYYMMDD>_<basis>
    date_s, _, basis = stem.partition("_")
    if len(date_s) != 8 or not date_s.isdigit():
        continue
    d = dt.date(int(date_s[:4]), int(date_s[4:6]), int(date_s[6:8]))
    if (today - d).days <= KEEP_DAYS:
        recent.append(path)
    if basis == "morning":
        month = date_s[:6]
        if month not in by_month or by_month[month][0] < date_s:
            by_month[month] = (date_s, path)
month_end = [p for m, (_, p) in by_month.items() if m < today.strftime("%Y%m")]
ids: set[str] = set()
for path in recent + month_end:
    try:
        rep = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"  경고: 인계 이력 {path} 을 읽지 못해 보호 목록에서 뺐다")
        continue
    ids.update(str(v) for v in (rep.get("stage_builds") or {}).values())
g = inputs.gc_pinned(Path("data/equity"), protect=ids)
print(f"  _pinned gc: 삭제 {g.n_removed} · 참조 유지 {len(g.kept_referenced)} · 보호 유지 {len(g.kept_protected)}"
      f" · 최신 유지 {len(g.kept_recent)} · 확보 {g.freed_bytes / 1e9:.2f}GB"
      f" (이력 최근 {len(recent)}건 + 월말 {len(month_end)}건 → 보호 id {len(ids)})")
for e in g.errors:
    print(f"  _pinned gc 오류: {e}")
PY
}
{
echo "════ [$(kst)] build_chain basis=$BASIS D=$D 시작 ════"
T0=$(date +%s)
STARTED_ISO=$(date -u +%FT%TZ)
if step "스냅샷" snapshot_step && step "stage 전량" stage_step; then
  STAGE_S=$(( $(date +%s) - T0 ))
  if step "stage 건전성" health_step; then H_STAGE="ok"; fi
fi
if [ "$H_STAGE" = "ok" ]; then
  T1=$(date +%s)
  if step "equity 전량" equity_step && step "equity catalog" catalog_step; then H_EQUITY="ok"; fi
  EQUITY_S=$(( $(date +%s) - T1 ))
else
  echo "  stage 건전성 실패 — equity 는 어제 판을 유지한다(잘못된 stage 위에 짓지 않는다)"
fi
step "인계 파일" deliver_step
step "스냅샷 GC" gc_step
echo "════ 종료 stage=$H_STAGE equity=$H_EQUITY ${STAGE_S}s+${EQUITY_S}s $(kst) ════"
} > "$RUN" 2>&1
cat "$RUN" >> "$LOG"
SUMMARY=$(printf 'D=%s snapshot=%s stage %s(%d분) equity %s(%d분) | %s' \
  "$D" "${SNAP:-없음}" "$H_STAGE" "$((STAGE_S / 60))" "$H_EQUITY" "$((EQUITY_S / 60))" \
  "$(grep -E '^stage 건전성|^  스냅샷|^  인계|^  snapshot gc' "$RUN" | tail -4 | tr '\n' ' ')" \
  | cut -c1-900)
if [ "$H_STAGE" != "ok" ] || [ "$H_EQUITY" != "ok" ]; then
  scripts/notify.sh crit "$LABEL 빌드 실패${FAILED:+: $FAILED}" "$SUMMARY | 로그 $LOG"
  rm -f "$RUN"; exit 2
fi
scripts/notify.sh info "$LABEL 준비 $(TZ=Asia/Seoul date +%H:%M)" \
  "$LABEL D=$D 준비 완료 (stage $((STAGE_S / 60))분 · equity $((EQUITY_S / 60))분) | $SUMMARY"
rm -f "$RUN"
