# v3 컷오버 되돌리기 절차서 (QL-I)

> 정본: [컷오버 트랙](plans/2026-10-10-cutover-track.md) §0·§4·T-20·T-21·T-35·**T-42**, [`COMPAT_LAYER.md`](COMPAT_LAYER.md) §8·§8-1(V3-A~E), [`DECISIONS.md`](DECISIONS.md) N-39(서버 밖 백업 보류)·N-42 Q4.
> 도구: `scripts/v3_backup.sh` · `scripts/v3_restore.sh`. 규칙 정본은 `src/compat/v3_restore.py` 머리 주석이고, 복원 뒤 반영 규칙은 `src/compat/v3_post.py` 머리 주석이다.
> 명령은 서버의 quant-ledger 홈(`cd ~/quant-ledger`)에서 친다. 아래 예시 경로는 `$HOME` 표기다. 저장소가 공개라 실제 홈 경로·계정은 적지 않는다.

## 0. 한눈에

컷오버 날(10-19) 순서는 이렇다. ① v3 quant.db 고정 백업 2벌(§1) → ② V3-A~E(v3 크론·체인 변경) → ③ 첫 반영 `v3_post.sh`(V3-C — 매일과 같은 증분 창, T-46) → ④ 원천 전환(PR-9). 되돌리기 창은 컷오버일부터 **5거래일**(10-19~23)이다. v3 는 매 수집에서 종목마다 최근 5행을 다시 받으므로, 창 안에 되돌리면 빈 날을 v3 가 스스로 메운다(§3).

| 되돌리는 것 | 그대로 두는 것 |
|---|---|
| v3 쪽 변경 V3-A·B·D·E(crontab · `job_runner.py` · 휴장 파일 · uni `kael_db.py`)와 컷오버 날 넣은 QL-L 감시 크론 | quant-ledger 연구 DB·원장·모델 DB(장 마감 판 산출물) |
| v3 quant.db 의 가격 등 **7표**(compat 9표에서 점수 두 표를 뺀 것 — V3-C 와 그 뒤 반영, 표 단위 복원) | v3 quant.db 의 **점수 두 표**(`score_history`·`_v2` — 컷오버 기간에 실제로 엑셀로 나간 점수, T-42). 되돌리는 이유가 점수 오류일 때만 함께 되돌린다(`--with-scores`) |
| quant-ledger 장 마감 체인의 v3 반영·발송 스위치(그림자로) | v3 quant.db 의 9표 밖 표(`market_*` · `research_reports` · `pipeline_runs` 등 — 컷오버 동안 v3 가 쓴 행 포함)와 `_compat_meta` 반영 기록(이력 — 복원 기록 1행이 덧붙는다) |

| 단계 | 무엇 | 사람 승인 |
|---|---|---|
| 4-0 | 시각·장부·현재 상태 기록 | — |
| 4-1 | quant-ledger 장 마감 체인을 그림자로(v3 반영·발송 끄기) | — |
| 4-2 | v3 quant.db 표 복원 | **[사람 승인 — v3 파일 변경]** |
| 4-3 | v3 크론·코드 복구(V3-E → D·B·A, QL-L 감시 크론) | **[사람 승인 — v3 파일 변경]** |
| 4-4 | v3 첫 실행(수집·스코어링) | 수동으로 돌릴 때만 **[사람 승인]** |
| 4-5 | 확인 체크 | — |

복원(4-2)을 v3 크론 복구(4-3)보다 먼저 한다. 그 시점에는 V3-B 로 `daily_all` 크론이 없고 4-1 로 제자리 반영이 꺼져 있다. 그래서 공유 락(`/tmp/kael_v3_daily_all.lock`)을 쥘 주체가 없는 상태에서 복원하게 되고, 되살린 `daily_all` 이 복원 전 DB 에 도는 일도 없다.

N-42 Q4 의 일괄 승인은 '컷오버 날 v3 수정'에 대한 것이다. 되돌리기의 v3 파일 변경은 따로 사람이 승인한다. 실행 직전에 한 줄로 보고한다.

## 1. 고정 백업 2벌 — 컷오버 날, V3-A~E 전

```bash
cd ~/quant-ledger
pgrep -af 'job_runner.py --chain daily_all|v3_post.sh|postclose_chain.sh' || echo "v3 체인·반영 없음"
scripts/v3_backup.sh --v3-db "$HOME/kael-system-v3/data/quant.db" \
  --dest "$HOME/quant-ledger/data/_cutover/v3_backup" --dest "$HOME/v3_cutover_backup" \
  --v3-root "$HOME/kael-system-v3" --uni-root "$HOME/unitelegram/unitelegram"
```

- **무엇을 남기나**(두 경로 각각, `stamp` = KST `YYYYMMDDTHHMMSS`, 전부 0444, `SHA256SUMS` 에 덧붙임).
  - `quant_<stamp>.db` — 온라인 백업이다. integrity_check ok 를 확인하고, rollback journal 로 바꿔 둔다.
  - `job_runner.py.bak.<stamp>`(V3-A) · `.kis_holidays.json.bak.<stamp>`(V3-D·QL-Q 연결, 숨김 파일) · `kael_db.py.bak.<stamp>`(V3-E) · `crontab.bak.<stamp>`(V3-A·B·D — `crontab -l`).
  - 대상 목록의 정본은 COMPAT_LAYER §8-1 이다.
