# 배포 묶음 6 — Q-5 수정주가 미해결 사건을 가격 축에서만 바로잡기(⑤, equity e1.26.0)

> 결정은 [`DECISIONS.md`](../DECISIONS.md) N-32 ②(10-08 사용자 '⑤ 먼저, 묶음 6')에 있다. 이 문서는 작업과 진행만 적는다. 조사 전문은 저장소 밖 로컬 `~/quant-data/research/2026-10-08-batch5/`(5-7 조사·0단계 대조)에 있다.
> 상태: **10-08 사용자 승인(N-33, D6-1~D6-8 권고안 그대로).** 구현 착수 10-08 저녁.

## 요약
- 원인을 equity 한 곳(`adj_factor`)에서 고친다. 열 2개를 더한다: 가격 전용 계수 `price_only_factor`(= 그날 KRX 기준가 ÷ 직전 행 종가)와 가격 축 해소 표식 `price_resolution`.
- 계수는 미해결(`factor_ok=false`) 사건 중 그날 KRX 기준가 근거가 있는 (종목, 날짜) 단위마다 **한 행에만** 싣는다.
- **보유 수량 경로는 그대로다.** 계수를 실은 행도 `factor_ok=false`·`price_factor=share_factor=1`·`factor_source` 그대로라, 보유 수량을 조정하는 두 백테스트 어댑터와 EGC-04 계약이 보는 사건 집합은 바뀌지 않는다.
- 새 계수를 읽는 곳은 `price_adj_daily` 와 조정가 매크로 `v_adj_price_fwd`·`v_adj_price` 셋뿐이다. 바뀌는 값은 조정 시가·고가·저가·종가, 거래량 축과 기존 누적계수 열은 그대로다.
- fi 는 `adj_ok` 를 '가격 축에 남은 미해결'로 좁힌다(fi1.3.0). scope 엔진·엑셀 코드는 바꾸지 않는다(이름 scope_v1.0 유지 — 입력 결함 수정).
- 대상 규모(로컬 사본 10-03 판 e1.22.0): 2,432 사건일 · 1,234종목(2016 이후 1,427 · 833).
- 판본 e1.26.0, main 병합은 e1.27.0 으로 밀린다.

## 진행률

| ID | 무엇 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|---|
| 6-1 | `adj_factor`: ⑤ 계수 열·가격 축 표식·계수를 실은 행의 공개일, EG3_adj_factor·EG8 | 완료 | 완료(f5e14b8e · D6-3 확대 640e5902 — factor_near 144, capred_paid 제외) | 통과(상 0·중 1 반영) | 10-09 rev 9a022e7f | 수동 빌드 통과 | 10-12 21:20 첫 e1.26.0 잠정판 |
| 6-2 | `price_adj_daily`·조정가 매크로 2개: 가격 전용 누적, EG3 재계산·매크로 정합, 골든 | 완료 | 완료(f5e14b8e) | 통과 | 10-09 rev 9a022e7f | 수동 빌드 통과 | 같음 |
| 6-3 | fi: `adj_ok` = 가격 축 미해결만, `adj_factor` 열 = 총 배수(fi1.3.0) | 완료 | 완료(eeed5b8a, 옛 판 거부) | 통과 | 10-09 rev 9a022e7f | 수동 빌드 통과 | 10-13 08:10 자동 발송 |
| 6-4 | 판본(e1.26.0)·이력 주석, 문서(FIELD_MAP·GATES·DESIGN·HANDOFF), TECH_DEBT, Q11 목록 | 완료 | 완료(6-1·검토 반영 — B-64~68, Q11 ⑤ 줄은 로컬 목록) | 통과(묶음 검토 하 2~5) | — | — | — |
| 6-5 | 서버 재연(읽기 전용)·임시 폴더 격리 빌드 2회, v3 대조(G1), scope 순위 차분 | 완료 | **완료(10-09, 하루 앞당김) — ①~⑤ PASS** | — | — | — | — |
| 6-6 | 배포, 수동 전량 2패스, EG5c 재기준, 계약 확인, (D6-7) 1M 판 재생성 | 완료 | — | — | **10-09 19:0x rev 9a022e7f(사용자 '배포도 해도 되잖아' — 10-12 에서 당김)** | 수동 2패스·재기준·계약·22일 재생성 통과 | 10-12 21:20 첫 e1.26.0 잠정판 · 10-13 08:10 확정판·발송 |
| 구조 검토 | 단계 끝 전체 검토 1회 | — | — | 대기 | — | — | — |

