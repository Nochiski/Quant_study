# P0 Task 0.9 — 서버 전용 파일 처분 판정

조사 시각 2026-09-09 · 저장소 `feat/daily-p0` @ `74181bd` · 서버 `kael-server:~/quant-ledger` (읽기 전용)

## 0. 조사 중 상황이 바뀌었다 (먼저 읽을 것)

조사를 시작할 때 저장소는 `main`(`db2e507`)이었으나 도중에 `feat/daily-p0`(`74181bd`, 커밋 5개
추가)로 바뀌었고, **그 사이 누군가 서버로 배포를 했다.** 09-09 리뷰 문서(§6)가 기록한 드리프트
목록은 이미 일부 해소되어 있다:

| 파일 | 09-09 리뷰 시점 | 지금(재실측) |
|---|---|---|
| `src/api.py` | — | 저장소=서버 `d8026c01` (v3 DART 키 폴백 제거분 배포됨) |
| `scripts/run_stage_all.sh` | 저장소가 앞섬 | 저장소=서버 `2e808a83` (배포됨) |
| `scripts/daily_wise.sh` | — | 저장소=서버 `e7b844d7` (notify 연동분 배포됨) |
| `scripts/gc.sh`·`notify.sh`·`sync_calendar.sh` | 저장소에 없었음 | 저장소·서버 양쪽에 존재 |

따라서 **남은 드리프트는 아래 표가 전부**다.

## 1. 처분 표

### 1-A. 서버에만 있는 것