- **언제**: v3 체인·반영이 돌지 않을 때(§4-0 의 피할 시각 밖)에 뜬다. 스크립트는 v3 락을 잡지 않는다. v3 크론은 `flock -n` 이라 락을 쥐면 그 시각 체인이 조용히 건너뛰어지기 때문이다.
- **성공 확인**
  - rc 0 이고 마지막 줄이 `════ 종료 rc=0 ════`, 그 앞 줄이 `compat backup 완료 — 2벌 × 5파일 …` 이다.
  - `(cd data/_cutover/v3_backup && sha256sum -c SHA256SUMS)` · `(cd "$HOME/v3_cutover_backup" && sha256sum -c SHA256SUMS)` 가 전부 `OK`(5줄)다.
  - `ls -la data/_cutover/v3_backup` 에서(숨김 파일 `.kis_holidays.json.bak.<stamp>` 이 보이게 `-a`) 다섯 파일이 `-r--r--r--` 이다.
  - 두 경로의 `quant_<stamp>.db` sha256 이 같다.
  - **stamp 를 적어 둔다**(아래 `S`).
- **멈춤**
  - 같은 이름이 이미 있으면 멈춘다(덮어쓰지 않는다).
  - 디스크 여유가 모자라면 멈춘다. 기준은 파일시스템마다 '백업 크기 × 2 × 그 파일시스템에 뜨는 벌 수'다. 두 경로가 같은 디스크면 × 4 다.
  - crontab 을 못 읽으면 멈춘다.
  - 사본 대상 파일이 없으면 rc 5 로 멈춘다. 이때는 경로를 서버에서 grep 으로 다시 찾는다(§8-1 줄 번호는 로컬 사본 기준이다).
  - 실패하면 이번에 만든 파일을 지우고, 이미 덧붙인 `SHA256SUMS` 도 덧붙이기 전으로 되돌린다.
- **소요**: 로컬 465MB 사본에서 4.7초(integrity_check 포함)다. 서버는 아직 재지 않았다.
- **한계**: 두 경로는 같은 서버 디스크일 수 있다. 우리 도구의 실수(덮어쓰기·삭제)는 막지만 디스크 고장은 못 막는다. 서버 밖 백업은 N-39 로 보류 중이다.

## 2. 언제 되돌리나

- **창**: 컷오버일 10-19 부터 5거래일(10-19~23)이다(정본 §4). 세는 법은 `daily.window_judge` 와 같다(세션 예외일도 거래일로 센다).
- **후보 신호**
  1. X-2 판정 실패. `PYTHONPATH=src .venv/bin/python -m daily.window_judge judge --start 20261019 --cutover 20261019` 의 되돌리기 창 날짜 중 fail 인 날이 있다. fail 이 되는 것은 장 마감 체인 단계 실패 · 세는 crit · 수동 개입 · 다음 날 대조 rc≠0 이다(T-39).
  2. v3 소비자 장애. 07:00 v3 브리핑 · v3 엑셀 텔레그램(T-20, daily_post) · uni 브리프가 틀리거나 나오지 않는다.
  3. 사람 판단.
- **결정은 사람(사용자)이 한다.** 판정 실패는 후보 신호다. 실패한 하루가 곧 되돌리기를 뜻하지는 않는다.
- **점수 표도 되돌릴지**도 이때 정한다. 기본은 되돌리지 않는다(T-42). 되돌리는 이유가 점수 오류(compat 점수가 틀렸다)일 때만 4-2 에서 `--with-scores` 를 준다.

## 3. 창 안과 창 밖, 점수 표

- **창 안**(복원 뒤 v3 첫 수집일이 10-23 이하)
  - v3 수집은 종목마다 최근 5행을 다시 받는다. 가격은 `backend/pipeline/collectors.py` 의 `items[:5]`, 수급은 `records[:5]` 다(로컬 사본 25dd56b).
  - 백업은 컷오버 날 아침에 떴으므로 직전 거래일(10-16)까지만 들어 있다.
  - 그래서 첫 수집일 X 가 10-19 부터 5거래일 안이면 10-19~X 의 `daily_prices`·`investor_detail_flows` 가 빈 날 없이 v3 정의로 다시 찬다.
- **창 밖**(첫 수집이 여섯째 거래일 이후)
  - 10-19 부터 (X 의 4거래일 전) 전날까지 두 표가 빈다.
  - 선택지(결정은 사람):
    - ① **권고** — 7표를 복원한 뒤 v3 자체 백필로 메운다. v3 저장소에서 `scripts/backfill.py --mode prices --days N` · `--mode investor_detail --days N` 을 돌린다. 키움 호출이므로 `--dry-run` 으로 예상 콜 수를 먼저 본다. `data/.backfill.lock` 을 쓴다. 이렇게 하면 v3 정의로 통일된다.
    - ② 가격·수급 두 표는 복원하지 않는다(`--tables` 에서 뺀다). compat 행이 남아 그 날들의 종가는 KRX 정규장 종가(T-33)다. v3 종가(애프터마켓 포함)와 섞인다.
