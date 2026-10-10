# 휴장 달력 직접 갱신 — KIS chk-holiday 하루 1콜 + K1-9 게이트(①②③④⑥⑦)

> 결정은 [`DECISIONS.md`](../DECISIONS.md) Q-2 = N-31 ②(10-08 "권장 사항으로 처리해봐.") 와 범위 N-40 ④(10-09 "주말에 할수있는 일들은 모두 해놔.")에 있다. 이 문서는 작업과 진행만 적는다.
> 정본 근거: [`2026-10-05-roadmap.md`](2026-10-05-roadmap.md) §4 휴장 캘린더 · §8 K1-9. 세부 후보: [`2026-10-05-gate-register.md`](2026-10-05-gate-register.md) C3(RG-C3-01~07, 채택 보류 — 이 문서는 K1-9 범위만 구현한다).
> 상태: **10-10 플랜·구현·테스트(브랜치 `wip/cal-direct`, 커밋 전) → 명세 리뷰 '준수'·품질 리뷰 '조건부 승인' → 지적 반영 완료(10-10).** 서버 실호출 검증 1회 대기. 배포는 main 병합 배포 뒤(N-40 ④).
> 확정(컨트롤러 전달 10-10): Q-CAL-1 = A(11-21~ 하루 2콜), Q-CAL-2 = A(③ 불일치는 crit 기록만), Q-CAL-3 = 옛 경로 파일 유지, 원장 표 `kis.db.kis_holiday` 채택 — 결정 장부 기록은 컨트롤러.

## 진행률

| ID | 무엇 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|---|
| CAL-1 | ① 직접 갱신(창 1콜) + 응답 검증 + 원장 적재 + 판정 연도 파일 덧씌움 | 완료 | 완료 | 지적 반영 | — | — | 서버 `--check` 1회 |
| CAL-2 | ② 달력상 휴장일에 KRX 시세 ok 행 → HALT(`krx.holiday_traded`, 시세 엔드포인트만) | 완료 | 완료 | 지적 반영 | — | — | 첫 08:10 |
| CAL-3 | ③ 지난 거래일 집합 = equity `trading_calendar` | 완료 | 완료 | 대기 | — | — | 서버 `--check` 1회 |
| CAL-4 | ④ 올해 판 부재·이듬해 판 12-15 기한 + 이어 받기(11-21~, 올해 판 없으면 올해) | 완료 | 완료 | 지적 반영 | — | — | 11-21 첫 이어 받기 |
| CAL-5 | ⑤ 날짜 계산 호출처 목록(리팩터 없음) | 완료 | 목록만(§7) | 대기 | — | — | — |
| CAL-6 | ⑥ 휴장 목록 감소 경고 | 완료 | 완료 | 대기 | — | — | — |
| CAL-7 | ⑦ 달력을 못 읽으면 중단(R7 폴백 폐지) · 옛 경로 파일 무시 | 완료 | 완료 | 대기 | — | — | 첫 06:00 |
| CAL-8 | v3 사본 병행: 판정 디렉터리에서 `v3/` 로 분리, 매일 일치·불일치 기록 | 완료 | 완료 | 대기 | — | — | 일치 30일 뒤 끄기 결정(나중) |

## 1. 배경
- quant-ledger 의 휴장 판정은 원본 v3 의 KIS 휴장 캐시 사본에 기대고 있다. v3 를 끄면 달력 갱신도 멈추고, 월중에 지정되는 임시공휴일은 v3 의 다음 월간 점검까지 모른다(로드맵 §4 위험 1·2).
- 달력을 못 읽으면 주말만 빼고 '영업일로 가정'하고 진행한다(R7, 09-09). 로드맵 C3 '휴장일 오판 0' 과 충돌해 Q-2 로 재결정됐다 — 달력을 못 읽으면 중단(N-31 ②).

## 2. 지금 동작(10-10, `wip/cal-direct` 9a1ae910 = 운영 코드와 같은 달력 경로)
- `scripts/sync_calendar.sh`(06:00 `daily_ledger.sh:49`·18:05 `daily_evening.sh:98`)가 v3 `data/.kis_holidays.json` 을 복사·검증(≥100건·전건 해당 연도·주말 ≥90)해 판정 디렉터리 `data/calendar/kis_holidays_<year>.json` 과 옛 경로 `data/calendar/kis_holidays.json` 을 덮는다.
- `src/daily/calendar.py` `load()` 가 디렉터리의 `kis_holidays*.json` 을 **전부** 합쳐(합집합) 읽는다. 실패하면 `weekend_only` 폴백.
- 이미 있는 게이트: KRX 가 '휴장' 응답을 준 날이 달력상 거래일이면 HALT(`ledger_health.py` `krx.holiday_misfire`).

### 결함 / 위험(4요소)

**CAL-D1: 달력을 못 읽으면 휴장일을 거래일로 보고 체인이 돈다**
- **상황**: 판정 디렉터리에 연도 파일이 없거나(배포 실수·디스크) JSON 이 깨졌다. 그날이 평일 휴장(예: 2026-10-09 한글날).
- **인풋**: 06:00 `daily_ledger.sh` 가 `--date` 없이 `c.load().prev_trading_day(오늘)` 로 D 를 구한다. 18:05 `daily_evening.sh` 가 `c.load().is_trading_day(오늘)` 를 묻는다.
- **에러 위치**: `src/daily/calendar.py:97-99` — 예외를 잡아 `Calendar(frozenset(), "weekend_only")` 를 돌려준다. 호출부는 경고만 찍는다(`kis_daily.py:539-540`·`kw_daily.py:948-949`·`backfill_krx.py:87-88`·`dart_daily.py:1054-1055`), 셸은 경고조차 없다.
- **위험성**: 휴장일 오판(silent corrupt). 10-09 같은 평일 휴장을 거래일로 보고 저녁 체인이 키움 수급을 그날 날짜로 받고, 다음 날 D 가 휴장일이 돼 KRX 빈 응답이 pending 으로 쌓인다. C3 '오판 0' 위반.