## 배포 기록(10-09 저녁, 실제) — 휴장일, 체인 유휴
- 사전: 체인 0 · 원장·빌드 락 비어 있음 · 되돌릴 rev d12085bd(묶음 5) · 맥 전원 연결. feat/v3-merge 를 wip/b6-int 로 빨리 감기(9a022e7f — feat 대비 차이는 문서뿐).
- `deploy.sh --apply` 테스트 2,123 passed, 바뀐 파일 11개 md5 11/11, 서버 판본 e1.26.0 · fi1.3.0.
- `equity_rebuild_all.sh b6_pass1`: 29표 rc 0 · e1.26.0 · 게이트 전부 pass(EG5a 첫 판 skip), 593초, 최대 RSS 8.1GB.
- `equity catalog`: EG5c FAIL n_diff 58,277 → 로컬 예측 파일(`~/quant-data/research/2026-10-09-batch6-replay/b6_eg5c_expected.json`)과 대조 — 총 58,277·뷰별 n_diff(v_adj_price 27,600 · v_adj_price_fwd 30,677 · 나머지 0)·종류 changed 만·열 증감 없음·차이 키 앞 10개·직전 표본 id b0edec021f07df01 모두 일치 → `--rebase-asof --reason "…(N-33 D6-6 사전 승인 …)"` EG5c pass. `equity contract` EGC-01~05·10 pass(EGC-04 ratio = share_factor, ok 2,036건).
- `b6_pass2`: 29표 rc 0, content_hash 1회차와 29/29 같음(591초, RSS 8.25GB). 2회차 판으로 catalog 재실행 EG5c '직전 표본과 동일'.
- fi·모델 확인과 D6-7 재생성은 묶음 7 배포 뒤 함께(아래 묶음 7 플랜 배포 기록).

## 6-5 서버 재연 결과(10-09 오후, 실제) — 전문은 로컬 `~/quant-data/research/2026-10-09-batch6-replay/`
- 기준: wip/b6-int 1de45d49 코드 사본 · 입력 운영 10-09 확정판(D=20261008, e1.25.0) · 서버 임시 폴더만(끝나고 삭제), 운영 판·락·notify·크론 무변경 확인(DEPLOYED d12085bd).
- ① 메모리 재연 PASS(50초, RSS 6.3GB): adj_factor 5,602행 — ok 2,041행 기존 열 변경 0, not-ok 은 새 열 2개 + 계수 행 2,220개 available_date 만. ⑤ 단위 2,334 · 1,205종목, factor_near 144 · price_only_near 367 · unresolved 599 · price_only_dup 117, 단위당 계수 행 1. price_adj_daily 조정 OHLC 만(adj_close 2,476,113행 · 1,205종목), 첫 단위일 이전·⑤ 밖 변경 0, 거래량·누적계수·미조정 수·available 변경 0.
- ② G1 PASS: 207940·000880·084010·001230·051360 새 조정 일간 수익률 v3 대비 ≤ 0.032%p(옛 판 23~63%p). 0단계 150건 실제 산출 148/150(예외 2건 09-14 이후 애프터마켓). 2016 이후 미해결 창 |r|>30% 333 → 29행(플랜 추정 15 — 19행은 unresolved 창 밖으로 보이나 행별 미확인).
- ③ EG5c 예측: 표본 6종목(000070·035720·068270·207940·247540·900050)만 changed — v_adj_price 27,600 · v_adj_price_fwd 30,677(합 58,277), v_cum_adj·v_adj_volume_fwd 0, 열 증감 없음. 임시 루트 catalog EG5c FAIL 보고와 규칙 예측이 양방향 0건 차이 → 배포 날 `--rebase-asof` 승인 근거(`b6_eg5c_expected.json`).
- ④ 격리 빌드 PASS: equity 30표 2회 content_hash 30/30 동일(596·595초), 운영과 다른 표는 adj_factor·price_adj_daily·dataset_profile(문구) 3개, 게이트 전부 pass, EG8 계수 행 max |수정수익률| 0.300(1.0 초과 0 → D6-5 폐기형 승격), 임시 루트 rebase 리허설 뒤 EG5c pass, 계약 EGC-04 not-ok 방출 0(6항 pass). 최대 RSS price_adj_daily 8.17GB(+0.48GB), +26초. fi 는 fi_adj_prices 만 변경(7표 해시 동일), 모델 rc 0.
- scope 순위(D=20261008): 593종목 중 107종목 변동, 최대 21계단(000880 65→86), Spearman 0.99998, 상위 30 집합·순서 그대로. 252세션 안 adj_ok=False 종목 32 → 4. 207940 r12m +8.2% → −25.9% 인데 순위 그대로(엔진 확인 안 함 — 미검증).
- 부수: 같은 시각 서버 `src/stage/__pycache__/parsers*.pyc` 가 다시 쓰였다(다른 조사 세션의 import 로 추정, 바이트코드 캐시라 운영 무영향). v3 quant.db 는 `mode=ro` 로만 읽었으나 WAL 모드라 -shm 파일 시각이 바뀐다(본체·wal 불변) — 다음부터 v3 읽기는 이 점을 감안.

## 이어갈 지점(10-09 새벽 갱신)
- 통합 `wip/b6-int` 1de45d49 = 6-1·6-2(f5e14b8e) + D6-3 확대(640e5902) + 6-3 fi1.3.0(eeed5b8a) + G8 핫픽스(1425a9ba) + feat/v3-merge(묶음 5) + 검토 반영(근처 표식 폐기형 술어 2개·문서·B-64~B-68). 전체 2,122 passed · 12 skipped(맥 — 실물 flock skip).
- 묶음 전체 검토: 상 0 · 중 1(반영) · 하 5(반영). look-ahead·결정성·fi·매크로 열 불변·성능 통과(로컬 사본 EG3_adj_factor 1.9초, price_adj_daily 재계산 RSS 3.3GB).
- 다음: 6-5 서버 재연(10-10~11, 읽기 전용 + 임시 폴더 격리 빌드) → 6-6 배포 10-12.