- **점수 두 표 — 기본은 복원하지 않는다(T-42)**
  - 컷오버 기간 점수는 그날 실제로 엑셀로 나간 점수다. 그래서 기본 복원(7표)은 점수 두 표를 건드리지 않는다. 컷오버 기간 날짜의 점수 행은 compat 값(593·625 유니버스 — T-17)으로 남는다.
  - **예외**: 되돌리는 이유가 점수 오류일 때만 `--with-scores` 로 함께 되돌린다. 그러면 컷오버 기간 날짜의 점수 행은 사라진다(백업에 없다. v3 스코어링은 그날만 계산한다).
  - **부수 효과 — compat 행과 v3 행이 섞인다.** v3 점수 쓰기는 `INSERT OR REPLACE` 다(v3 `backend/db/repositories/score_repo.py:37` · `backend/scoring/v2_repo.py:21`, 로컬 사본 25dd56b). 같은 날짜를 compat 과 v3 가 둘 다 쓰면 겹치는 종목은 v3 행으로 바뀌고, v3 유니버스 밖 종목의 compat 행은 그대로 남는다(예: 그날 ⑥ 이 점수를 쓴 뒤 4-4 수동 실행이 같은 날짜를 다시 쓸 때). 날짜로 보면 컷오버 기간은 compat 행, 그 뒤는 v3 행이다.
  - **확인 필요**: uni `get_signal_insights`(`sources/kael_db.py` — 4-3 에서 V3-E 날짜 조건을 걷어 낸 뒤) 가 종목별 최신 행을 고를 때의 영향. 복원 뒤 v3 유니버스에서 빠진 종목은 컷오버 기간의 compat 행이 '최신'으로 잡힐 수 있다. 서버 파일에서 조회 식을 보고 정한다.

## 4. 순서

공통 변수:

```bash
cd ~/quant-ledger
B="$HOME/quant-ledger/data/_cutover/v3_backup"     # 첫 벌(sha 불일치면 둘째 벌 "$HOME/v3_cutover_backup")
S=<§1 의 stamp>
V3="$HOME/kael-system-v3/data/quant.db"
```

### 4-0. 준비

- **시각**: 복원은 v3 본 파일 쓰기 락을 수 초~수십 초 쥔다(로컬 465MB 사본에서 9표 230만 행에 9.1초 — 7표는 그보다 짧다. 서버는 미실측). v3 연결의 busy_timeout 은 5초다(`backend/db/connection.py`). 그래서 아래 시각(KST)을 피한다.
  - v3 브리핑 07:00 · 12:15 · 15:35
  - uni(quant.db rsync) 06:35 · 07:10 · 12:15 · 15:40
  - 장 마감 체인 15:41~16:30
  - v3 daily_insight 20:05 · 리서치 20:30 · 브로커 리서치 21:00
  - quant-ledger 키움 저녁 수집·refill 21:05~
  - 권장 창은 평일 09:30~11:30 · 13:00~15:00 또는 22:00 뒤다. 그날 v3 엑셀이 이미 나간 날(4-4 분기)은 22:00 뒤가 편하다 — 그날 20:05 daily_insight 가 끝났고 다음 daily_all 은 내일이다.
  - 4-1~4-3 은 한 자리에서 이어서 한다.
- **맥 전원**: 맥을 전원에 연결한다. 연결이 끊기면 서버 락이 남는다(정본 §5 10-10 기록).
- **수동 개입 장부**: `PYTHONPATH=src .venv/bin/python -m daily.window_judge record --date <오늘 YYYYMMDD> --what "v3 되돌리기(QL-I) — <사유>" --by <누가>`
- **지금 상태 기록**(되돌리기의 되돌리기용): `crontab -l > "$HOME/v3_cutover_backup/crontab.before_rollback.$(date +%Y%m%dT%H%M%S)"`
- **백업 검증**: `(cd "$B" && sha256sum -c SHA256SUMS)` 가 전부 `OK` 여야 한다. 아니면 둘째 벌을 `B` 로 둔다.

### 4-1. 장 마감 체인을 그림자로 — v3 반영·발송 끄기(quant-ledger, PR-8 스위치, T-42)

`config/postclose_chain.env`(PR-8)를 그림자 상태로 둔다: `POSTCLOSE_ENABLED=1` · `POSTCLOSE_SEND=0` · `POSTCLOSE_V3=shadow` · `POSTCLOSE_V3_POST_CMD=''`.

- 컷오버 전 그림자(10-14~16)와 같은 상태다. v3 본 파일과 텔레그램에 닿는 것이 없다(세 모드의 v3_post 가 전부 `--shadow` 로 돌고 v3 락도 잡지 않는다). 크론·16:30 워치독은 그대로 두고, 다시 컷오버할 대조 기록이 계속 쌓인다.
- 체인 자체(수집기·판)가 원인이라 아예 세워야 하면 사람이 정한다. 그때는 `POSTCLOSE_ENABLED=0` 이고, 꺼져 있으면 16:30 워치독이 crit 을 내므로 4-3 의 crontab 편집 때 15:41 `postclose_chain.sh close`·16:30 `watchdog.sh postclose_board` 줄도 주석 처리한다.
- **바꾸는 방법**: 저장소에서 고쳐 `scripts/deploy.sh` 로 배포한다. 배포 금지 창은 15:40~16:30 · 21:00~21:30 이다. 급해서 서버 파일을 직접 고쳤으면 같은 값을 저장소에도 커밋한다. deploy 가 `config/` 를 `--delete` 로 맞추므로, 커밋하지 않으면 다음 배포가 되돌린다.
- **PR-9 스위치**: 아침판 '짓기만' · 대체 발송 · 10:30 워치독(B-57)을 컷오버 전 값으로 되돌린다. 이름·명령은 PR-9 머지 때 이 줄에 적는다(PR-9 체크리스트 ⑥).
- **QL-Q 연결**: 06:00 체인이 v3 `data/.kis_holidays.json` 을 쓰고 있으면 끈다. 자리는 연결 PR 이 정한다.
- **성공 확인**
  - `bash scripts/postclose_chain.sh close --dry-run | head -1` 이 `켜짐 · 발송 off · v3 shadow` 를 보인다.
  - `pgrep -af 'postclose_chain.sh|v3_post.sh'` 가 비어 있다. 돌고 있으면 끝날 때까지 기다린 뒤 4-2 로 간다. `v3_post.sh` 는 v3 락을 잡은 **뒤에** 스테이징을 뜬다. 그래서 복원(4-2)이 락을 쥔 동안 기다리던 제자리 반영은 복원 뒤에 깨어 복원된 파일로 계산하고, '복원 뒤 첫 반영' 게이트(§5, T-46)를 지나 **반영된다** — 되돌린 표에 compat 값이 다시 들어간다. 게이트가 막는 것은 복원 앞 파일로 계산한 반영뿐이다.