**CAL-D2: 임시공휴일이 월중에 지정되면 다음 v3 월간 점검까지 모른다**
- **상황**: v3 크론은 매월 1일 점검·12-30 이듬해 갱신뿐이다(로드맵 §4). 정부가 월중(예: 열흘 전)에 임시공휴일을 지정한다.
- **인풋**: 06:00·18:05 의 `sync_calendar.sh` 가 그 달 1일 판 v3 캐시를 계속 복사한다.
- **에러 위치**: 원천이 v3 캐시 하나(`sync_calendar.sh:11`), quant-ledger 에 자체 갱신이 없다.
- **위험성**: 휴장일에 체인이 돌아 거래일을 하나 만들거나(오판), 반대 방향으로 거래일을 건너뛴다. 지금 게이트는 한 방향(`krx.holiday_misfire`)뿐이라 '휴장일인데 KRX 시세가 들어온' 경우를 못 잡는다.

**CAL-D3: 옛 경로 사본이 판정 디렉터리에 합집합으로 섞인다**
- **상황**: `sync_calendar.sh:52` 가 옛 경로 `data/calendar/kis_holidays.json`(v3 원본 그대로)을 판정 디렉터리에 계속 쓴다.
- **인풋**: `load()` 가 `kis_holidays*.json` 전부를 읽는다(`calendar.py:78-79`).
- **에러 위치**: `calendar.py:78-95` — 같은 연도 파일 둘을 합집합한다.
- **위험성**: 판정 연도 파일을 직접 갱신으로 고쳐도(지정 취소 반영) 옛 경로 사본의 휴장일이 합집합으로 살아남는다 — 직접 갱신의 제거가 조용히 무효가 된다.

**CAL-D4: 18:05 v3 동기화가 06:00 직접 갱신을 덮는다(직접 갱신을 그냥 얹을 때)**
- **상황**: 직접 갱신 결과를 판정 연도 파일에 쓰고, 동기화는 지금처럼 같은 파일을 덮는다.
- **인풋**: 06:00 직접 갱신이 10-20 임시공휴일을 반영 → 18:05 `sync_calendar.sh` 가 그 달 1일 판 v3 사본으로 `kis_holidays_2026.json` 을 덮는다.
- **에러 위치**: `sync_calendar.sh:45-50`(판정 디렉터리 연도 파일 원자 교체).
- **위험성**: 반영한 지정이 사라져 저녁 체인·워치독·다음 날 아침 판정이 옛 달력으로 돈다. 그래서 v3 사본은 판정 디렉터리 밖(`v3/`)으로 옮긴다.

## 3. 변경 내용

### 3-1. 06:00 순서(`scripts/daily_ledger.sh`)
1. `sync_calendar.sh` — v3 사본을 **`data/calendar/v3/kis_holidays_<year>.json`** 에만 쌓는다(판정 디렉터리 연도 파일은 쓰지 않음). 옛 경로 `data/calendar/kis_holidays.json` 은 호환용으로 계속 갱신하지만 `load()` 가 읽지 않는다(`probe_kw_timing.py` 만 읽는다 — §7). 18:05 동기화도 같다.
2. **`python -m daily.calendar_refresh`**(dry-run 이면 `--check`) — 아래 3-2. rc 2 → `notify crit`, rc 1 → `notify warn`(마지막 줄이 '휴장 달력 …' 요약일 때만 — 요약 없이 rc 1 이면 import 실패 같은 사고로 보고 crit). 체인 rc·`FAILED`·`ledger_chain` 런 로그에는 넣지 않는다(달력 갱신 실패가 그날 D 수집을 '실패'로 만들어 다음 06:00 에 전 소스를 다시 받게 하면 안 된다). 직전 판정 달력은 그대로 쓰인다.
3. D 산출 — `load()` 가 실패하면 기존 '대상 거래일 산출 실패 — 중단'(crit, rc 2) 경로로 멈춘다(⑦).
- **운영 주의**: dry-run(`daily_ledger.sh --dry-run`)도 `--check` 로 KIS 를 **실제 1콜** 한다(원장·달력에는 쓰지 않음). 06:00 체인이 이미 부른 날 수동 dry-run 을 하면 그날 2콜째가 되어 KIS 권고('가급적 1일 1회')를 넘는다 — 같은 날 반복 dry-run 은 피한다.

### 3-2. `src/daily/calendar_refresh.py`(신설) — 한 번 실행에 하는 일
실행 순서는 **e(이어 받기) → a~d(창) → f → g → h → i** 다 — 연말에 이듬해 판이 게시된 날, 창이 덮는 이듬해 날짜(예: 1월 초 새 지정)도 같은 날 반영되게 하려고 이어 받기를 먼저 한다.

| 표기 | 일 | 쓰는 곳(적용 모드) |
|---|---|---|
| a | **창 1콜**: `BASS_DT = 오늘(KST)`, TR `CTCA0903R`, 연속조회 없이 1페이지. 오늘 이미 불렀으면(런 로그 `source=kis_holiday` 에 오늘 행) 부르지 않는다 | 런 로그 |
| b | **① 검증**(3-3) → 실패면 그날 창 무효·직전 판 유지·crit | — |
| c | 원장 적재: `data/raw/kis.db` 표 `kis_holiday`(3-4) | 원장 |
| d | 판정 연도 파일 덧씌움: 응답이 덮는 날짜만 바꾼다(그 날짜들의 휴장 = `opnd_yn='N'`). 연도 파일이 없는 해의 날짜는 반영하지 않는다(이듬해 판은 e 로만 만든다). 바뀐 해만 원자 교체(임시 파일 → `os.replace`, 0600) | 달력 파일 |
| e | **연도 판 이어 받기**: 11-21 부터 이듬해 판이 게시될 때까지 하루 1콜(런 로그 `source=kis_holiday_next`). **올해 판이 없으면(연도 경계 사고) 날짜와 무관하게 올해를 대상으로** 같은 방식으로 받는다 — 스스로 회복(약 16일), 빠른 복구는 §8 수동 절차. 커서 = 누적분 마지막 날 + 1(처음엔 1/1). 누적은 `data/calendar/direct/next_<year>.json`. 받은 페이지에서 그 해 1/1(신정)·12/31(연말휴장)이 개장(Y)이면 **KIS 가 그 해 휴장을 아직 싣지 않은 것**으로 보고 누적하지 않는다(원장 적재는 함, warn '이듬해 판 미게시로 보임', 다음 날 같은 커서). 1년치가 차면 전체 검증(3-3) 통과 시 `kis_holidays_<year>.json` 게시 | 원장·누적 파일·달력 파일 |
| f | **④** 올해 판이 없으면 crit(연도 경계를 이미 넘음), 12-15 부터 이듬해 판이 없어도 crit — 문구에 §8 복구 절차를 싣는다 | — |
| g | **③** 판정 달력 거래일 = equity `trading_calendar` 현판(3-5) | — |
| h | **v3 병행 대조**: 판정 연도별 휴장 집합 vs `v3/` 사본 — `only_v3`·`only_direct` 를 보고서에 기록(등급 info) | 보고서 |
| i | 보고서 `logs/calendar/<오늘>.json`, 마지막 줄 요약(개행 없는 한 줄), rc 0(정상)·1(warn)·2(crit). 같은 날 재실행이 호출 없이 끝나면 그날 첫 보고서를 덮지 않는다 | 보고서 |

