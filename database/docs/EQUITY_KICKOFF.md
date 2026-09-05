# Equity 층 착수 노트 (2026-09-03, stage 완료 직후 인계)

> stage 세션이 equity 세션에 넘기는 시작 문서. 읽는 순서: 이 문서 → `STAGE_HANDOFF.md`(읽기 계약·테이블별 메모) →
> `STAGE_SPEC.md` §3(깨진 주장)·§4(확정 한계)·§5(미결 D3~D6) → ERD v0.1 아티팩트(Equity.db ERD, 2026-09-01).
> stage 실측의 근거는 `STAGE_DESIGN.md` §10.

## 0. 현재 상태
- stage: 62테이블(원장 61 + `stg_analyst_broker`) 서버 풀 빌드 2회 통과, 재현성 확인. 산출 `~/quant-ledger/data/stage/<table>/MANIFEST.json`,
  회귀 기준 `data/stage/baseline.json`. 코드 `database/src/stage/`, 테스트 194, PR #14~#34, main 이 정본.
- equity 산출은 `~/quant-ledger/data/equity/` 로 분리한다. stage 는 읽기 전용(MANIFEST 경유, 맨 glob 금지).
- 워크플로우는 stage 와 동일: 설계 1회 적대적 검수 → 슬라이스별 짧은 브랜치·TDD(손계산 픽스처) → 서버 실측(읽기 전용 스크립트는 scp 후 `.venv/bin/python`) → 문서 §기록 → PR self-merge → 브랜치 삭제. 배포는 rsync.

## 1. 0단계 — 설계 확정 (코드 0)
`EQUITY_DESIGN v1` 작성 + 검수 1회. 결정할 것:
1. 산출 형식 — `equity.duckdb` 파일 하나(권장: 조인·집계 층, 엔진이 SQL 로 뷰 4종 호출) vs parquet 세트.
2. 판본·게이트 규약 — stage 의 MANIFEST/`_meta`/G-게이트/baseline 을 그대로 이어받을지.
3. 팩터 ID 정본(SPEC D5: `FACTORS.md` vs `DATA_CATALOG`).
4. `universe_daily` 3층 임계(SPEC D6).

### ERD v0.1 → v0.2 개정 목록 (stage 실측 반영)
| v0.1 | 실측 | 개정 |
|---|---|---|
| `security` 상장일·폐지일 1개 | 재상장 실증 2종 — 한 티커에 상장 구간 복수 | `security_span`(ticker × 구간) 분리 |
| `universe_daily.status` listed/suspended/delisted | 2026-08-21~백필일은 어떤 원장으로도 재구성 불가(§8) | `coverage_gap` 상태 추가 — 08-20 연장 금지 |
| `credit_daily.available_date` = 결제일 | `stlm_date` 는 결제일이지 공개일 아님(§6) | available = 거래일(default), 랙은 dataset_profile |
| `consensus_daily` PK (ticker, obs_date, target_period, metric), available=수집일 | WISE 는 13개월 이력을 매일 재작성 — 판본 축 = 관측점 × 수집일, PIT = 같은 관측점의 min(fetched_date) 행(§7) | grain (ticker, obs_month, target_period, metric) + `available_date = first_seen_fetched_date` + `unit`(EPS 원·매출 억원) |
| `fin_std.is_restated` 만 | 정정 신호는 equity 파생(정기보고서 그룹 11.5% 정정, 30%는 90일 뒤) | `disclosure_version`(원본↔정정, corrected_at) 신설, `fin_std.has_correction` |
| 없음 | `stg_analyst_broker`(증권사별 목표가·의견, 09-03 편입) | `opinion_broker_daily` 추가. `opinion_daily` 원천 = `stg_analyst_summary` |
| 원천 이름이 가상 | 실명: `stg_flow_daily_kiwoom`+`stg_flow_split_daily`(KIS, 폐지 커버) · `stg_foreign_daily` · `stg_short_daily_kiwoom/kis` · `stg_lending_daily` · `stg_loan_daily_kis` · `stg_v3_analyst_opinions` · `stg_event_*` | 표 갱신. `flow_daily` 도 키움+KIS 상보 결합에 `src` 딱지. `universe_daily` 원천에 `stg_delisted_master` 추가 |
| 미결: OHLCV 당일 vs 익일 | stage 확정 available=date(default, lag_known) — 시총·주식수 익일 지식은 dataset_profile `column_scope` | 미결 해소 |
| 미결: admin_flag 과거 없음 | `stg_master_daily` coverage_from=2026-09-01 (`_meta.json`) | 그대로 |
원칙 6개(PIT 게이트·원주가 불변+조정계수·corp/ticker 이축·결측은 결측·지식은 카탈로그·뷰 4종)와 basis 어휘(measured/convention/default)는 유지.