### 4-2. v3 quant.db 표 복원 **[사람 승인 — v3 파일 변경]**

```bash
scripts/v3_restore.sh --backup "$B/quant_$S.db" --v3-db "$V3" --dry-run
scripts/v3_restore.sh --backup "$B/quant_$S.db" --v3-db "$V3"                  # 기본 7표
# 되돌리는 이유가 점수 오류일 때만(T-42): scripts/v3_restore.sh --backup "$B/quant_$S.db" --v3-db "$V3" --with-scores
```

- **① 검증(락을 잡기 전)**: 백업 sha256 을 SHA256SUMS 와 대조하고, 표·열 이름을 보고, 표별 `본 파일 N행 → 백업 M행 (차이)` 을 낸다. 본 파일은 읽기만 한다. `--dry-run` 이면 여기서 끝난다(락 없음). 차이는 컷오버 동안 늘어난 날짜 몫쯤이어야 한다. 백업 0행처럼 큰 이상이 보이면 멈춘다.
- **② 복원(v3 락 안)**: sha 를 다시 대조한 뒤 고른 표를 한 트랜잭션에 되돌린다. 순서는 ATTACH · BEGIN IMMEDIATE · 표별 전체 DELETE · 백업 INSERT · `_compat_meta` 복원 기록 1행 · COMMIT 이다. 9표 밖 표와 반영 기록은 건드리지 않는다.
- **락**: v3 체인·`v3_post.sh` 와 같은 `/tmp/kael_v3_daily_all.lock` 이고 대기형이다. 이 순서(4-1 뒤, 4-3 전)에서는 쥘 주체가 없어야 한다. `v3 락 대기 시작` 줄이 나오면 **무엇이 쥐었는지 먼저 본다** — 다른 터미널에서 `pgrep -af 'kael_v3_daily_all.lock|v3_post.sh|job_runner.py'`. 4-1 이 덜 꺼졌거나(v3_post 제자리) 손으로 돌린 v3 체인이면 그것이 끝나기를 기다리거나 원인을 정리한다.
- **rc**
  - 0 완료.
  - 2 실패. COMMIT 전이라 본 파일은 그대로다. sha 불일치·SHA256SUMS 줄 없음·9표 밖 표·점수 표인데 `--with-scores` 없음·열 불일치(백업 뒤 v3 스키마 변경)가 여기에 든다. 검증 실패면 락도 잡지 않았다. 로그 `logs/v3_restore/*.log` 의 원인을 본다. sha 불일치면 둘째 벌로 다시 한다.
  - 3 락 실패.
  - 5 인자 오류.
- **표 고르기**: `--tables a,b`(9표 안에서만, 점수 표는 `--with-scores` 와 함께)로 고른다. 창 밖 선택지 ②(§3)에 쓴다.
- **성공 확인**(복원 직후, v3 첫 실행 **전**에 한다). 셋째 인자는 견줄 표다 — 생략하면 기본 7표, `--with-scores` 로 복원했으면 `nine`, 리허설처럼 모든 표가 같아야 하면 `all`.
  ```bash
  .venv/bin/python - "$V3" "$B/quant_$S.db" <<'EOF'
  import sqlite3, sys
  from pathlib import Path
  NINE = ["daily_prices", "stocks", "investor_detail_flows", "consensus_revision_daily",
          "consensus_revision_compare", "consensus_annual", "financial_summary",
          "score_history", "score_history_v2"]
  mode = sys.argv[3] if len(sys.argv) > 3 else "seven"
  cur, ref = (Path(p).resolve().as_uri() for p in sys.argv[1:3])
  c = sqlite3.connect(f"{cur}?mode=ro", uri=True)
  c.execute("ATTACH ? AS r", (f"{ref}?mode=ro",))
  names = [t for (t,) in c.execute("SELECT name FROM r.sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'sqlite_%' AND name <> '_compat_meta' ORDER BY name")]
  want = set(names) if mode == "all" else set(NINE if mode == "nine" else NINE[:7])
  bad = 0
  for t in names:
      a = c.execute(f'SELECT count(*) FROM (SELECT * FROM main."{t}" EXCEPT SELECT * FROM r."{t}")').fetchone()[0]
      b = c.execute(f'SELECT count(*) FROM (SELECT * FROM r."{t}" EXCEPT SELECT * FROM main."{t}")').fetchone()[0]
      print(f"{'견줌' if t in want else '참고'} {t}: 지금에만 {a} · 기준에만 {b}")
      bad += (a + b) if t in want else 0
  print(c.execute("SELECT basis, status, date, tables FROM main._compat_meta ORDER BY exported_at DESC LIMIT 1").fetchone())
  print(c.execute("PRAGMA main.quick_check").fetchone())
  print(f"{mode} PASS" if bad == 0 else f"{mode} FAIL")
  EOF
  ```
  기대: '견줌' 줄이 전부 `0 · 0` 이고 `seven PASS` 다. '참고' 줄(점수 두 표·9표 밖)은 컷오버 동안 쓴 만큼 다를 수 있다. 마지막 기록은 `('restore', 'ok', <오늘>, <7표 행 수 json>)` 이고 `('ok',)` 이다.