- `runlog.start` 뒤에 예외(원장 잠김·디스크 등)가 나면 그 런을 `failed` 로 마감하고 예외를 다시 올린다 — `main` 이 rc 2(crit)로 바꾼다. 'running' 으로 남지 않는다.

`--check`(읽기 전용) 모드: a 의 창 1콜만 실제로 부르고(오늘 이미 불렀어도 부른다 — 명시적 수동 점검) b·d·f·g·h 를 메모리에서 계산해 출력한다. 원장·달력 파일·누적 파일·런 로그·보고서에 **아무것도 쓰지 않는다**. 이듬해 이어 받기는 대상 여부·커서만 출력한다.

### 3-3. ① 응답 검증(창·이듬해 페이지 공통) — 하나라도 어기면 그 페이지 전체 무효
1. 호출 판정 `backfill_kis.classify` 가 ok(`rt_cd='0'`). 토큰 만료(EGW00121·EGW00123)만 캐시를 버리고 1회 재발급 후 재시도(`kis_daily.fetch_credit` 와 같은 규칙 — 휴장 서비스에 닿기 전 게이트웨이 거절이다). 그 밖의 오류는 같은 날 재시도하지 않는다.
2. `output` 이 배열이고 1행 이상(빈 응답이 정상 캐시를 덮은 v3 2026-06-05 사고 재발 방지).
3. 모든 행의 `bass_dt` 가 실제 날짜(YYYYMMDD), `opnd_yn` 이 `Y`·`N`.
4. **구간의 모든 날짜**: 첫 날짜 = 요청한 `BASS_DT`, 이후 하루씩 빈틈·중복 없이 이어진다.
5. **주말 포함**: 응답 안의 토·일은 전부 `opnd_yn='N'`.
6. **해당 연도**: 날짜는 자기 연도의 파일에만 반영한다. 이듬해 페이지는 그 해 1/1 이후만 받고 12/31 을 넘는 행은 버린다.
- 1년치 게시 검증(이듬해 판): 1/1~12/31 전 날짜 존재 · 토·일 전부 휴장 · 휴장 ≥ 100건(v3 규칙) · 1/1(신정)·12/31(연말휴장) 휴장. 평일 휴장 수는 기록만.

### 3-4. 원장 기록 — 권고: **응답 원문을 원장에 남긴다**(`kis.db` 표 `kis_holiday`)
- 형식: 응답 열 그대로(TEXT, 새 열은 `ALTER TABLE ADD COLUMN`) + `req_bass_dt`(그 행을 처음 본 요청) + `collected_at`(UTC). PK `row_hash` = 응답 열만으로 만든 해시(`backfill_dart.row_hash` 를 import — 산식 복제 금지). `INSERT OR IGNORE`.
- 결과: 같은 사실을 다른 창에서 다시 받으면 새 행이 생기지 않아 `collected_at` = **최초 관측 시각**으로 남고, `opnd_yn` 이 바뀌면(지정·취소) 새 행으로 남는다 — `kis_daily.store_new_facts` 와 같은 원칙(append-only·정정 보존). 하루 증가는 약 1행(+ 변경).
- **근거**: ① 원장은 외부 응답의 유일한 정본이고 판정 연도 파일은 파생물이다 — 원장(+ 시드 v3 사본)으로 다시 만들 수 있어야 한다. ② 지정을 '언제 처음 알았는가'(최초 관측)가 있어야 지정·취소·감소 경고(⑥)를 사후에 원문으로 확인하고, 판정 재생(RG-C3-03, 채택 보류)이 가능하다. ③ `kis.db` 는 주간 백업(`backup_raw.sh` DBS)에 이미 들어 있다. ④ 비용이 작다(하루 수십 행 이하, 대부분 IGNORE). 원천별 DB 규약(KIS → `kis.db`)을 따른다. stage 는 표 이름을 지정해 읽으므로(`stage/rules_kis.py`) 새 표가 stage 에 섞이지 않는다.
- 달력 파일만 만드는 안(대안)은 '언제 무엇을 받았나'가 덮어쓰기로 사라져 ⑥ 의 원인 확인이 불가능해 택하지 않는다.

### 3-5. ③ 대조 정의
- 구간: 판정 달력이 덮는 가장 이른 해의 1/1 ~ `max(trading_calendar.date)`. 그 구간의 {달력 거래일} = {`trading_calendar.date`}. 한쪽에만 있는 날이 1건이라도 있으면 crit(앞 10건 기록).
- 읽기: `equity.inputs.resolve(<home>/data/equity, "trading_calendar")`(MANIFEST current_build 의 파티션만 — 옛 판 glob 금지).
- equity 를 못 읽거나 겹치는 구간이 없으면 '판정 불가 = 실패'(공통 3) → crit.
- 비교 대상은 06:00 시점에 MANIFEST `current_build` 가 가리키는 판이다(전날 21:20 저녁 잠정판이 커밋됐으면 그것, 아니면 직전 아침 확정판). 어느 쪽이든 `trading_calendar` 는 KRX 지수 날짜(`stg_index_daily`)에서 나오고 KRX 는 T+1 08:00 에 공표하므로, `max(date)` 는 직전 08:10 체인의 D — 오늘 06:00 체인 D 의 바로 앞 거래일이다. 오늘 반영한 창(오늘 이후)은 이 구간 밖이라 ③ 은 시드·과거 정정만 본다.