| 파일 | 판정 | 근거 | 명령 |
|---|---|---|---|
| `src/equity_s23/` (29모듈+sql 29+fixtures 28) | **서버 삭제** | ① 서버 `src/equity` 48파일 md5 가 저장소 `database/src/equity` 와 **전부 일치** → 저장소가 정본이고 서버 `equity` 는 최신 사본. ② `diff -rq src/equity src/equity_s23` 에서 `Only in equity_s23` 이 **0건**(pycache 제외) → 부분집합. ③ `equity_s23/model.py` 의 `RULES_VERSION = "e1.7.0"`(mtime 09-06 15:44) vs `equity/model.py` `"e1.14.0"`(09-08 04:22) → **7개 판(e1.8.0~e1.14.0) 뒤처진 구본**. ④ 서버 `scripts`·`src` 어디서도 `equity_s23` 을 참조하지 않는다(`grep` RC=1). 서버 `~/quant-ledger` **전체**(.venv·pycache 제외) 로 넓혀도 문자열 `equity_s23` 을 담은 파일은 `baseline_seed_s23.json` 2개뿐이고, 그 안의 값은 테스트 파일명 `test_equity_s23_price_adj.py` 라 디렉토리 참조가 아니다. 크론(`crontab -l`)에서 quant-ledger 항목은 `scripts/daily_wise.sh` 단 하나이고, systemd 타이머 `quant-fetch`·`quant-enrich`·`quant-consensus` 는 전부 `kael-system/quant-pipeline` 소속이라 무관하다. 저장소 문서 7건의 언급은 전부 테스트 파일명 `test_equity_s23_price_adj.py` 이거나 이 조사 자체를 적은 리뷰/플랜이다. ⑤ S23 규칙(`rules_s23.py`, `price_adj_daily`)은 이미 저장소 `database/src/equity/rules_s23.py` 에 있다. | `rsync --delete` 가 자동 제거 (§3 dry-run 확인) |
| `src/probe_krx_timing.py` (1,082 B, mtime 09-03) | **전문 회수(문서) 후 서버 삭제** | 산출 DB `data/raw/krx_timing.db` 가 **서버에 존재하지 않는다** → 한 번도 안 돌았거나 관측이 남지 않았다. 관측 자산이 없으므로 회수 가치는 **코드 패턴뿐**이고, 그 패턴은 §2 에 전문으로 보존했다. Task 0.7(키움 프로브)이 이걸 본뜨면 목적 달성. 저장소 `database/src` 로 되살릴 실익 없음. 참조 스크립트·크론 0건. | `rsync --delete` 가 자동 제거 |
| `src/export_csv.py` (6,651 B, mtime 09-03) | **서버 삭제** | 경로가 **로컬 맥 하드코딩**: `D = "/Users/claudeoscarmonet/Desktop/Quant_study/workspace/dongmin/data"`. 서버에서 실행 불가(경로 부재 → 전 테이블 `continue` → 빈 산출). 게다가 PR #66 이 폐기한 옛 `workspace/dongmin` 경로 체계이고, 읽는 표(`equity_test.db`, `daily_prices`, `financials` …)는 stage/equity 층 이전의 죽은 스키마다. 참조 0건. PR #66 에서 의도적 삭제(`b9f2aaf`). | `rsync --delete` 가 자동 제거 |
| `src/api.py.bak.20260824144311` (6,735 B, mtime 08-24) | **서버 삭제** | 현행 `api.py` 대비 109줄 diff 로 **뒤처진 백업**: DART 키가 1개뿐(현행 5개), 키움 8005(토큰 조기 폐기) 강제 재발급 재시도 없음. 백업 목적은 git 이 대신한다. **삭제 전 키 노출 확인 필요** — 이 파일은 키를 코드에 담지 않고 환경변수 이름만 나열하므로 안전하나, 서버 백업 관행 자체를 끊는 것이 이 판정의 요지. | `rsync --delete` 가 자동 제거 |
| `src/rebuild_share.py` (18,058 B, mtime 08-25) | **유지 (배포본으로 재분류)** | md5 `e9a5c10869993a0dbcece278c7fe17fb` 가 저장소 `backend/ops/rebuild_share.py` 와 **바이트 동일**. 즉 서버 전용 파일이 아니라 **backend 정본의 배포본**이다. `database/src` 로 복사하면 이중 정본이 된다. → `database/` 로 회수하지 말고, deploy.sh 에서 `--exclude` 한 뒤 `backend/ops/` 에서 따로 미는 별도 라인으로 다룬다. 산출물 `share/latest → v/20260827T0930` 은 08-27 이후 갱신 없음(크론 없음, 수동 실행). | deploy.sh §3 의 3번째 rsync 라인 |
| `src/sync_v3_wise.py` (4,264 B, mtime 09-03) | **유지 (삭제 보류 — 사람 승인 사안)** | **플랜 `2026-09-09-daily-incremental.md:195` 는 "서버에서도 지운다" 라고 적었는데 이는 근거와 충돌한다.** `database/src/equity/rules_s17.py:458-467` 이 게이트 근거(`_V3_MIRROR_NOTE`)로 이렇게 못박고 있다: v3 미러가 `daily_wise` 체인에서 2026-09-02 에 빠져 `wisereport.v3_consensus_revision_daily` 가 08-31 에 멈췄고, 원본 `kael-system-v3/quant.db.consensus_revision_daily` 는 계속 쌓이므로 **"미러를 재개하면 소급 복구되지만 운영 변경이라 사람 승인이 필요하다"**. 스크립트를 지우면 재개 선택지가 사라진다. 빠진 사유("직접 수집이 같은 값을 커버")도 rules_s17 이 반박한다 — wise 직접 수집에는 영업이익·순이익이 없다. | deploy.sh `--exclude 'sync_v3_wise.py'` |
| `src/.kw_token.json`·`src/.kis_token.json` | **유지 (필수)** | 런타임이 굽는 토큰 캐시. `src/` 안에 있으므로 `--delete` 가 **반드시 지운다** → 다음 수집이 통째로 실패한다. exclude 누락이 이 배포의 최대 사고 지점. | deploy.sh `--exclude '.kw_token.json' --exclude '.kis_token.json'` |
| `src/__pycache__`·`src/*/.ruff_cache` | 유지 | 런타임 산출물 | `--exclude '__pycache__/' --exclude '.ruff_cache/'` |