### 4-3. v3 크론·코드 복구 — V3-E → D·B·A, QL-L 감시 크론 **[사람 승인 — v3 파일 변경]**

공통 규칙이 있다. 사본으로 덮기 전에 `diff` 로 V3-x 변경만 있는지 본다. 다른 변경이 섞였으면 덮지 말고 그 줄만 손으로 되돌린다. 덮을 때는 `cp` 를 쓴다(기존 파일의 권한은 그대로 남는다).

**그날 v3 엑셀이 이미 나갔으면**(4-4 분기 — ⑥ daily_post 가 그날 엑셀을 보냈다) 아래 2(crontab)는 그날 20:05 daily_insight 가 끝난 뒤에 한다. 먼저 되살리면 그날 20:05 `daily_all` 이 엑셀을 한 번 더 보낸다.

1. **V3-E — uni 점수 조회**
   ```bash
   diff "$B/kael_db.py.bak.$S" "$HOME/unitelegram/sources/kael_db.py"   # get_signal_insights 의 score_date 조건만이어야 한다
   cp "$B/kael_db.py.bak.$S" "$HOME/unitelegram/sources/kael_db.py"
   ```
   확인: `sha256sum "$HOME/unitelegram/sources/kael_db.py"` 이 `grep "kael_db.py.bak.$S" "$B/SHA256SUMS"` 의 값과 같다.
2. **V3-D·B·A·QL-L — crontab**
   ```bash
   diff <(crontab -l) "$B/crontab.bak.$S"
   ```
   - 기대하는 차이는 넷이다. (A) `--chain daily_insight` 줄이 있다 → 없다. (B) `--chain daily_all` 줄이 없다 → 있다. (D) `refresh_year_holidays`·`monthly_holiday_review` 두 줄이 없다(주석) → 있다. (QL-L) 23:30 `daily.cutover_watch` 감시 줄이 있다 → 없다(컷오버 날 V3-A~E 뒤에 넣은 줄이라 백업에 없다).
   - 그 밖의 줄도 다르면(컷오버 뒤 quant-ledger 크론 변경 등) 통째로 덮지 않는다. `crontab -e` 로 위 줄들만 고친다 — 감시 줄은 지우지 말고 주석 처리한다.
   ```bash
   crontab "$B/crontab.bak.$S"
   ```
   - 4-1 에서 완전 정지를 골랐으면 이어서 `crontab -e` 로 15:41·16:30 장 마감 줄을 주석 처리한다.
   - 확인: `crontab -l | grep -c -- '--chain daily_all'` → 1 · `crontab -l | grep -c daily_insight` → 0 · `crontab -l | grep -cE 'refresh_year_holidays|monthly_holiday_review'` → 2 · `crontab -l | grep -v '^[[:space:]]*#' | grep -c 'daily.cutover_watch'` → 0(감시 줄이 없거나 주석이다 — 남아 있으면 되살린 daily_all·v3 스코어링을 매일 위반 crit 으로 본다).
3. **V3-D — 휴장 파일**: QL-Q 연결이 v3 휴장 파일을 썼을 때만 한다(4-1 에서 쓰기를 끈 뒤). 연결 전이면 건너뛴다. 되살린 daily_all 의 `calendar_refresh` 가 20:05 에 이 파일을 다시 쓴다.
   ```bash
   diff "$B/.kis_holidays.json.bak.$S" "$HOME/kael-system-v3/data/.kis_holidays.json"
   cp "$B/.kis_holidays.json.bak.$S" "$HOME/kael-system-v3/data/.kis_holidays.json"
   ```
4. **V3-A — `job_runner.py`**: crontab 에서 daily_insight 줄을 뺀 **뒤에** 한다. 줄이 남은 채로 체인을 지우면 그 줄이 없는 체인 이름으로 실패한다.
   ```bash
   diff "$B/job_runner.py.bak.$S" "$HOME/kael-system-v3/scripts/job_runner.py"   # CHAINS 의 daily_post·daily_insight 두 체인만이어야 한다
   cp "$B/job_runner.py.bak.$S" "$HOME/kael-system-v3/scripts/job_runner.py"
   ```
   확인: sha256 이 SHA256SUMS 줄과 같고, `grep -cE '"daily_post"|"daily_insight"' "$HOME/kael-system-v3/scripts/job_runner.py"` → 0.

### 4-4. v3 첫 실행

분기 기준은 **그날 v3 엑셀(⑥ daily_post 의 `export_scores`)이 이미 나갔는가**다. 같은 날 v3 엑셀이 두 번 나가지 않게 하려는 것이다. 텔레그램 채팅방과 아래 기록으로 본다(`started_at` 은 UTC — KST 그날 = UTC 전날 15:00 ~ 그날 15:00).