### 3-6. ② `ledger_health` `krx.holiday_traded`(HALT)
- 08:10 `daily_build.sh` 의 KRX 재수집 창과 같은 폭(직전 10거래일 ~ D, `KRX_RECHECK_SESSIONS = 10` — 테스트가 셸의 `n=10` 과 대조)에서 **시세 엔드포인트**(`ingest_log.endpoint` ∈ `sto/stk_bydd_trd`·`sto/ksq_bydd_trd`·`idx/kospi_dd_trd`·`idx/kosdaq_dd_trd`·`etp/etf_bydd_trd`)의 `status='ok' AND n_rows > 0` 인 날짜가 달력상 휴장일이면 HALT(rc 2·확정 빌드 안 함·crit). 종목기본정보(`*_isu_base_info`)는 시세가 아니고 휴장일에도 행을 줄 수 있어 뺀다(명세 '휴장일에 KRX 시세'). 목록의 정본은 `backfill_krx.EPS` — import 시점에 키를 요구해 들여오지 못하므로 테스트가 소스를 읽어 부분집합인지 대조한다. 기존 `krx.holiday_misfire`(KRX '휴장' ↔ 달력 거래일)의 반대 방향 짝이다.
- 근거: `backfill_krx.py` 는 평일을 전부 부르므로(`bdays`) 달력상 평일 휴장도 KRX 를 친다. 그날 KRX 가 시세를 주면 `ok` 행이 남는다 — 달력이 틀렸다는 직접 증거다.

### 3-7. ⑥ 감소 경고
- 창 덧씌움에서 휴장 → 개장으로 바뀐 날(`removed`)이 있으면 warn. 반영은 한다(KIS 가 원천이고, 잘못 반영돼도 양방향 HALT ②·`krx.holiday_misfire` 가 다음 08:10 에 잡는다). '지정 취소'와 '응답 오류'는 기계로 가르지 못하므로 매번 경고하고 사람이 원장 원문(`kis_holiday` 의 두 행과 `collected_at`)으로 확인한다. 추가(`added`)는 info 로 기록만.

### 3-8. ⑦ 달력을 못 읽으면 중단
- `load()` 는 실패 시 `CalendarUnavailable`(RuntimeError) 를 낸다. `weekend_only`·`Calendar.detail` 제거, 호출부의 죽은 폴백 출력 4곳 제거(`kis_daily`·`kw_daily`·`backfill_krx`·`dart_daily`).
- `load()` 는 **`kis_holidays_<YYYY>.json` 만** 읽는다(CAL-D3). 파일 이름의 연도와 내용의 `year` 가 다르면 실패.
- 셸 호출부는 이미 '판정 불가 → 중단'으로 짜여 있다: D 를 구하는 곳(`daily_ledger.sh:53-57`·`daily_build.sh:91-96`·`build_morning.sh:24-28`·`wics_weekly.sh:35-37`)은 빈 D 면 crit·rc 2, 거래일 판정(`daily_evening.sh:103-114`·`build_evening.sh:32-41`)은 rc 2 = 판정 불가 → crit. 손댈 것 없음. `watchdog.sh:27-29` 는 실패하면 거래일로 보고(`|| echo 1`) 체인 미실행을 알린다 — 알림 쪽 폴백이라 유지(§7 목록에 기록).

## 4. 게이트별 판정 기준과 실패 동작
| 게이트 | 판정 | 실패 동작 | 위치 |
|---|---|---|---|
| ① | 3-3 의 1~6 전부 | 그날 창 무효·직전 판 유지·부분 저장 0·crit(rc 2). 같은 날 재시도 없음(다음 06:00) | `calendar_refresh.validate_page` |
| ② | 직전 10거래일~D 의 달력상 휴장일에 KRX 시세 엔드포인트 ok 행 0 | HALT(rc 2·확정 빌드 안 함·crit) | `ledger_health.check_krx_holiday_traded` |
| ③ | 3-5 구간의 두 거래일 집합이 같다 | crit(기록). 체인은 멈추지 않는다 — Q-CAL-2 = A 확정 | `calendar_refresh.compare_trading_calendar` |
| ④ | 올해 판이 있다 · 12-15 부터 이듬해 판이 `load().years` 에 있다 | 매일 crit(§8 복구 절차 안내) | `calendar_refresh.deadline_finding` |
| ⑥ | 창 덧씌움의 휴장 → 개장 0 | warn(반영은 함) | `calendar_refresh.run` |
| ⑦ | `load()` 성공 | 체인 중단(crit·rc 2) | `calendar.load` + 셸 기존 경로 |
| 이어 받기 페이지 | 3-3 + 1/1·12/31 개장이면 미게시로 봄 | warn(누적 안 함, 다음 날 같은 커서), 1년치 검증 실패는 crit·게시 안 함 | `calendar_refresh._next_year` |
| v3 대조 | 기록만(info) | — | `calendar_refresh.compare_v3` |

## 5. 하루 호출 수
- 평소 1콜(창). 11-21 ~ 이듬해 판 게시(약 16일)는 창 1 + 이듬해 1 = **2콜** — 로드맵 §4 의 "06:00 에 오늘부터 약 24일 구간 … 이듬해 판은 11월 하순부터 하루 1콜씩"을 그대로 읽은 것이다. KIS 예제의 '1일 1회' 권고를 이 기간에 넘는다 — Q-CAL-1 = A 로 확정(10-10).
- 같은 날 같은 용도의 재호출 0(런 로그로 센다). `--check` 는 명시적 수동 점검이라 이 규칙을 따르지 않는다(그날 n 번째 호출인지 출력).

## 6. 테스트 계획(네트워크는 전부 `api.kis` monkeypatch)
- **옛 결함 재현(옛 코드에서 결함 동작 그대로 FAIL — 6건)**:
  - CAL-D1: `test_daily_calendar.py::test_missing_calendar_raises_instead_of_assuming_business_days`·`test_corrupt_calendar_raises`(옛 코드는 weekend_only 폴백), `test_daily_ledger_sh.py::test_unreadable_calendar_stops_the_chain`(진짜 python 으로 D 산출 — 옛 코드는 영업일 가정으로 rc 0 끝까지)
  - CAL-D3: `test_daily_calendar.py::test_legacy_unsuffixed_file_is_not_read`(옛 코드는 합집합)
  - CAL-D4: `test_sync_calendar_sh.py::test_v3_copy_goes_to_v3_dir_and_leaves_the_judging_file`(옛 동기화가 판정 파일을 덮음)
  - CAL-D2 반대 방향 미탐: `test_daily_health.py::test_krx_rows_on_a_calendar_holiday_halt`(옛 코드엔 그 HALT 가 없다)
