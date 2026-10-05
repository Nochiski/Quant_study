# 원본 v3 체인 ↔ quant-ledger 합치기 다각도 검토 (2026-10-02)

> 다섯 관점(소비자 지도 · 데이터 원천 대체 · 모델 동등성·컷오버 게이트 · 일정·운영·자원 · 플랜 문서 비판)을
> 병렬 에이전트로 검토하고, 결론을 좌우하는 주장은 오케스트레이터가 코드·서버에서 직접 확인했다(**확인** 표시).
> 근거 경로: v3 = 서버 `~/kael-system-v3`(읽기 전용 스냅샷으로 검토), 우리 = `database/`.
> 이 문서는 [`../plans/2026-10-02-v3-merge-v2.md`](../plans/2026-10-02-v3-merge-v2.md) 의 근거다.

## 0. 한 줄 결론
엔진 계산은 맞다(09-28 실데이터 골든 29건 통과). 합치기를 막는 것은 계산이 아니라 **① 점수가 나오는 시각
② 유니버스 ③ 가격 기준 차이 ④ 감사에서 빠진 소비자 ⑤ 낡은 문서·게이트**다.

## 1. 정정된 사실
- 원본 v3 의 WISE 수집 범위는 **전 종목 약 2,534**(`daily_pipeline.py` `_current_stocks()` 전량, 시총 필터 없음 — **확인**).
  1,310 은 점수 유니버스(시총 ≥ 1,000억)다.
- 원본 v3 `daily_prices` OHLC 는 키움 ka10081 `upd_stkpc_tp=1` **수정주가**다(`clients/kiwoom/client.py:184` — **확인**).
- M3-a(아침 모델 자동화)는 플랜만 있고 코드(`scripts/model_daily.sh`)는 없다(**확인**).

## 2. 결함 (위험 큰 순)

### R-1 컷오버 뒤 v3 후단이 하루 묵은 데이터를 오늘 것으로 낸다 (silent stale)
- **상황**: 결정 D-8("모델은 아침 확정판만")대로 M4 컷오버(v3 `daily_all` 제거) 뒤. 점수는 D+1 ≈09:50 에 생긴다.
- **인풋**: ① D 저녁 `daily_post` 의 `export_scores`(인자 없음) ② D+1 07:00 `briefing --slot morning`.
- **에러 위치**: v3 `scripts/export_and_send.py:124`(`date.today()`), `:131-151`(D 행 0이면 파일·발송 없이 정상 종료 — **확인**) ·
  `backend/briefing/collectors/kr_market.py:165-174`(`MAX(trade_date)` 로 기준일 해석 → D-1, 점수는 `score_date = d` 정확 일치 — **확인**).
- **위험성**: 저녁 엑셀이 매일 0건인데 성공으로 기록되고, 07:00 브리핑은 D-1 장·점수를 D+1 머리글로 외부(네프콘 txt)에 낸다.
  플랜 §6 의 "M4 뒤 점수 22:50" 과 D-8 이 서로 모순이다.

### R-2 첫 제자리 upsert 가 증분이면 수정주가 레벨이 이어 붙는다 (silent corrupt)
- **상황**: compat `adj_close` = 전방 조정(`close × cum_share_factor`, `src/equity/sql/price_adj_daily.sql:127` — **확인**, 005930 은 실가 ×50),
  v3 `adj_close` = 키움 수정주가(최근일 = 실가).
- **인풋**: M4 첫 `compat_export.sh`, 대상 v3 quant.db, 기본 증분 14일(`src/compat/quant_db.py:50` — **확인**).
- **에러 위치**: `src/compat/mappings.py:47-52` 는 "비율만 쓰므로 같다" 고 보지만 창 전체가 한 원천일 때만 참.
- **위험성**: 경계를 넘는 수익률이 누적계수 배로 튄다. kael-wiki 가설 적중 판정·EW 벤치마크(`judge_returns.py`·`fwd_common.py`)가
  위키 원장에 영구 기록된다.

### R-3 소비자 감사 누락 — kael-wiki · unitelegram
- `scripts/compat_consumer_audit.py` 는 v3 트리만 훑는다. 위키 ingest(financial_summary·consensus_revision_daily·점수 상위 100·sector 집계)와
  uni `weekly_watchlist.py`·`weekly_holding_review.py` 가 빠졌다. `mappings.py` `retire_when` 은 financial_summary 소비자를 "scoring 뿐"으로
  적어 D-4 에 만료시키려 한다 → 위키 숫자가 마지막 값에 얼어붙는다.
- sector 를 WICS 로 바꾸면 위키 sector GROUP BY 에 가짜 섹터 로테이션. stock_name 정확 일치 소비자 3곳(브로커·뉴스 preview·위키 judge).

### R-4 점수 유니버스 절반 — 소비자 영향
- scope/v3 이식 587(D-10 추정치 보유) vs 원본 1,309~1,321, v2 619 vs 2,531.
- uni `kael_db.py` 는 종목별 최신 행을 날짜 검사 없이 쓴다 → 유니버스에서 빠진 종목의 옛 점수가 계속 '최근'으로 보인다.
  `weekly_holding_review.py` 90일 Δ 판정은 z 기준이 바뀐 두 시계열을 잇는다.