```bash
.venv/bin/python - "$V3" <<'EOF'
import sqlite3, sys
from pathlib import Path
c = sqlite3.connect(f"{Path(sys.argv[1]).resolve().as_uri()}?mode=ro", uri=True)
print(c.execute("SELECT job_name, status, started_at FROM pipeline_runs WHERE job_name IN "
                "('chain:daily_post', 'export_scores') ORDER BY run_id DESC LIMIT 4").fetchall())
EOF
```

- **아직 안 나갔으면**(되돌리기가 그날 ⑥ 전이거나, ⑥ 이 실패·그림자라 daily_post 가 돌지 않은 날): 첫 실행은 4-3 에서 되살린 20:05 `daily_all` 크론이다. 수집(최근 5행) → 수정주가 → 스코어링 2종 → 엑셀 텔레그램 → insight·위키 순으로 돌고 엑셀은 한 번 나간다. 장중에 수동으로 돌리지 않는다. v3 수집은 그날 행의 종가를 현재가로 채운다(`collectors.py` 의 `cur_prc` 대체). 20:05 가 이미 지났으면 21:05 quant-ledger 키움 저녁 수집이 끝난 뒤(v3 와 앱키를 같이 쓴다 — README 크론 표 결정 11) `--chain daily_all` 을 손으로 한 번 돌린다 **[사람 승인]**(v3 crontab 줄에서 cron 시각만 뺀 형태).
- **이미 나갔으면 [사람 승인]**: 엑셀 잡 `export_scores` 를 빼고 잡만 돌린다. 시각은 21:05 키움 저녁 수집이 끝난 뒤, 그날 20:05 daily_insight 가 끝난 뒤다. 휴장일이면 하지 않는다. 재시도·시간 한도·치명 여부는 고정 숫자를 쓰지 않고 **실행 직전 서버 `scripts/job_runner.py` 의 `CHAINS["daily_all"]` 에서 읽는다**(서버는 10-02 패치로 `adj_prices` 시간 한도가 로컬 사본과 다르다 — 3600). 치명(True) 단계(`holiday_gate`·`daily_pipeline`)만 실패하면 멈추고, 비치명(False) 단계(`adj_prices`·`scoring`·`scoring_v2`) 실패는 체인 정의대로 다음 단계로 간다.
  ```bash
  cd "$HOME/kael-system-v3"
  grep -n -A12 '"daily_all": \[' scripts/job_runner.py          # 눈으로 확인
  STEPS=$(.venv/bin/python - <<'EOF'
  import ast
  tree = ast.parse(open("scripts/job_runner.py", encoding="utf-8").read())
  for n in ast.walk(tree):
      tgt = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if isinstance(n, ast.Assign) else None)
      if getattr(tgt, "id", None) == "CHAINS":
          for job, retry, timeout, critical in ast.literal_eval(n.value)["daily_all"]:
              if job in ("holiday_gate", "daily_pipeline", "adj_prices", "scoring", "scoring_v2"):
                  print(job, retry, timeout, int(critical))
  EOF
  )
  echo "$STEPS"                                                  # 다섯 줄: 잡 재시도 시간한도 치명(1/0)
  flock -n /tmp/kael_v3_daily_all.lock bash -c 'source "$HOME/.local/bin/env" && export $(grep -v "^#" .env | xargs) && while read -r job retry timeout crit; do PYTHONPATH=. .venv/bin/python scripts/job_runner.py --job "$job" --retry "$retry" --timeout "$timeout" && continue; [ "$crit" = 1 ] && { echo "치명 단계 $job 실패 — 멈춘다"; exit 1; }; echo "비치명 단계 $job 실패 — 다음 단계로(체인 정의와 같다)"; done <<< "$1"' _ "$STEPS" \
    || echo "rc≠0 — v3 락이 잡혀 있었거나(flock -n) 치명 단계가 실패했다. pipeline_runs·v3 로그를 보고 다시"
  ```
- **성공 확인**
  ```bash
  .venv/bin/python - "$V3" <<'EOF'
  import sqlite3, sys
  from pathlib import Path
  c = sqlite3.connect(f"{Path(sys.argv[1]).resolve().as_uri()}?mode=ro", uri=True)
  print("daily_prices 최신", c.execute("SELECT max(trade_date) FROM daily_prices").fetchone()[0])
  print(c.execute("SELECT trade_date, count(*) FROM daily_prices WHERE trade_date >= '2026-10-19' GROUP BY 1").fetchall())
  for t in ("score_history", "score_history_v2"):
      print(t, c.execute(f"SELECT score_date, count(*) FROM {t} WHERE score_date >= '2026-10-19' GROUP BY 1").fetchall())
  print(c.execute("SELECT job_name, status, finished_at FROM pipeline_runs ORDER BY run_id DESC LIMIT 6").fetchall())
  EOF
  ```
  - `daily_prices` 최신일이 그날이다. 창 안이면 10-19 뒤 거래일마다 행이 다시 있다.
  - 점수: v3 가 쓴 날짜(첫 실행일)는 v3 유니버스 — `score_history` 1,300 안팎, `score_history_v2` 2,500 안팎이다. 컷오버 기간 날짜는 compat 값 593·625 그대로다(T-42 — 점수 표를 복원하지 않았다. `--with-scores` 였으면 그 날짜 행이 없다). 같은 날짜를 둘 다 썼으면 §3 의 섞임이 생긴다.
  - 잡이 `success` 다(비치명 단계 실패는 로그에 남는다 — 원인을 본다).