- **새 동작 검증(옛 코드에서 FAIL 이지만 결함 재현은 아님 — 검사·게이트 이름이 없었을 뿐, 3건)**: `test_daily_calendar.py::test_year_in_file_name_must_match_the_content`, `test_daily_health.py::test_krx_holiday_rows_on_a_calendar_holiday_pass`·`test_krx_rows_on_a_holiday_outside_the_recheck_window_are_not_judged`.
- 신규 동작: `test_calendar_refresh.py`(①·③·④·⑥·이어 받기·`--check` 무쓰기·같은 날 재호출 0·원장 최초 관측·v3 대조 기록)
- 리뷰 지적 회귀(10-10): 아래 '리뷰 반영' 절.
- 회귀: `database/tests` 전체.

## 7. ⑤ 날짜 계산 호출처 목록(리팩터 안 함)
> 10-10 `wip/cal-direct` 9a1ae910 기준 `src/`·`scripts/` 전수(tests·docs 제외). 줄 번호는 이번 변경 **전** 기준이다. 분류 A = 달력 모듈 사용, B = 달력 없이 거래일을 따로 셈, C = 달력일 창(의도), D = equity `trading_calendar`(KRX 지수 날짜) 격자.
> 건수: **A 30**(파이썬 20 · 셸 10) · **B 20** · **C 30**(의도 불분명 2) · **D 20개 파일 약 48곳** · 셸 폴백 5. 단일화(⑤ 본 작업)는 이 목록을 등록표로 삼아 B 를 A 로 옮기고 C·D 는 사유를 적는 일이다 — 이번엔 하지 않는다.

**B 중 휴장일을 무시해 실제로 틀릴 수 있는 곳(위험 순)**
| # | 위치 | 무엇을 세나 | 왜 틀리나 |
|---|---|---|---|
| 1 | `src/probe_kw_timing.py:23`·`:36-49`·`:90`·`:93`·`:114` | 자체 `prev_trading_day`·T−1·T−2 | 옛 경로 `kis_holidays.json` 단일 파일을 직접 읽고 실패하면 빈 집합(주말만). 1월 첫 거래일의 T−1 이 전년 12-31(연말휴장)로 잡힌다. 이번 변경 뒤에도 옛 경로 파일(v3 사본)을 읽는다 — 프로브 증거 DB 만 오염 |
| 2 | `src/compat/quant_db.py:49-50` `INCREMENTAL_DAYS = 14` | v3 `quant.db` 증분 창 '10세션' | 달력일 14일은 평일 최대 11일 — 설·추석처럼 평일 휴장 2일 이상이면 10세션 미만. 08:10 KRX 재수집(10**거래일**, `daily_build.sh:61-62`)보다 좁아 연휴 뒤 정정분이 증분 export 에서 빠질 수 있다 |
| 3 | `src/deliver/excel_weekly.py:62-65`·`:86-89`·`:124-125`·`:514`, `deliver/__main__.py:162`, `deliver/telegram.py:160` | 주간 엑셀 '그 주 거래일 판 N/5'·기준일 | 월~금 고정·분모 5 — 휴장 주(10-05·10-09 의 W41)에도 '3/5'로 결손처럼 보이고, ✕ 가 휴장과 미빌드를 가르지 못한다. 금요일 빌드 실패 시 목요일이 조용히 기준일 |
| 4 | `scripts/compat_export.sh:48` (같은 꼴: `build_chain.sh:31`·`stage/health.py:424`) | D 기본값 = 오늘 KST | 거래일 검사 없음. 지금 호출자는 늘 `--date` 를 준다 |
| 5 | `src/backfill_krx.py:70-74` `bdays` | 평일 전부를 대상일로 | 휴장 평일에도 KRX 7콜(가격 1콜 뒤 생략). ② 는 이 성질을 이용한다(휴장 평일 응답이 원장에 남음) |
| 6 | `scripts/daily_ledger.sh:69` `date +%u` = 1 | DART 번호표 갱신 요일 | 거래일 판정이 아니라 무해 |

**A — 달력 모듈을 쓰는 곳(대표)**: 체인 D 산출 `daily_ledger.sh:50-51`·`daily_build.sh:88-89`·`build_morning.sh:21-23`·`wics_weekly.sh:29-30`, 거래일 판정 `daily_evening.sh:103-111`·`build_evening.sh:32-41`·`watchdog.sh:26-29`(실패 시 `|| echo 1` = 거래일로 보고 체인 미실행을 알림 — 알림 쪽 폴백이라 유지)·`:120-121`·`:207-209`, KRX 재수집 하한 `daily_build.sh:61-62`(빈 값 검사 없음 — D 산출이 먼저 멈추므로 영향은 `--date` 수동 실행뿐), 원장 수집 `kis_daily.py:543·546`(D·D−2)·`kw_daily.py:950-951`·`dart_daily.py:1053·1056`·`backfill_krx.py:129`(빈 응답 pending 판정), 유예 카운터 `universe.py:135`(`count_trading_days`), 건전성 `ledger_health.py:237·546-550·583·620-622`, 프로브 `probe_postclose.py:335·610-613`.

**C — 달력일 창(의도 명시, 대표)**: KIS 조회창 40일 `kis_daily.py:47·118-122`, 원장 중복 창 D−40 `ledger_health.py:551`, WICS 나이 ≤9일 `ledger_health.py:487·524-526`, DART 스윕 `dart_daily.py:769-776`·`sweep_disclosure.py:95-124`, 결산 유예 `backfill_dart.py:76-103`, 키움 역방향 커서 `backfill_kw.py:104-105`, compat 창 `compat/quant_db.py:47-52`, 가격 이력 550일 `factor_inputs/queries.py:28`·`model/engines/v3_zscore.py:49`·`v2_percentrank.py:151·268`·`v4_rank.py:57-58`, 추세 1W·1M `deliver/trend.py:39-85`, stage 신선도 허용 지연 `stage/freshness.py:43-72`(연휴 5일 가정 — 더 긴 연휴면 거짓 FAIL, 거래일 전환 후보), 워치독 시각 창 `watchdog.sh:127-131·242`. 불분명 2: `probe_kw_timing.py:94`(today−14), `equity/rules_s05.py:312-319`(near_dup 창).