### 1-B. 저장소에만 있는 것 (미배포 5개)

| 파일 | 판정 | 근거 |
|---|---|---|
| `database/scripts/check_baseline_lock.py` | **배포 불필요 (로컬 실행)** | docstring 자체가 로컬 워크플로다: `scp kael-server:~/quant-ledger/data/equity/baseline.json /tmp/… && uv run python database/scripts/check_baseline_lock.py /tmp/server_baseline.json`. 대조 대상은 저장소 `baseline_locked.json` 이라 서버에 사본이 없다. **플랜 P0 Task 0.6 이 이 스크립트로 서버 파일을 대조하는 것도 로컬 실행이 전제**다. 다만 배포해도 해롭지 않으므로(§3 dry-run 에 포함) 굳이 exclude 하지 않는다. |
| `database/scripts/check_field_map.py` | 배포 불필요 (로컬) | `backend/FACTORS.md` 와 `database/docs/EQUITY_FIELD_MAP.md` 를 읽는다 — 둘 다 서버에 없다. 배포하면 서버에서는 항상 rc=2. |
| `database/scripts/fetch_equity_local.sh` | 배포 불필요 (로컬) | `rsync kael-server:… → 로컬` 방향이다. 서버에서 실행하면 자기 자신을 당긴다. |
| `database/scripts/run_mvp_backtest.py` | 배포 불필요 (로컬) | `uv run --project backend` 로 워크벤치 컨테이너를 부팅한다. 서버에 `backend/` 트리가 없다. |
| `database/scripts/doc_fixtures_from_sample.py` | **배포 (저장소 정본)** | 서버 `7fef12c1`(09-04) vs 저장소 `2452c881`. diff 는 **docstring 3곳의 줄바꿈·문구뿐이고 로직은 동일**. 저장소판이 `DOC_DESIGN v1.1` 의 판 번호와 "사람이 대조한 뒤" 문구를 잃었다는 사소한 정보 손실이 있으나 동작에 영향 없음. 저장소가 정본이므로 덮어쓴다. |

`scripts/run_stage_all.sh` 는 **이미 배포됐다**(md5 동일). 저장소판이 전제하는 것은 하나 —
문서층 4테이블은 `data/stage/_tmp/doc/<SNAP>/summary.json` 이 있고 `"status": "ok"` 일 때만
`ORDER` 에 붙고, 없으면 건너뛰며 프리패스 명령을 안내한다. 서버에 그 캐시가 없으면
`stg_doc_*` 4개가 조용히 빠지는 것이 아니라 **안내 한 줄을 찍고 빠진다**(가드가 의도대로 동작).
빌드 락(`/tmp/quant_ledger_build.lock`, `QL_BUILD_LOCK_HELD`)도 이미 들어가 있다.

## 2. `src/probe_krx_timing.py` 전문 (Task 0.7 참고용 · 키 값 없음 확인)

키·시크릿은 없다. 인증은 전부 `import api` 에 위임하고 환경변수는 건드리지 않는다.

```python
"""KRX 가 당일 데이터를 언제부터 주는지 관측. 매시 1회, 당일·전일 2콜."""
import os, sys, sqlite3, time
from datetime import datetime, timedelta
BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import api

DB = f"{BASE}/data/raw/krx_timing.db"
con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS probe(
  ts_kst TEXT NOT NULL, target_dd TEXT NOT NULL, offset_d INTEGER NOT NULL,
  n_rows INTEGER NOT NULL, PRIMARY KEY (ts_kst, target_dd))""")
kst = datetime.utcnow() + timedelta(hours=9)
for off in (0, 1):                      # 당일 · 전일
    d = kst - timedelta(days=off)
    if d.weekday() >= 5: continue       # 주말은 애초에 데이터가 없다
    dd = d.strftime("%Y%m%d")
    try:    n = len(api.krx("sto/stk_bydd_trd", dd))
    except Exception: n = -1
    con.execute("INSERT OR REPLACE INTO probe VALUES (?,?,?,?)",
                (kst.strftime("%Y-%m-%d %H:%M"), dd, off, n))
    time.sleep(0.4)
con.commit(); con.close()
```