### 4-5. 확인 체크(다음 날 아침까지)

- [ ] 4-1~4-4 의 확인을 전부 통과했다.
- [ ] 다음 장 마감(15:41) 체인이 그림자다. 로그 `logs/postclose/<T>_close.log` 머리 줄이 `발송 off · v3 shadow` 다.
- [ ] v3 본 파일에 복원 기록 뒤 새 반영 기록이 0건이다. `SELECT count(*) FROM _compat_meta WHERE exported_at > (SELECT max(exported_at) FROM _compat_meta WHERE basis = 'restore')` → 0.
- [ ] 20:05 v3 `daily_all` 이 성공했다(`pipeline_runs` 의 `chain:daily_all` success). v3 엑셀 텔레그램이 그날 한 번만 도착했다.
- [ ] 다음 날 07:00 v3 브리핑과 07:10 uni 브리프가 그날 장(최신 `trade_date`)으로 나왔다.
- [ ] `logs/notify.log` 에 복원 뒤 `v3_post … 반영 완료`(info)도 `v3_post … 실패`(crit)도 없다. 있으면 4-1 이 덜 꺼진 것이다. 복원 뒤에 계산된 제자리 반영은 게이트(§5)를 지나 반영되므로(T-46) 대개 '반영 완료' 로 남는다 — 위 '복원 기록 뒤 새 반영 기록 0건' 이 같은 것을 본다.
- [ ] `logs/notify.log` 에 그날 23:30 뒤 `컷오버 감시 …` crit 이 없다(감시 크론이 주석이다).
- [ ] 수동 개입 장부에 기록했다(4-0).

## 5. 복원 뒤 반영 규칙 — 다시 컷오버할 때(T-42)

`_compat_meta` 의 복원 기록(basis `restore`)은 장벽이다. 규칙 정본은 `src/compat/v3_post.py` 머리 주석이다.

- 복원 기록 **앞**의 반영 기록은 순서 가드(T-35)와 아침 7표 판정(T-34)에서 빠진다. 그 기록들은 복원 뒤 본 파일을 설명하지 않는다 — 가격 등 7표는 백업 시점으로 돌아갔고, 남겨 둔 점수 두 표는 복원 뒤 v3 스코어링이 같은 키를 덮는다. 기록은 지우지 않는다(이력).
- 복원 기록 **뒤** 첫 제자리 반영은 **복원 뒤에 계산된** compat 기록만 받는다(T-46). 아니면 게이트 실패(rc 2, 본 파일 무변경)다.
  - 조건 ①: compat 기록 시각(`exported_at`)이 복원 기록 시각보다 뒤다. 같은 시각·시각 없음·읽을 수 없는 시각은 거부한다.
  - 조건 ②: 스테이징 `_compat_meta` 에 그 복원 기록이 있다. 곧 스테이징을 복원 뒤 v3 파일에서 떴다.
  - 창은 매일과 같은 증분(마지막 거래일 + 앞 10거래일)이어도 된다. v3 이력은 이미 KRX 기준가 사슬과 같다(QL-E 0행 차이). 창 안 종가는 compat 종가(KRX 정규장 종가 — T-33), 창 밖은 v3 종가(애프터마켓 포함)로 남는다 — 첫 컷오버(V3-C)와 같다.
  - 막는 것은 복원 앞 v3 파일로 계산한 반영이다(단계를 손으로 나눠 돌렸거나 두 셸의 락 경로가 달랐던 경우). `v3_post.sh` 는 락을 잡은 뒤에 스테이징을 뜨므로, 락을 기다리다 복원 뒤에 깬 반영이나 꺼지지 않은 체인은 막지 **않는다** — 4-1 이 끄고 4-5 가 확인한다.
  - 그림자(`--shadow`)는 본 파일에 쓰지 않으므로 이 조건을 보지 않는다.
  - **새 정지 조건이라 사용자 확인 대기**(구현은 했다).
- 제자리 `--full` 은 손 복구용이다. 창 시작이 대상 v3 표(`daily_prices`·`investor_detail_flows`)의 이력 시작보다 앞이면 compat 이 쓰기 전에 멈춘다(rc 2, T-46). 메시지의 `--window-days N` 을 `--full` 과 함께 준다.
- QL-L 감시(`daily.cutover_watch`)는 복원 기록을 반영 기록으로 세지 않는다.
- 다시 컷오버하는 순서: 새 백업 2벌(§1 — 새 stamp, 옛 백업은 남긴다) → V3-A~E → `v3_post.sh`(V3-C — 증분 창, T-46) → QL-L 감시 크론 주석 풀기 → 4-1 스위치를 PR-9 값으로.

## 6. 리허설 — 서버 사본(컨트롤러)

원본(QL-H 날짜별 사본 `data/_cutover/v3_daily/quant_<D>.db`)은 읽기만 한다. 실행 홈을 따로 둔다(`QL_HOME`). 그래야 알림(`logs/notify.log`)·로그가 운영 홈에 섞이지 않는다. 사본 리허설에서 나는 crit 이 연속 창 판정(X-2)에 들어가면 안 된다. 락도 임시 경로(`QL_V3_LOCK_FILE`)를 쓴다.