**D — `trading_calendar` 격자(대표)**: 정의 `equity/sql/trading_calendar.sql:8-13`, 자체 재정의 `security_span.sql:16-18`, 세션 창 `universe_daily.sql:70-91·163-205`·`adj_factor.sql:132`·`rules_s06.py:264·322`·`views.py:130-131`, 권리락 직전 거래일 `corp_event.sql:56-59`, 달력 밖 격리 `credit_daily.sql:76`·`flow_daily.sql:44`·`price_daily.sql:145`·`rules_s23.py:311·322`, EG17 무결성 `rules_s02.py:87-97`, fi 번호표·유예 나이·신용 랙 `factor_inputs/queries.py:113-147·243·389-390`, D 거래일·60세션 하한 `factor_inputs/build.py:259-275`. 두 달력(운영 KIS·데이터 KRX)을 대조하는 곳은 지금까지 없었다 → ③ 이 처음이다.

**셸·모듈 폴백(이번 변경으로 정리된 것 ✓ / 남은 것)**: ✓ `calendar.py:97-99` weekend_only, ✓ 호출부 경고 4곳(`backfill_krx.py:87-88`·`kis_daily.py:539-540`·`kw_daily.py:948-949`·`dart_daily.py:1054-1055`), ✓ 동기화 실패 문구(`daily_ledger.sh:49`·`daily_evening.sh:98` — 이제 판정 달력과 무관). 남음: `watchdog.sh:29`(위 A, 의도된 알림 쪽 폴백), `watchdog.sh:120-121·207-209`(예외 시 crit 본문이 빔), 경로 해석 세 갈래(명시 경로·`DEFAULT_PATH`·`probe_kw_timing` 자체 경로).

## 8. 배포·되돌리기·서버 확인
- **배포 시점**: main 병합 배포(10-13 관측 뒤) 다음, 운영 체인 밖(10:30~15:10). `deploy.sh --apply --allow-branch <브랜치>`.
- **배포 전**: ① 서버 판정 디렉터리 목록·해시 기록(`ls -la data/calendar` · `sha256sum data/calendar/kis_holidays_*.json`) ② 되돌릴 rev 기록 ③ 체인·락 비어 있음.
- **첫 06:00 뒤 확인**: `logs/calendar/<오늘>.json` 의 창 반영·③·v3 대조, `kis.db` `kis_holiday` 행 수, 런 로그 `source=kis_holiday` n_calls=1, `data/calendar/v3/` 생성.
- **연도 경계 수동 복구**(④ crit '올해 판 없음'·'이듬해 판 기한 초과', ⑦ 정지 메시지가 연도 파일 부재일 때):
  1. 사람이 원인을 확인한다 — `logs/calendar/<날짜>.json` 의 `next_year`·`next_year.deadline`, 누적 파일 `data/calendar/direct/next_<연도>.json`, 원장 `kis.db.kis_holiday`(그 해 1/1·12/31 행의 `opnd_yn`·`collected_at`), 런 로그 `source=kis_holiday_next`.
  2. `data/calendar/v3/kis_holidays_<연도>.json` 이 있으면 내용(휴장 ≥ 100건·토·일 전부·1/1·12/31 휴장)을 눈으로 확인한 뒤 판정 디렉터리 `data/calendar/kis_holidays_<연도>.json` 으로 복사한다(0600). 다음 06:00 창 갱신이 그 위에 덧씌우고, ③·v3 대조가 그날부터 다시 잰다.
  3. v3 사본이 아직 없으면(v3 의 이듬해 갱신은 12-30) 이어 받기에 맡긴다 — 누적 파일이 손상됐으면 지우면 1/1 부터 다시 받는다(하루 1페이지, 약 16일). 더 빨리 필요하면 권고 초과 호출은 사용자 승인 사항이다.
  4. 한 일을 결정 장부·플랜 진행 기록에 남긴다.
- **되돌리기**: 직전 rev 재배포. 판정 연도 파일은 직접 갱신이 바꾼 날짜만 다르다 — `data/calendar/v3/kis_holidays_<year>.json` 을 판정 디렉터리로 복사하면 배포 전 상태다(사람 확인 뒤). `kis.db.kis_holiday` 표는 남겨도 해가 없다(stage 가 읽지 않음). 새로 생긴 `data/calendar/v3/`·`direct/`·`logs/calendar/` 삭제는 사용자 승인.