Task 0.7 이 본뜰 때 고칠 점 3가지:
1. `n = -1` 로 예외를 뭉갠다 — 인증 실패·레이트리밋·네트워크를 구분 못 한다. 키움 프로브는
   실패 사유(예외 클래스 또는 return_code)를 별도 컬럼에 남길 것.
2. PK 가 `(ts_kst, target_dd)` 이고 `ts_kst` 가 분 단위라 같은 시각 재실행이 덮어쓴다. 관측
   이력을 쌓으려면 실행 시각을 초 단위로 두거나 append-only 로.
3. 휴장일 판정이 `weekday() >= 5` 뿐이다 — 공휴일에 `n=0` 이 찍히면 "아직 안 나옴"과
   "원래 없음"이 구분되지 않는다. `trading_calendar` 를 참조해야 한다.

## 3. `database/scripts/deploy.sh` 초안

```bash
#!/usr/bin/env bash
# quant-ledger 코드 배포 — 저장소 database/{src,scripts} 를 서버 ~/quant-ledger/{src,scripts} 로
# 밀어 넣는다. **정본은 저장소**이고 서버는 사본이다(P0 Task 0.9 판정).
#
#   database/scripts/deploy.sh            # dry-run (기본, 아무것도 안 바꾼다)
#   database/scripts/deploy.sh --apply    # 실제 배포
#
# --delete 를 쓰는 이유: 서버에만 남은 작업 사본(equity_s23/ 같은 것)이 다음 사람에게
# "둘 중 어느 쪽이 정본인가" 를 다시 묻게 만든다. 저장소에 없으면 서버에도 없어야 한다.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # …/database
REMOTE="${QL_REMOTE:-kael-server}"
ROOT="${QL_REMOTE_ROOT:-quant-ledger}"                       # 원격 홈 기준 상대경로

DRY="--dry-run"
[ "${1:-}" = "--apply" ] && DRY=""

# 서버에만 있어야 하는 것 — 지우면 안 된다.
#   토큰 2종: 런타임이 굽는 캐시. 저장소에 올릴 수 없다.
#   sync_v3_wise.py: v3 컨센서스 미러. daily_wise 체인에서 2026-09-02 에 빠졌지만
#     rules_s17 이 "재개하면 소급 복구, 단 운영 변경이라 사람 승인 필요" 로 근거에 박아 두었다.
#     승인 전까지는 지우지 않는다.
#   rebuild_share.py: 정본이 저장소 backend/ops/ 쪽이다(md5 동일). 아래 별도 라인으로 민다.
COMMON=(--exclude '__pycache__/' --exclude '.ruff_cache/' --exclude '.venv/'
        --exclude '*.pyc' --exclude '.DS_Store')
SRC_KEEP=(--exclude '.kw_token.json' --exclude '.kis_token.json'
          --exclude 'sync_v3_wise.py' --exclude 'rebuild_share.py')

echo "== src/  ($REPO/src/ → $REMOTE:~/$ROOT/src/)"
rsync -avz $DRY --delete "${COMMON[@]}" "${SRC_KEEP[@]}" \
      "$REPO/src/" "$REMOTE:$ROOT/src/"

echo "== scripts/  ($REPO/scripts/ → $REMOTE:~/$ROOT/scripts/)"
rsync -avz $DRY --delete "${COMMON[@]}" \
      "$REPO/scripts/" "$REMOTE:$ROOT/scripts/"

# 워크벤치 공유 산출물 재생성기. 저장소 정본은 backend/ops/ 이고 서버는 src/ 에 두고 쓴다.
echo "== backend/ops/rebuild_share.py → src/rebuild_share.py"
rsync -avz $DRY "$REPO/../backend/ops/rebuild_share.py" "$REMOTE:$ROOT/src/rebuild_share.py"

[ -n "$DRY" ] && echo && echo "(dry-run 이었다. 실제로 밀려면 --apply)"
```