```bash
cd ~/quant-ledger
D=20261008                                   # QL-H 사본이 있고 그날 인계 이력·판이 GC 되지 않은 거래일
R="$HOME/quant-ledger/data/_cutover/rehearsal_qli"
mkdir -p "$R/home/data" && for x in scripts src .venv; do ln -s "$HOME/quant-ledger/$x" "$R/home/$x"; done
ln -s "$HOME/quant-ledger/data/calendar" "$R/home/data/calendar"   # 판정 달력(읽기만) — 증분 창·제자리 창 세션을 센다
cp "data/_cutover/v3_daily/quant_$D.db" "$R/quant.db" && chmod 644 "$R/quant.db"
export QL_HOME="$R/home" QL_V3_LOCK_FILE="$R/v3.lock" QL_EQUITY_ROOT="$HOME/quant-ledger/data/equity" \
       QL_STAGE_ROOT="$HOME/quant-ledger/data/stage" QL_MODEL_ROOT="$HOME/quant-ledger/data/model"
H="$HOME/quant-ledger/data/deliver/history/${D}_morning.json"
# ① 백업 2벌(사본 대상) — rc 0
bash "$R/home/scripts/v3_backup.sh" --v3-db "$R/quant.db" --dest "$R/bak1" --dest "$HOME/qli_rehearsal_bak2" \
  --v3-root "$HOME/kael-system-v3" --uni-root "$HOME/unitelegram/unitelegram"
S=$(ls "$R/bak1" | sed -n 's/^quant_\(.*\)\.db$/\1/p')
# ② 첫 반영(V3-C — 매일과 같은 증분 창, T-46) — rc 0, 걸린 시간 기록
time bash "$R/home/scripts/v3_post.sh" --date "$D" --basis morning --v3-db "$R/quant.db" --staging "$R/staging.db" --builds-from "$H"
# ③ 기본 복원(7표) — 계획 → 복원, rc 0, 걸린 시간 기록
bash "$R/home/scripts/v3_restore.sh" --backup "$R/bak1/quant_$S.db" --v3-db "$R/quant.db" --dry-run
time bash "$R/home/scripts/v3_restore.sh" --backup "$R/bak1/quant_$S.db" --v3-db "$R/quant.db"
# ④ 대조 — 4-2 확인 스크립트를 인자 "$R/quant.db" "data/_cutover/v3_daily/quant_$D.db" 로(셋째 인자 생략 = 7표).
#    기대: seven PASS. '참고' 줄 중 score_history·_v2 만 다르다(② 가 쓴 score_date = D 행 — compat 593·625 대 원본 v3).
#    나머지 9표 밖 표는 0 · 0
# ⑤ 복원 앞에 계산한 반영 만들기 — 단계를 손으로 나눠 스테이징·export 를 ⑥ 복원 앞에 끝낸다(rc 0, 본 파일 무변경)
P() { PYTHONPATH=src .venv/bin/python -m compat "$@"; }
P stage --v3-db "$R/quant.db" --out "$R/staging_pre.db"
P export --date "$D" --basis morning --equity-root "$QL_EQUITY_ROOT" --stage-root "$QL_STAGE_ROOT" \
  --model-root "$QL_MODEL_ROOT" --target "$R/staging_pre.db" --in-place --builds-from "$H" \
  --tables "$(P v3-tables --v3-db "$R/quant.db" --date "$D" --basis morning)"
# ⑥ 점수 오류 경로 — --with-scores 복원 → 4-2 확인 스크립트를 셋째 인자 all 로. 기대: all PASS(모든 표 0 · 0),
#    _compat_meta 는 [morning ok, restore ok(7표), restore ok(9표)]
bash "$R/home/scripts/v3_restore.sh" --backup "$R/bak1/quant_$S.db" --v3-db "$R/quant.db" --with-scores
# ⑦ 복원 뒤 첫 반영 게이트(T-46) — ⑤ 의 반영은 rc 2('복원 뒤 첫 반영' 두 줄 — 기록 시각·스테이징), 본 파일 sha256 무변경.
#    이어서 복원 뒤에 계산한 증분 v3_post.sh 는 rc 0(_compat_meta 끝 = restore ok(9표), morning ok)
sha256sum "$R/quant.db"
P apply --staging "$R/staging_pre.db" --v3-db "$R/quant.db" --date "$D" --basis morning; echo "rc=$?"
sha256sum "$R/quant.db"
bash "$R/home/scripts/v3_post.sh" --date "$D" --basis morning --v3-db "$R/quant.db" --staging "$R/staging.db" --builds-from "$H"; echo "rc=$?"
# ⑧ 제자리 --full 하한(T-46) — 730일 창이 v3 이력 시작(가격 2025-01-02 · 수급 2025-01-23)보다 앞이라 ② compat 이 rc 2,
#    본 파일 sha256 무변경, 로그에 표별 '이력 시작'과 맞출 --window-days
sha256sum "$R/quant.db"
bash "$R/home/scripts/v3_post.sh" --date "$D" --basis morning --v3-db "$R/quant.db" --full --staging "$R/staging.db" --builds-from "$H"; echo "rc=$?"
sha256sum "$R/quant.db"
grep '이력 시작' "$R/home/logs/v3_post/${D}_morning.log"
# 정리
unset QL_HOME QL_V3_LOCK_FILE QL_EQUITY_ROOT QL_STAGE_ROOT QL_MODEL_ROOT
rm -rf "$R" "$HOME/qli_rehearsal_bak2"
```

- 그날 판이 GC 됐으면 ②에서 compat 이 rc 2 로 멈춘다(QL-F2 기록). 이때는 D 를 판이 남은 가장 최근 QL-H 사본 날짜로 바꾼다.