### 서버 실호출 검증(읽기 전용 — 원장·달력·런 로그·보고서에 쓰지 않음, KIS 1콜)
```bash
cd ~/quant-ledger && export QL_HOME="$HOME/quant-ledger" PYTHONPATH="$HOME/quant-ledger/src"
.venv/bin/python -m daily.calendar_refresh --check
```
- 배포 전에 돌릴 때는 브랜치 코드를 서버 임시 폴더에 펼쳐 `PYTHONPATH=<임시>/src` 로 같은 명령을 쓴다(`QL_HOME` 은 운영 — 읽기만 한다). 주의: `api.py` 의 KIS 토큰 캐시 경로는 `api.py` 옆(`<임시>/src/.kis_token.json`)이라 임시 사본에서는 **토큰을 새로 발급**한다(`api.py:66`·`:81-90`). 발급 1콜을 피하려면 `ln -s ~/quant-ledger/src/.kis_token.json <임시>/src/.kis_token.json` 로 운영 캐시를 가리키게 한다(파일은 열지 않는다 — 재발급이 일어나면 운영 캐시가 갱신된다). 06:00~07:00(KIS 단계·v3 토큰 재발급)은 피한다.
- 확인할 것: 응답 행 수·첫/끝 날짜, ① 통과, 덧씌우면 바뀔 날짜(없어야 정상 — **배포 전에는 판정 파일 자체가 v3 사본이라 '바뀔 날짜 없음'이 곧 v3 와의 일치 확인**이다), ③ 결과(2026-01-01 ~ 현판 `max(trading_calendar.date)` 일치), 오늘 몇 번째 호출인지. **v3 대조는 배포 전엔 `v3 사본 디렉터리 없음 — 대조 못 함`(info)이 정상**이다(`data/calendar/v3/` 는 배포 뒤 첫 동기화가 만든다). 배포 뒤 `--check` 면 연도별 `match: true` 여야 한다. 전후로 `sha256sum data/calendar/kis_holidays_*.json data/raw/kis.db` 와 `logs/calendar/` 유무가 같아야 한다.
- 이 검증 자체가 그날 KIS 휴장 조회 1콜이다(v3 와 같은 앱키면 합산 — 공유 여부 미확인).
- **주말 BASS_DT(10-10 토·10-11 일)로 돌릴 때**: 응답이 BASS_DT(토)부터 시작하고 토·일을 `N` 행으로 포함하면 ① 통과·창 OK 다(근거는 간접뿐이다: v3 2026 판의 휴장 121건에 토·일이 들어 있으니 응답이 주말 행을 준다. 2026 판에 1/1 이 있으니 v3 가 1/1 을 BASS_DT 로 받았다면 휴장 BASS_DT 에서도 그날부터 준다 — v3 `holiday.py` 의 `fetch_year_holidays` 는 `cursor = f"{year}0101"` 에서 시작해 `ctx_area_nk` 로 넘기는 연 단위 수집 코드다(품질 재리뷰 정정 — 옛 문구 '연 단위 수집 코드가 없는 옛 판'은 틀렸다). 휴장일 BASS_DT 는 10-09 실측으로 확인했고(아래 진행 기록), 주말 BASS_DT 는 10-10·10-11 실측 대기). 기대와 다르면 이렇게 보인다: (가) 다음 영업일(10-12)부터 시작 → `window=crit … 첫 날짜 20261012 가 요청한 BASS_DT 와 다르다`, (나) 주말 행을 빼고 줌 → `날짜가 하루씩 이어지지 않는다: 20261009 다음 20261012`(BASS_DT 가 평일이 아니면 (가)가 먼저 걸린다), (다) 빈 output → `빈 응답(output 0행)`, (라) 주말이 `Y` 로 옴 → `토·일이 개장(opnd_yn='Y')으로 왔다`. 어느 경우든 점검 모드라 아무것도 쓰지 않고 rc 2 다. (가)·(나)면 주말 06:00 마다 crit 이 나므로, 실측 원문으로 ① 의 시작일 규칙(예: 첫 날짜 ≥ BASS_DT 이고 그 사이가 전부 주말이면 허용)을 바꾸는 결정이 필요하다 — 지금 코드는 추측으로 넓히지 않았다.
- ② 지난 날짜 재판정(KIS 호출 없음, 운영 리포트 무변경): `.venv/bin/python -m daily.ledger_health --date 20261008 --out "$(mktemp -d)"` — 창(직전 10거래일 = 09-21~10-08)에 추석 09-24·25 와 10-05 대체공휴일이 들어 있다. `krx.holiday_traded` PASS 여야 한다(10-12 판정이면 10-09 한글날도 든다).

## 진행 기록(10-10, 실제)
- **서버 실측 10-09 23:43 KST(휴장일 BASS_DT, 브랜치 임시 사본·운영 토큰 캐시 연결, `--check`)**: rc 0 · 1콜 — `window` ok(창 20261009~20261101 24일, 덧씌우면 바뀔 날짜 추가 0·제거 0 = v3 사본과 일치) · `next_year.deadline` ok · `trading_calendar` ok(2026-01-01~2026-10-08 거래일 187일 일치) · `v3_compare` info(디렉터리 없음 — 배포 전 정상). 실행 전후 `data/calendar/*.json`·`data/raw/kis.db` sha256 같음, `logs/calendar/` 안 생김.
- **서버 실측 10-10(토) 11:4x KST(주말 BASS_DT, 같은 브랜치 사본 — 파일별 sha256 d3995a93 와 같음, `--check`)**: rc 0 · 1콜 — `window` ok(창 **20261010~20261102 24일** — BASS_DT 토요일부터 시작, 토·일 포함해 날짜가 하루씩 이어짐, 추가 0·제거 0) · `next_year.deadline` ok · `trading_calendar` ok(187일 일치) · `v3_compare` info(대조 디렉터리 없음). 실행 전후 `data/calendar/*.json`·`data/raw/kis.db` 해시 같음, `logs/calendar/` 생기지 않음. §8 (가)~(라) 해당 없음 → ① 시작일 규칙 그대로(결정 불필요). 10-11(일) 실측은 같은 경로라 생략 가능.
- **② 재판정 10-09 밤(`ledger_health --date 20261008`, 임시 출력)**: `krx.holiday_traded` pass · `krx.holiday_misfire` pass(추석 09-24·25·10-05 포함 창) · 전체 26/27 pass(나머지 1 = 기존 `kis.credit.dup_growth` skip), 원장 파일 mtime·크기 변화 0.
- 옛 코드(9a1ae910)에서 FAIL 하는 테스트 9건을 먼저 확인했다. 그중 **옛 결함을 재현하는 것은 6건**(CAL-D1 3·D3 1·D4 1·② 미탐 1 — §6), 나머지 3건은 새 동작 검증이다(이름·내용 연도 대조, ② 의 PASS 두 경우 — 옛 코드엔 검사·게이트 이름이 없어 FAIL).
- 폴백 폐지로 드러난 숨은 의존: `test_daily_kis.py`(main 2건)·`test_daily_kw.py`(26건)가 임시 루트에 달력 없이 '주말만 거름' 폴백에 기대고 있었다 — 픽스처에 2026 연도 파일(주말만)을 넣었다. 운영 코드에 같은 의존이 있으면 ⑦ 로 그 체인이 멈춘다(서버엔 2026 판이 있으므로 해당 없음).
- 실행 순서 의존 1건: 새 테스트가 `backfill_kis` 를 먼저 import 해 두면 `test_daily_dart` 가 `backfill_dart` 만 다시 import 해서 `test_daily_kis` 의 `_spec_shim` 이 KeyError 로 깨졌다 — `test_calendar_refresh.py` 의 픽스처가 끝날 때 두 모듈을 import 전 상태로 되돌린다(다른 테스트 파일은 손대지 않음).
- 1차 결과: `database/tests` 전체 2,221 passed · 12 skipped · 0 failed(7분 44초, 공유 venv). 새·수정 파일 ruff·pyright(basic) 통과 — 기존 파일의 옛 I001 3건(`ledger_health.py`·`test_daily_calendar.py`·`test_daily_kis.py`)과 `test_daily_health.py` 의 옛 pyright 지적은 이번 변경 전부터 있던 것이라 그대로 뒀다.
- `_spec_shim` 순서 의존의 뿌리는 TECH_DEBT B-77 로 남겼다.
- 런 로그 `source=kis_holiday`·`kis_holiday_next` 의 date 는 KST 달력일이다. `daily_report.py` 는 D 날짜의 모든 런을 보여 주므로, 거래일 06:00 의 갱신 실패는 그 D 리포트에도 '수집 실패 런'으로 한 번 더 뜬다(의도 — P1).