## 2. 1~6단계 (의존 순서)
| 단계 | 산출 | stage 입력 | 판단·게이트 |
|---|---|---|---|
| 1 종목마스터·유니버스 | `security`·`security_span`·`corp_ticker`(corp_code↔ticker, 우선주 1:N, ISIN8)·`universe_asof(D)` | listing(pit)·master_daily(현재)·delisted_master(폐지일)·corp_map·company·price | 코넥스 제외, 재상장 구간, `coverage_gap`. `src/build_bridge.py` corp_ticker·isin8 재사용 |
| 2 가격 정본·조정계수 | `price_daily`(KRX 원주가) + `adj_factor` 2종(분할·병합 / 배당·유증) + `price_matches_krx` | price·etf·listing(PARVAL·LIST_SHRS)·credit(수정종가 대조) | SPEC §3-1·3-2 계수는 하나가 아니다. 분할일 수익률 점프 0, 교차 일치율 |
| 3 격자·결측 3분류 | 캘린더×유니버스 격자, `fill_kind`(src_omitted→0+플래그 / 결측 / 미수집) | 수급·공매도·대차·외인 + units·shards·calls | SPEC §5-3 레짐 편향 장치. 공매도 0일 행 생략 |
| 4 재무 PIT | `fin_std`·`fin_asof(D)`·`disclosure_version` | fin·rcept_dt_map·corp_ticker·company(acc_mt) | `src/finalize.py asof(year, acc_mt)` 재사용, 비12월 105사, restated_unknown |
| 5 컨센서스 PIT | 월간(min fetched_date)·증권사별(보고서 간격 복원)·v3 과거분(2026-04-03~09-02) 이어붙이기 | consensus_*·analyst_*·v3_* | 최신행 선택 금지(리비전 방향 = 알파), `coverage_degraded`, 증권사 `change_pct` 는 간격 없이 해석 금지(상상인 사례) |
| 6 공개시점 대장 | `dataset_profile`(테이블×컬럼군: 내용일 뜻·공개 시점·basis·권장 랙·근거) | HANDOFF `lag_known`·DESIGN §6 | 엔진은 `available_date ≤ asof − lag` 만 본다 |
병렬: 0단계 후 1·2·3 독립(에이전트 3), 4·5 는 1(corp_ticker) 의존, 6 마지막. 마무리 = baseline·재현성·실측 기록·팩터층 인계.

## 3. 첫 세션에서 바로 할 일
1. `git fetch && git checkout -b equity/design origin/main` — main 에 stage 전부 있음.
2. 위 읽기 순서대로 읽고 `docs/EQUITY_DESIGN.md` v1 + ERD v0.2 초안 → 0단계 결정 4개 브리핑 → 승인.
3. 1단계 종목마스터부터 TDD. 서버 실측은 `data/stage/` 읽기 전용.

## 4. stage 쪽 남은 후속 (equity 와 무관)
- `stg_fin` 스필 15~18GB 최적화(일일 증분 설계 때) · doc_index 는 수집 종료(09-03) 후 174,309행으로 재빌드 완료 · 통합 종목마스터 DB 는 1단계에서 자연 해소.