설계 결정 3가지:
- **dry-run 이 기본, `--apply` 가 명시적.** `--delete` 를 쓰는 스크립트에서 기본값이 파괴적이면
  안 된다.
- **`-a` 는 쓰되 `-c` 는 안 쓴다.** 판독용 dry-run 은 `-c`(체크섬)로 돌리면 "정말 내용이 다른
  것"만 보이지만, 실배포에서 매번 전 파일 체크섬을 굽는 것은 낭비다. mtime+size 로 충분하다.
- **`src/rebuild_share.py` 는 exclude 후 별도 push.** 두 정본을 만들지 않으면서도 `--delete`
  희생양이 되지 않게 하는 유일한 방법.

## 4. dry-run 실측 결과 (2026-09-09, `--dry-run` 만 실행 · 서버 무변경)

체크섬 모드(`-avzc`)로 돌려 "실제로 내용이 다른 것"만 남겼다.

### 4-A. `database/src/` → `~/quant-ledger/src/`

**삭제 예정 113항목** — `equity_s23/` 트리 전부 + 파일 3개. 그 외 전송 0건
(= `src` 최상위 22 py + `stage/` + `equity/` 는 저장소와 서버가 이미 완전 동일).

```
deleting equity_s23/sql/…                (29 파일)
deleting equity_s23/fixtures/…           (28 파일)
deleting equity_s23/{views,rules_sample,rules_s01~s23,model,inputs,gates,
                     contract,catalog,build,baseline,__main__,__init__}.py
deleting equity_s23/baseline_locked.json · baseline_seed_s01~s23.json
deleting equity_s23/sql/ · equity_s23/fixtures/
deleting probe_krx_timing.py
deleting export_csv.py
deleting api.py.bak.20260824144311
--- 변경/신규 전송 ---
(없음. "cannot delete non-empty directory: equity_s23" 는 dry-run 특유의 안내로,
 --apply 시에는 하위가 먼저 지워져 디렉토리까지 제거된다)
```

**exclude 가 실제로 지켜졌는지 확인**: `sync_v3_wise.py`·`rebuild_share.py`·
`.kw_token.json`·`.kis_token.json` 은 삭제 목록에 **없다**. 의도대로다.

### 4-B. `database/scripts/` → `~/quant-ledger/scripts/`

**삭제 예정 0건.** 전송 5건:

```
check_baseline_lock.py          (신규 · 로컬 전용이나 무해)
check_field_map.py              (신규 · 로컬 전용이나 무해)
doc_fixtures_from_sample.py     (갱신 · docstring 3곳만 다름)
fetch_equity_local.sh           (신규 · 로컬 전용이나 무해)
run_mvp_backtest.py             (신규 · 로컬 전용이나 무해)
```

## 5. 실행 순서 제안

1. `database/scripts/deploy.sh` 를 위 초안대로 커밋.
2. `deploy.sh`(인자 없음)로 dry-run 재확인 — 삭제 목록이 §4-A 와 같은지.
3. 서버 `src/equity_s23` 를 지우기 전에 **최후 백업 한 번**:
   `ssh kael-server 'cd ~/quant-ledger && tar czf /tmp/equity_s23_20260909.tgz src/equity_s23'`
   (구본이라 되살릴 일은 없겠지만, `--delete` 첫 실행이므로.)
4. `deploy.sh --apply`.
5. 배포 직후 검증: `ssh kael-server 'cd ~/quant-ledger && ls src/.kw_token.json src/.kis_token.json src/sync_v3_wise.py src/rebuild_share.py && ls -d src/equity_s23 2>&1'`
   → 앞 4개는 있고 마지막은 "No such file" 이어야 한다.
6. `sync_v3_wise.py` 재개 여부는 **별건으로 사람에게 물을 것** (rules_s17 `_V3_MIRROR_NOTE`).