## 리뷰 반영(10-10 — 명세 '준수'·품질 '조건부 승인')
| 지적 | 고친 곳 | 회귀 테스트 |
|---|---|---|
| I-1·중-1 연도 경계: 올해 판이 없으면 ④ 가 거짓 OK, 이어 받기가 회복하지 못함 | `deadline_finding`(올해 부재 crit + §8 복구 안내 `RECOVERY_HINT`), `next_year_target`(올해 부재면 올해), §8 '연도 경계 수동 복구' | `test_next_year_deadline_is_dec_15`(2027-01-02·{2026} → crit 외), `test_missing_this_year_is_crit_with_the_recovery_procedure`, `test_next_year_target`, `test_missing_this_year_is_paged_from_jan_1` |
| I-2 이듬해 판 미게시 페이지 누적 | `_next_year` — 1/1·12/31 이 Y 면 누적 안 함(원장 적재는 함)·warn·같은 커서 | `test_unpublished_next_year_page_is_not_staged` |
| I-3 정지 메시지 경로 | `calendar.read_year_files`(파일별 예외를 연도 파일 전체 경로와 함께 `CalendarUnavailable` 로), `load`(옛 경로 대신 판정 디렉터리) | `test_corrupt_year_file_is_named_with_its_full_path`, `test_missing_calendar_raises_instead_of_assuming_business_days`(dir= 로 바꿈) |
| 하-4 ② 범위 = 시세 | `ledger_health.KRX_PRICE_ENDPOINTS`·`check_krx_holiday_traded`(`endpoint IN` 시세 5개) | `test_base_info_rows_on_a_holiday_are_not_price`, `test_price_endpoints_are_backfill_krx_endpoints` |
| Minor 2·하-6 예외 시 런 로그 | `_fail_run` — `_window`·`_next_year` 의 `runlog.start` 뒤 예외는 failed 마감 후 다시 올림(main rc 2) | `test_exception_after_runlog_start_closes_the_run_as_failed` |
| Minor 1 같은 날 재실행 보고서 | `run.finish` — 호출 0 이고 보고서가 있으면 덮지 않음 | `test_same_day_rerun_without_a_call_keeps_the_first_report` |
| Minor 3 창 폭 대조 | — | `test_recheck_sessions_match_daily_build_krx_step`(셸 `n=9` 로 바꾸면 FAIL 확인) |
| Minor 4 요약 개행 | `RefreshReport.summary` | `test_summary_is_one_line` |
| Minor 5 연말 같은 날 반영 | `run` 순서: 이어 받기 → 창 | `test_publish_runs_before_the_window_so_year_end_lands_the_same_day` |
| 하-3 v3 에만 있는 연도 | `compare_v3`(판정 쪽 부재를 `judging: missing`) | `test_v3_only_year_is_recorded` |
| Minor 7 공백 | — | `test_network_exception_is_call_failed`, `test_token_still_expired_after_reissue_is_call_failed`, `test_next_year_page_failure_is_warn_and_keeps_the_cursor`, `test_window_across_the_year_boundary_updates_both_files`, `test_store_raw_adds_a_new_response_column`, `test_next_year_already_called_today_is_skipped` |
| Minor 8 | TECH_DEBT B-77 | — |

- 반영 뒤 결과: `database/tests` 전체 **2,246 passed · 12 skipped · 0 failed**(8분 19초). 새·수정 파일 ruff·pyright(basic) 통과(옛 지적은 그대로).
| 하-1·하-5·운영 주의 | §6 재현/새 동작 분리, §3-5 비교 대상, §8 배포 전 v3 대조 문구, §3-1 dry-run 1콜 | — |

## 9. 사람이 정할 것 — 10-10 확정(컨트롤러 전달), 기록용
- **Q-CAL-1 이듬해 이어 받기의 호출 수 → A 확정**: (A) 11-21 부터 창 1 + 이듬해 1 = 하루 2콜 약 16일(로드맵 문구 그대로, **구현 기본값·권고** — 12-06 무렵 끝나 기한까지 9일 여유) · (B) 하루 1콜 엄수 — 11-05 부터 창·이듬해를 하루씩 번갈아(창 공백 1일, 약 32일) · (C) 하루 1콜 엄수 — 이어 받는 날엔 창을 쉰다(약 16일 동안 임시공휴일 감지 공백). 권고 A: 권고 초과는 연 16콜이고 v3 도 같은 기간 월간 점검·12-30 갱신으로 부른다. B·C 는 상수 한 곳(`NEXT_YEAR_FROM`)과 분기 몇 줄로 바꿀 수 있다.
- **Q-CAL-2 ③ 불일치 때 체인을 멈출지 → A 확정**: (A) crit 기록만(**구현 기본값·권고** — P3 '체인이 멈추는 조건'은 사람이 정한다, ②·`krx.holiday_misfire` 가 이미 양방향 HALT) · (B) 06:00 체인 중단.
- **Q-CAL-3 첫 가동 직후 판정 디렉터리 정리 → 옛 파일 유지 확정**: 옛 경로 `data/calendar/kis_holidays.json`(v3 사본, `load()` 가 더는 읽지 않음)을 그대로 둘지. 권고: 그대로 둔다(probe 가 읽고, 서버 데이터 삭제는 항목별 승인 대상).
- **나중(이번 범위 밖)**: v3 사본 동기화를 끄는 시점(일치 30일 — 로드맵 §4), ⑤ 단일화 리팩터, RG-C3-01 의 기대 휴장 집합(E_Y) 등록.