## 이어갈 지점(10-08 16:4x)
- 통합 `wip/b6-int`(c17e9586 = wip/b6-eq 640e5902 + wip/b6-fi eeed5b8a). 전체 테스트·묶음 전체 검토(명세+품질: look-ahead·게이트 독립성·조인 폭증)가 돌던 중 — 결과 보고 뒤 반영.
- 로컬 사본(10-03 판) 실측: ⑤ 단위 2,333 · 1,205종목(D6-1 적용), factor_near 144, unresolved 604, price_only_near 367. v3 대조 5건 0.03%p 안. scope 근사 유니버스 미해결 종목 252세션 30 → 5(서버 e1.25.0 에선 약 3 예상).
- 다음: 검토 반영 → 10-10~11 6-5 서버 재연(읽기 전용 + 임시 폴더 격리 빌드, EG5c 예측 집합) → 10-12 배포.
- 로컬 측정은 duckdb `memory_limit 3GB · threads 4`, 한 번에 하나(10-08 맥 스왑 고갈 뒤).

## 사실 근거

### 코드(feat/v3-merge cdbf2caa)
- adj_factor: 미해결 행은 계수 1·`factor_ok=false`(`src/equity/sql/adj_factor.sql:419-422`). 기준가 후보 `bp` = 비ETF·구간 첫날 아님·|기준가 ÷ 직전 행 종가 − 1| > `base_price_tol_rel`(`:293-304`). (c) 정지 재개 재발견은 행을 만들지 않는다(`:88`·`:360`). 공개일 식 `:435-439` — 기준가가 확정한 ok 행만 min(announce, apply)(C-07), 정상 아닌 행은 다음 세션.
- price_adj_daily: ok 계수만 접는다(`sql/price_adj_daily.sql:55-60`), 조정가 = 원주가 × `cum_share_factor`(`:126-129`), 미해결 수는 `NOT factor_ok` 전부(`:85-104`).
- 게이트: EG3_price_adj_daily ② `cum_price × cum_share = 1`(`rules_s23.py:270-271`), ④ 독립 재계산 7축(`:295-301`), ⑨ 매크로 정합(`:49-54`·`:161-230`). EG3_adj_factor not-ok 행 계수 1 검사(`rules_s06.py:200-201`), 공개일 재계산(`:262-266`).
- 매크로: `views.py:118-168`(`_FWD_CTE`)·`:184-210`(`v_cum_adj`)·`:212-222`(`v_adj_price`).
- 소비자: 워크벤치 백테스트 `backend/.../equity_duckdb/_adapter.py:949-957`(`WHERE factor_ok`, share_factor) · 커널 어댑터 `backend/src/backtest_engine/adapters/equity_duckdb.py:649-651`·`:92` · 계약 EGC-04 `src/equity/contract.py:405-459`(not-ok 방출 0) · 연구 패널 `_specs.py:532-549`(`price_adj_daily.adj_close` 직독) · compat `src/compat/units.py:80-81` · fi `src/factor_inputs/queries.py:311-333`(`bad` = `NOT factor_ok` 전부, `adj_factor` = `cum_share_factor`) · v4 `src/model/engines/v4_rank.py:174-201` · scope 는 `adj_ok` 를 읽지 않는다(`v3_zscore.py:21`) · 주간 엑셀 `src/deliver/excel_weekly.py:407-425`.
- 카탈로그: as-of 표본 뷰 4개 중 `v_adj_price`·`v_adj_price_fwd` 값이 바뀐다(`catalog.py:57-59`). EG5c 는 차이가 있으면 FAIL, `--rebase-asof --reason` 으로만 승인(`:289-361`·`:484-507`). 체인은 카탈로그 실패를 equity 실패로 친다(`scripts/build_chain.sh:314`).
- 골든: `src/equity/fixtures/price_adj_daily.json` fx2_015(247540 2022-06-24·06-27 조정가)가 247540 2022-05-09 ⑤ 단위 때문에 바뀐다. fx2_016(`n_unadjusted_events`=1)은 뜻을 유지하면 그대로.

### 로컬 사본 실측(10-03 판 e1.22.0, 읽기 전용 — 서버 현판과 끝단 몇 건 다를 수 있음)
- 미해결 행 3,565. ⑤ 단위 2,432 (종목, 날짜) · 1,234종목. 단위 안 행 2,549(unknown_price_only 1,744 · krx_base_inconsistent 727 · no_price_match 70 · ratio_null 6 · same_day_suppressed 2).
- 단위당 KRX 기준가 행: 1개 2,427 · 2개 5(같은 날 감자 성분 4, 123420 병합+무상 1). 계수를 실을 행이 없는 단위 0(② 행이 있는 단위 기준). 기준가 후보·not-ok 행은 있는데 ② 행이 없는 날은 (c) 재발견과 DART 행이 같은 날이면 1 이상일 수 있다(e1.25.0 재연 1건 — 086830 2016-05-19 ratio_null). ok 계수가 같은 날 접혀 제외되는 단위 1.
- 단위 밖 미해결 1,016: ±[5, 40] 세션 안 ⑤ 단위 있음 383 · 가까이에 ok 계수만 130 · 근거 없음 489 · 자기 날짜 기준가 후보 있으나 단위 아님 14(ok 접힘일). 2025-10 이후: 단위 안 173 · 근처 ⑤ 39 · 근거 없음 38 · 근처 ok 9 · 기타 1(0단계 '39행 / 49행' 갈래와 같다).
- 기준가 비율 r: 0.0019~120, |r−1| > 30% 610건, ≤ 5% 838건.
- 계수를 실을 행 2,437행 중 2,318행이 available > apply — 옛 공개일 식 그대로 접으면 C-07 과 같은 하루 늦은 가짜 급락·반등이 생긴다.
- 종류: common 2,175 · preferred 96 · fund 56 · spac 38 · reit 35 · foreign 28 · ship_fund 8 · dr 1. fund 56행은 r 전부 < 1(중앙 0.968), reit 35행도 전부 < 1(중앙 0.984) — 분기·반기 반복 하락이라 분배·배당락으로 보인다(추정).
- 표본 영향: as-of 표본 20종목 중 ⑤ 단위 6종목(000070·035720·068270·207940·247540·900050) — EG5c 차이 확정. 절단본 15종목 중 ⑤ 단위 2건(247540 2022-05-09 r = 491,300/498,500 · 900050 2011-02-16 r = 10,150/10,250).