### R-5 병행기 키움 충돌과 실패 전파
- v3 는 키움을 20:05~20:47, ≈21:52~22:35(adj_prices), 22:37~(insight) 에 쓴다. 우리 21:05~21:20 은 시각만 맞춰 끼어 있고
  v3 상태를 보지 않는다(`scripts/daily_evening.sh:60-67`). 양쪽 모두 인증 오류 시 토큰을 강제 재발급한다
  (`src/api.py` 8005 재시도 · v3 `client.py:118-122` — **확인**; 활성 토큰 1개 제약은 문서 전제, 미실측).
- 우리 저녁 키움이 실패하면 다음 날 `kiwoom.ka10060.rows`(REQUIRED) FAIL → 확정판·엑셀이 함께 사라지고 자동 재수집 경로가 없다.

### R-6 무음 실패
- 10-01 텔레그램 로그 알림 차단(`scripts/notify.sh`) 뒤 워치독 crit 도 `logs/notify.log` 에만 남는다. 사용자 신호는 엑셀 도착뿐.

### R-7 모델 쪽 남은 차이
- 수정주가 미해결 사건 **3,562/5,594**(10-02 서버 `adj_factor.factor_ok=false`; 확정 2,032 — v1 플랜·초기 보고의 "1,724/5,733" 은 확정 수를 미해결로 거꾸로 적은 것), scope 유니버스 587 중 **34종목**의 창 안에 미해결 사건(fi `adj_ok=false`), F-6 131종목 → 모멘텀(.30)이 해당 종목에서 조용히 틀린다(DQ-1).
- 연도 전환기(1~3월): 유니버스 fy 기준(`build.py:236`)·리비전 결산기(`v3_zscore.py:169-173`)·퀄리티 WISE/DART FULL OUTER JOIN NULL
  (`queries.py:667-732`)이 동시에 바뀐다. 9~10월 대조(0.987)는 이 구간을 대표하지 않는다.
- v3 리비전 3개월 비교값 고착(v3 결함 — `consensus_revision_compare_repo.py:57-65`). 비교 도구가 `v3_defect` 로 분류해야 한다.
- `score_history` PK (stock_code, score_date) 에 모델 열이 없다 → 같은 날 v3 와 scope 가 둘 다 쓰면 섞인다(단일 쓰기 원칙 필요).
- scope 는 점수에 안 쓰는 `val_ev_ebitda` 원값을 계속 내보낸다 → `compare.py` 원인 분류 1순위를 오염.

### R-8 문서·게이트
- 플랜 §0 이 09-29 에 멈춤. "D-8" 이 두 뜻(GAP-1 선택지 / 모델 basis). D-5 는 현실과 다름(퀄리티 4칸이 DART).
- G-M5 ② `score_history ≥ 1,500 · v2 ≥ 2,000` 은 원본 v3 자신(1,309)도 못 넘는다. G-M2 재정의 "커버리지 ≥ 2,500" 도 도달 불가.
- G-M3 ① 0일(T2.7·T2.8 미구현). 모델 판 manifest 에 레지스트리 해시가 없고 factor_inputs `RULES_VERSION` 이 T-Q4 뒤에도 `fi1.0.0`.

## 3. 원본 v3 adj_prices (10-02 별건, 처리 완료 — 결과 확인 대기)
- 30분 타임아웃(09-22·09-29~10-01). 구조 원인: 20:05 시세 수집이 최근 5행을 `INSERT OR REPLACE` 하며 adj_close 를 NULL 로 덮어
  매일 전 종목(≈2,530 × 최대 3페이지)을 다시 받는다. 처리 순서 끝 1,955~2,560번째 약 600종목(KOSDAQ)이 09-18 이후 미갱신.
- 사용자 승인으로 v3 파일 3개 수정(타임아웃 3600초 · 경고 로깅 · 진행/빈 응답/완료 출력), 원본 `*.bak-20261002`.

## 4. 검토가 낸 권고 (플랜 v2 에 반영)
1. 점수 시각을 먼저 정한다 — (i) 저녁 잠정 모델 (ii) 후단을 아침으로(v3 수정) (iii) 하루 지연 수용.
2. `score_history` 단일 쓰기 · scope 판 G-M3 · 병행 10거래일 수치 게이트(종합 ≥ 0.98, 상위 30 ≥ 25, 상위 50 ≥ 43, D+1 10:00 전 생성 10/10, 행수 ±5%).
3. 첫 제자리 upsert 는 `--full` + 경계 연속성 게이트.
4. 소비자 감사를 서버 kael-wiki·unitelegram 원본까지.
5. 운영 가드: 21:05 키움 전 v3 상태 확인 · 아침 누락 보강 · KIS 06:55 마감 · 워치독 시각.
6. 추적성: 판 manifest 에 TOML sha256, fi 규칙 버전 상향.
7. 컷오버 뒤에도 되돌리기 창 유지(v3 입력은 날짜 축 없는 스냅샷 — 지난 날짜 재계산 불가).