### 서버(읽기만, 10-08 14:4x)
- 배포 rev a8261680. adj_factor·price_adj_daily 현판은 10-08 아침 판 e1.24.0, 코드는 e1.25.0·fi1.2.0.
- 10-09 는 KRX 휴장(달력 파일에 20261009).

### main(로컬 원격 추적 e8400fba)
- `views.py` 에 `v_unfolded_event`·`v_adj_close`(#220·#369). `v_adj_close` 는 krx_base_inconsistent 적용일 행을 결측으로 가린다. 워크벤치 백테스트 포트(#369)는 그 사건의 보유 수량을 적용일 기준가 비로 조정하고 경고 `equity.unfolded_level_shift` 를 낸다. 커널 어댑터 `backtest_engine/adapters/equity_duckdb.py` 는 main 에서 삭제됐다.

## 설계: ⑤ 를 가격 축에만 싣는다

### adj_factor (6-1)
- 새 열 `price_only_factor DOUBLE NOT NULL`: 계수를 실은 행은 r = 그날 KRX 기준가 ÷ 직전 행 종가(방향은 `price_factor` 와 같다 — 기준가 교체 ok 행의 `price_factor` 가 이 r, `adj_factor.sql:335`), 다른 행은 1.
- 새 열 `price_resolution VARCHAR`(폐쇄 어휘): `factor`(factor_ok) · `price_only`(계수를 실은 행) · `price_only_dup`(같은 단위의 다른 not-ok 행) · `price_only_near`(D6-2) · `factor_near`(D6-3) · `unresolved`(나머지).
- 단위 = (ticker, d): ① `bp` 후보 ② 그날 apply_date 인 not-ok 행 중 `factor_source ∈ {krx_base_inconsistent, unknown_price_only}` ∧ `apply_basis='krx_base_price'` 가 하나 이상 ③ 그날 ok 계수가 접히지 않음(ok 행의 apply_date 와 greatest(apply_date, available_date) 모두) ④ 종목 종류가 대상(D6-1).
- 계수를 실을 행 = ② 중 event_id 가 가장 작은 1행. 값은 그 행의 계수가 아니라 그날 `bp.r`(성분 루트는 잔여 비율을 가지므로, `:335-337`).
- 공개일: 계수를 실은 행은 least(announce_date, apply_date) — C-07 규칙을 이 행으로 넓힌다(r 이 그 세션 KRX 일별 행에서 오므로). 나머지 행은 옛 식.
- 그대로: `factor_ok`·`factor_source`·`price_factor`·`share_factor`·`apply_date`·`apply_basis`·행 수(EG1). 창 상수 신설 없음 — 두 줄 사건 창 [−5, +40] 은 기존 `price_match_lookback_sessions`(5)·`price_match_window_sessions`(40)와 같다.

### price_adj_daily (6-2)
- `adj_{open,high,low,close}(d) = 원주가 × cum_share_factor(d) ÷ cum_price_only_factor(d)`, `cum_price_only_factor(d) = Π(price_only_factor : price_resolution='price_only' ∧ 같은 구간 ∧ fold_date ≤ d)`, fold_date 는 ok 계수와 같은 `greatest(apply_date, available_date)`(계수를 실은 행은 공개일이 바뀌어 apply_date).
- `adj_volume_shr`·`cum_price_factor`·`cum_share_factor`·`n_factors_applied` 그대로 — EG3 ② 곱 1 불변식 유지.
- 새 열: `cum_price_only_factor` · `n_price_only_applied` · `n_price_unresolved_events`(D6-4).

### 매크로
- `v_adj_price_fwd`(`_FWD_CTE`)·`v_adj_price` 는 같은 ⑤ 누적을 곱한다. 출력 열은 늘리지 않는다 — EG5c 차이를 ⑤ 로 바뀐 행으로만 한정해 승인 전 예측 집합과 대조한다. `v_cum_adj`·`v_adj_volume`·`v_adj_volume_fwd` 그대로. 전방·후방 조정의 종목별 상수배 성질(`test_equity_s06_views.py:270`) 유지.

### 기각한 안
- ⑤ 를 factor_ok=true 로 싣기 — 두 어댑터가 share_factor 로 보유 수량을 1/r 배로 바꿔 인적분할·권리락에서 가짜 손익.
- cum_share_factor 에 접기 — EG3 ② 가 깨지고 주식수 축에 가격 전용 조정이 섞인다.
- 별도 표 — 기준가 후보 계산이 두 곳(P4 위반).

### 소비자별로 보이는 것

| 소비자 | 읽는 것 | ⑤ 뒤 |
|---|---|---|
| 워크벤치 백테스트 `_adapter.py:949-957` | `WHERE factor_ok` share_factor·apply_date + 원주가 bar | 변화 없음(보유 수량·손익 그대로, H-04 는 남음) |
| 커널 어댑터 `equity_duckdb.py:649-651` | `_FACTOR_COLUMNS`(:92), factor_ok 아니면 건너뜀 | 변화 없음. EGC-04 가 'not-ok 방출 0'을 매 빌드 확인 |
| 연구 패널 `price.adj_close`(`_specs.py:532-549`) | `price_adj_daily.adj_close` | 단위 날짜 점프가 사라진다(사건일 뒤 수준 1/r 배, 그날 수익률 = close/base − 1) |
| compat `units.py:80` → v3 형식 `daily_prices.adj_close` | 같은 열 | 키움 수정주가와 같은 방식. 체인 밖이라 다음 수동 export 부터 |
| fi `fi_adj_prices` | adj_close · `adj_factor` · `adj_ok` | adj_close 연속. `adj_factor` = cum_share ÷ cum_price_only('원가 × 계수 = 수정가' 유지). `adj_ok` 는 `price_resolution='unresolved'` 에서만 뒤집힘 |
| scope(v3_zscore) | fi adj_close | 모멘텀 r1m~r12m·std_20d 의 단위 점프가 사라진다. 엔진 코드·이름 그대로 |
| v4 `v4_rank.py:181-184` | adj_close + adj_ok | '수정주가미해결' 결측이 남은 미해결로 줄고 연속값이 채워진다(엑셀 비교 열은 빠져 있음, §8-17) |
| 주간 엑셀 `excel_weekly.py:407-425` | adj_close·adj_ok | '결측(수정주가미해결)'이 줄고 1주 수익률이 연속값(자동 발송은 보류 중, N-22) |
| universe_daily | adj_factor (ticker, apply_date, factor_ok, event_type) | 값 변화 없음 |
| main 병합 뒤 #369 포트·`v_adj_close` | `v_unfolded_event` | 포트는 그대로 맞다. `v_adj_close` 는 ⑤ 로 맞아진 적용일을 가리게 되므로 병합 때 가림 술어를 좁힌다 |

### 두 줄 사건 — 계수는 한 번만
- 같은 날 두 줄: (종목, 날짜) 단위로 한 행에만 계수, 같은 날 다른 행은 `price_only_dup`·계수 1. 해당(로컬 사본): DART 명목 행 no_price_match 70 · ratio_null 6, 같은 날 억제 2, 사건 교체에서 밀린 krx_base_inconsistent 34, 084010 2026-01-05 형(bonus nominal + unknown_price_only), 성분 감자 쌍(event_id 작은 쪽).
- 날짜가 다른 DART 행에는 어떤 경우에도 계수가 붙지 않는다(그 날짜엔 기준가 후보가 없어 r 이 없다). 표식은 D6-2: 근처(±[5, 40] 세션)에 ⑤ 단위가 있고 같은 창에 (c) 재발견 후보가 없으면 `price_only_near`, 아니면 `unresolved`. 기준가 근거 없는 DART 행(최근 49 · 전체 489)은 `unresolved`.

### fi `adj_ok` 와 v4
- `queries.py:321-326` 의 `WHERE NOT factor_ok` → `WHERE price_resolution = 'unresolved'`(equity 열 하나만 읽는다, P4).
- C-05 정밀화: ⑤ 처리 사건과 같은 날 중복본은 이번에 빠진다. 다른 날 DART 중복본은 D6-2, ok 형제 억제 중복(C-05 원안)은 D6-3.
- v4 영향(추정, 5-7 §2-2): scope 근사 유니버스의 미해결 종목 30 → 약 3(기준가 근거 없는 DART 행만 있는 종목, 052400 포함). r12m 결측 28셀 대부분이 연속값으로.
- 판 섞임: fi1.3.0 은 e1.26.0 판(새 열)이 있어야 돈다 — 옛 판을 읽으면 열이 없어 실패한다(P1, 섞임이 조용히 지나가지 않는다).

## 작업 규칙(갈래별)

### 6-1 adj_factor (wip/b6-eq)
- Files: `src/equity/sql/adj_factor.sql`(끝단 `:405-445` 를 CTE 로 감싸고 ok 접힘일·단위·계수 행 CTE 와 최종 SELECT 새 열 2개·공개일 분기), `src/equity/rules_s06.py`, `src/equity/fixtures/adj_factor.json`, `tests/test_equity_s06_adj.py`.
- 종목 종류는 SQL 화이트리스트 문자열, `rules_s06` 상수와 같은지 테스트로 묶는다. 숫자 리터럴은 0·1·2 만, 창은 `_const`. 범위 조인은 ticker 등호 + 세션 번호 범위(한쪽 테이블에만 걸리는 조인 술어 금지, HANDOFF §7-3).
- EG3_adj_factor 폐기형 추가: 어휘 닫힘 · `price_resolution='factor' ⇔ factor_ok` · 계수를 실은 행은 factor_ok=false·계수 1·`apply_basis='krx_base_price'`·허용 종류 · `price_only_factor` = 게이트 쪽 `_BASE_PRICE_CANDIDATES_CTE` 의 r(`FACTOR_PRODUCT_TOL`) · 단위당 계수 행 정확히 1(게이트가 단위를 다시 만들어 대조) · ok 접힘일의 계수 행 0 · 계수 행 아닌데 `price_only_factor ≠ 1` 0 · 공개일 재계산식(`:262-266`)에 계수 행 분기.
- 기록형: 사유·종류별 수, r 분포, 운반 행 없는 단위 수((c) 재발견과 DART 행이 같은 날이면 1 이상 가능 — 086830 2016-05-19), `unresolved` 사유별 수, 근처 판정 수. EG8: 계수 행 적용일 수정수익률 — 폐기형(10-09 재연 근거, D6-5: 운영 입력 D=20261008 최대 0.300·초과 0 → 같은 상수 1.0).
- G1(합성, 옛 코드는 열이 없어 FAIL): 207940/000880 형(사건 없음, 기준가 ×1.465, 주식수 ×1.2 → unknown_krx 정상 아님 → 계수 행 r = 1.465, 공개일 = 적용일) · 084010 형(같은 날 bonus 정상 아님 + unknown_price_only → 계수 행은 KRX 행 1개, bonus 는 `price_only_dup`) · 111610 형(성분 감자 2행 → 계수 행 1개, 값 = 그날 r) · 작은 권리락 r = 0.9923 적용.
- 회귀 가드: 005930 50:1 분할 ok 행 기존 열 전부 그대로·`factor`·1 · ok 접힘일과 겹친 unknown_price_only → `unresolved`·1 · (c) 재발견 → 행 없음 · 근거 없는 no_price_match → `unresolved` · fund → `unresolved`(D6-1) · 절단본 전체 ok 행 전 열 diff 0·행 수 같음.
- 부정 테스트(EG3 FAIL 해야): 계수 1/r · 단위에 계수 행 2개 · ok 접힘일에 계수 행 · 계수 행 factor_ok=true.
- 계약: `test_equity_s07_contract.py` EGC-04 가 247540 ⑤ 행이 있는 상태에서 `n_not_ok_emitted=0`·사건 집합 그대로. 정적 가드: `test_equity_s23_price_adj.py:589` 옆에 '두 어댑터 소스에 `price_only_factor` 없음'.

### 6-2 price_adj_daily·매크로 (같은 브랜치, 6-1 과 한 커밋 사슬)
- Files: `sql/price_adj_daily.sql`, `rules_s23.py`(선언 열, `input_columns` adj_factor +2, `install_recalc`, EG3 ①③④⑤, FieldProfile 문구 `:427-436`), `views.py`(`_FWD_CTE`·`v_adj_price`), `fixtures/price_adj_daily.json`, `tests/test_equity_s23_price_adj.py`·`test_equity_s06_views.py`.
- 매크로 출력 열은 그대로, 표만 새 열. EG3 ⑨ 가 기본 랙에서 두 산출이 같음을 증명.
- G1(`_synth_adj_close`): 207940 형 적용일 수정수익률 옛 +46.5%(FAIL) → close/base − 1(|·| < tol), 다음 세션 영향 없음, available ≠ date 행 0. 매크로를 안 고치면 EG3 ⑨ 가 FAIL 하는 것도 확인.
- 골든: fx2_015 두 칸 손계산 재정의(247540 05-09 계수 498,500/491,300 곱), 247540 2022-05-09 연속성 칸 신설(조정 수익률 481,000/491,300 − 1 = −2.10% 대 원수익률 −3.51%).
- 회귀 가드: 절단본 전체 `adj_volume_shr`·`cum_price_factor`·`cum_share_factor`·`n_factors_applied`·`n_unadjusted_events` diff 0, fx2_016 은 1 그대로, 005930 골든 그대로. 부정 테스트: 나눗셈↔곱셈 뒤집기 · 구간 첫 행 누적을 1로 끊지 않기.

### 6-3 fi (wip/b6-fi, 6-1·6-2 뒤)
- Files: `factor_inputs/queries.py:311-333`, `factor_inputs/build.py:45`(fi1.3.0), `model/contracts.py:79-89` 설명, `tests/test_factor_inputs.py:184-205`·`:892-903`, `test_model_v4_rank.py`, `test_deliver_excel.py`.
- G1: ⑤ 운반 행만 있는 종목 `adj_ok` 가 옛 코드에선 단위일부터 False(FAIL) → 새 코드 계속 True · v4 `_ret` 가 그 날을 넘으면 '수정주가미해결' → 값 · 주간 `_returns` 결측 → 값.
- 회귀 가드: `unresolved` 행은 여전히 계단 표식 · 창 밖 옛 사건 무시 · `adj_factor` 열 = adj_close ÷ close.

### 6-4 문서·판본
- `equity/model.py:28` e1.26.0 + 이력 주석(N-32 ②, 바뀐 열, EG5c 재기준). `EQUITY_FIELD_MAP.md:21` 가격 조정 정의(field_id 30 그대로, dataset_profile 72행). `EQUITY_GATES.md` §3-⑩·§3-㉓·EG3 목록. `EQUITY_DESIGN.md:108`·`:113-121`·`:316`. `EQUITY_HANDOFF.md` §11 조정가 읽는 법. DECISIONS N-32 진행. TECH_DEBT(범위 밖). 5-5 Q11 목록에 ⑤ 영향 줄.
- 워크벤치 `_specs.py` 설명 문구는 main 병합 때(지금 고치면 충돌만 는다).

## 서버 재연·격리 빌드 (6-5, 10-10~11 주말, 체인 밖)
- 입력: 첫 e1.25.0 확정판(10-09 08:10, D=10-08), 없으면 10-08 21:20 잠정판.
- ① 메모리 재연(읽기 전용, 4-1a 방식): 새 SQL 로 adj_factor·price_adj_daily 를 다시 계산해 현판과 diff — ok 행 전 열 변경 0 · not-ok 행은 새 열 2개와 계수 행 available_date 만 · price_adj_daily 는 ⑤ 단위 종목의 첫 단위일 이후 행만, 거래량 변경 0 · 단위·행 수가 로컬 사본 갈래와 같음.
- G1 대조(원본 v3 quant.db, `mode=ro`): 207940 2025-11-24 · 000880 2026-08-25 · 084010 2026-01-05(+ 001230 05-29 · 051360 07-27) 새 조정 일간 수익률과 v3 차이 < 0.5%p(옛 판 +46.5%·+40.9% 는 FAIL). 0단계 150건을 실제 구현 산출로 다시 대조 — 148/150 이상(09-14 이후 애프터마켓 2건은 알려진 예외). 2016 이후 미해결 창 |r| > 30% 행 329 → 15 안팎(추정).
- ② EG5c 예측: 표본 6종목의 `v_adj_price`·`v_adj_price_fwd` 차이 집합(as_of·ticker·date)을 미리 계산 — 배포 날 승인 근거.
- ③ 격리 빌드(`mktemp -d` 루트, 운영 판 무흔적): adj_factor·price_adj_daily 2회 content_hash 같음. fi·모델을 같은 D 로 지어 운영 판과 scope@1.0 순위 차분 — |Δ순위| 상위, 상위 30 집합 변화, 실점프 5종목 r1m~r12m 전후. 기대(추정): 000880·001230·051360·084010·207940 가 가장 크게, 기준가 변화 5~17% 종목 약 20이 작게, z 정규화로 나머지도 조금.
- ④ EG8 수치: 계수 행 max |수정수익률|(D6-5).

## 배포 (6-6)

| 날짜 | 할 일 |
|---|---|
| 10-08(목) | 플랜 보고 |
| 10-09(금, 휴장) | 묶음 5 배포. 묶음 6 은 로컬 구현·검토만(서버 쓰기 없음) |
| 10-10~11 | 6-5 |
| 10-12(월) 10:45~15:10 | 배포 — 조건: 묶음 5 첫 거래일 관측(06:00 원장 락 대기 · 08:10 확정판 · 10:30 워치독 발송 장부 확인) 정상 |
| 10-12 21:20 | 첫 e1.26.0 잠정판 |
| 10-13 08:10 | 첫 ⑤ 확정판 + 자동 발송(D=10-12) |
| 예비 | 10-13 낮 창 |

1. 체인·원장·빌드 잠금이 비었는지 확인, 되돌릴 rev(묶음 5 rev) 기록.
2. `deploy.sh --apply --allow-branch feat/v3-merge`(테스트 전량 통과 조건), 바뀐 파일 md5 대조.
3. `equity_rebuild_all.sh b6_pass1`(수동 판 b_, 약 10분, RSS 7.7GB) — 이 패스의 `before.json` 이 되돌릴 판 목록.
4. `equity catalog --rebase-asof --reason "N-32 ② e1.26.0 ⑤ — 표본 6종목 가격 축 보정"` — EG5c `diff_by_kind` 가 `changed` 뿐이고 차이 집합이 6-5 ② 예측과 같을 때만. 다르면 멈춘다(P1).
5. `equity contract --engine-src _engine` — EGC-04 not-ok 방출 0.
6. `b6_pass2` → 두 `summary.tsv` content_hash 29/29 같음(EG5a 는 전량 재빌드에서 inputs_changed 로 빠지므로 해시 비교, HANDOFF §7-7).
7. fi·모델을 임시 out-root 로 1회(발송 없음) — scope 순위가 6-5 ③ 과 같은 갈래.
- 되돌리기(판 보관 10개 ≈ 5거래일 안): 기록 rev 재배포 → `before.json` 판으로 29표 전부 `equity rollback`(일부만 옮기면 섞인다, C-01) → `catalog --rebase-asof`(되돌림 사유) → 이미 발송했다면 fi·모델 재빌드 후 `--resend` 정정판.

## 기존 연구 재현값이 바뀌는 범위
워크벤치 `price.adj_close`·compat `adj_close` 가 ⑤ 대상 1,234종목·2,432사건일(2016 이후 833·1,427)에서 바뀐다. 전방 조정이라 사건 전 값은 그대로, 사건일 이후 수준이 1/r 배, 일간 수익률은 사건일 하루만 점프 → 실제 등락. 사건일을 품은 창의 모멘텀·변동성·52주 고점 신호만 달라지고 백테스트 손익(원주가 bar + ok 계수)·거래량은 그대로. 5-5 의 '4-1 밖 67건'(유동 보통주 |r| > 40%)이 주 대상이라 가격 축 연구 차분 확인 순서를 다시 매긴다(N-27 ⑦·§8-11).

## main 병합(e1.27.0) 때 맞출 것
- `v_unfolded_event`(#220·#369) 술어 그대로 — ⑤ 는 factor_ok·factor_source 를 안 바꾸므로 #369 포트 보유 수량 조정은 계속 맞다.
- `v_adj_close` 가림 술어를 `price_resolution='unresolved'`(그날 기준가가 있는 행)로 좁힌다(지금 술어는 ⑤ 로 맞아진 적용일을 가린다).
- #369 포트가 직접 계산하는 기준가 비(직전 종가 ÷ 기준가) = `1/price_only_factor` — 한 곳에서 읽게 할지는 병합 때. 포트 수량 조정 대상은 krx_base_inconsistent 뿐이라 factor_source 필터는 남긴다.
- 충돌 예상: `views.py`, `rules_s23.py` FieldProfile 문구(main 은 field_scope='field_map'), `model.py` 판본 줄, 커널 어댑터 삭제에 걸린 정적 테스트 2개.

## 범위 밖 (TECH_DEBT 로)
1. ok 기준가 계수의 주식수비 사용 — 키움(기준가비 r)과 곱 허용치 0.01 안에서 최대 약 1%p 차이(208640 2026-06-25 0.66%p).
2. (c) 정지 뒤 재개 기준가 리셋 — 행·표식 없음, 2016 이후 132행, 1,000억 이상 035890 +106% · 002210 +85% · 298000 +41%(2:1 병합 추정). ⑤ 뒤 scope 에 남는 가격 불연속의 주원인(추정).
3. H-04 — 백테스트 어댑터가 미해결을 경고 없이 버린다(§8-11). ⑤ 는 보유 수량을 고치지 않는다. main #369 가 krx_base_inconsistent 수량 조정·경고를 가져온다.
4. 미검증 범위 — 우선주·리츠·외국기업·DR, 2025 이전 사건은 키움 대조 없음. fund·ship_fund·reit 는 D6-1 로 제외.
5. ok 접힘일과 겹친 단위 1건, 기준가 근거 없는 DART 행(최근 38~49)은 표식만. 09-14 이후 키움 종가는 애프터마켓 체결가라 하루치 v3 대조가 성립하지 않는다.

## 결정할 것

| ID | 갈림길 | 권고 | 대안 | 근거 |
|---|---|---|---|---|
| D6-1 | ⑤ 적용 종류 | 주식 계열(common·preferred·spac·foreign·dr)만. fund·ship_fund·reit 99행은 `unresolved` | ETF 뺀 전부 / 보통주·스팩만 | 펀드·리츠 기준가 변화는 분기마다 2~5% 하락(분배·배당락 추정) — 접으면 adj_close 가 분배 재투자 축이 되어 주식과 뜻이 갈린다. scope 유니버스 밖 |
| D6-2 | 날짜가 다른 DART 행(그날 기준가 없음, ±[5, 40] 세션 안 ⑤ 단위, 같은 창 (c) 후보 없음)을 `adj_ok` 미해결에서 뺄지 | 뺀다(`price_only_near`, 전체 383 · 최근 39) | 남긴다(보수) | 그 날짜엔 기준가 변화가 없어 가격 불연속이 아니고 실제 불연속인 ⑤ 단위는 고쳐졌다. (c) 가드로 숨은 점프를 막는다 |
| D6-3 | C-05 원안(ok 계수 근처·같은 날 억제 중복, 130+14행)도 같은 열로 정리할지 | 이번에 함께(`factor_near`) — N-26 4.3 을 바꾼다 | v4 묶음으로 미룸(N-26 4.3 그대로) | P4, 미루면 equity 판본 상향·전량 재빌드를 한 번 더 |
| D6-4 | 미해결 수 열 | `n_unadjusted_events` 뜻 유지 + `n_price_unresolved_events` 추가 | 기존 열 뜻을 가격 축으로 | 기존 열 뜻을 조용히 바꾸지 않는다(P1) |
| D6-5 | EG8-P02 를 계수 행에도 폐기형으로 | 6-5 재연에서 초과 0 이면 같은 상수(1.0)로 — **폐기형(10-09 재연 근거: 최대 0.300·초과 0, `PRICE_ONLY_JUMP_GATE = True`)** | 기록형만 | 재개일 물리 상한이 기준가의 50~200% |
| D6-6 | EG5c 표본 재기준 승인(`--rebase-asof`) | 이 플랜 승인과 함께, 실행은 배포 날 차이가 6-5 예측과 같을 때만 | 배포 날 따로 묻기 | 승인이 없으면 그날 저녁 카탈로그 실패 → 다음 날 자동 발송 없음 |
| D6-7 | 지난 1M 모델 판 재생성 | 첫 ⑤ 확정판 뒤 22거래일 fi·모델 판 재생성(N-17 방식, 약 5분) | 안 함(N-26 4.4 방식) | 실점프 5종목 모멘텀이 크게 바뀐다 — 안 하면 첫 ⑤ 엑셀의 Δ순위 1M·흐름선에 일회성 이동 |
| D6-8 | 배포일 | 10-12(월) 10:45~(묶음 5 첫 워치독 확인 뒤) | 10-13 | 묶음 5 와 겹치지 않고 원인이 갈린다 |
