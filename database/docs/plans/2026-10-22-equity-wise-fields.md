# equity WISE 필드 — 컨센서스 매트릭스·연간 추정·재무비율 + 사모 순매수 (2026-10-22)

| 작업 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|
| T1 S26 표 3개(`consensus_matrix`·`consensus_annual`·`fin_ratio_wise`) | 10-22 | 10-22 | — | 10-23 판정·계보 정리 뒤 | — | 첫 아침 빌드 EG3_* |
| T2 사모 순매수 필드 선언(`flow.private_fund_net_buy`) | 10-22 | 10-22 | — | T1 과 함께 | — | dataset_profile 행 |
| T3 S19 등재(`SOURCE_TABLES` 26 → 29) | 10-22 | 10-22 | — | T1 과 함께 | — | dataset_profile 커버율 |
| T4 서버 임시 폴더 빌드(운영 체인 밖) | 10-22 | 10-22 | 10-22 16:28~16:31 — 3표 rc 0(8초·1초·11초, RSS 최대 3.2GB, 게이트 전부 통과) · dataset_profile 98행 rc 0(1분 33초) | — | — | — |
| T5 워크벤치 어댑터(main, #609 선언을 equity 표로) | 10-22 | — | — | PR | — | — |
| T6 scope_v1.0 근사 YAML 재대조 | 10-22 | — | — | — | — | 상위 10 겹침 ≥ 8 |

## 배경

- 사용자(10-22): "애쿼티 디비에 리비전 데이터는 필요하지 않나? 공개된 데이터잖아?" → WISE 리비전·PER·PBR·배당·키움 사모를
  equity 표·필드로 넣는다(#608 기록 issuecomment-6331208554, stage 참조안 #609 는 닫음).
- 워크벤치의 scope_v1.0 근사 YAML 이 쓰는 필드 24개 중 14개가 equity 에 없다 — WISE 13(영업이익·순이익 컨센서스
  현재·1주·1개월·3개월 전 8, FY0 추정 2, PER·PBR·배당수익률 3)과 사모 순매수 1.
- TECH_DEBT B-23: equity `consensus_daily` 의 WISE 축은 eps·매출(cF5001/5002)뿐이라 fi(`queries.py`)와 compat
  (`mappings.py`)이 '한시 예외'로 stage 를 직접 읽는다. 이 작업이 그 equity 쪽 첫걸음이다(fi·compat 전환은 범위 밖).

## 범위

- 넣는 것: stage 3표를 equity 표로(T1), 사모 열의 필드 선언(T2), dataset_profile 등재(T3), 워크벤치 어댑터(T5).
- 넣지 않는 것: fi·compat 의 읽기 전환(B-23 나머지), stage 변경, WISE 수집 시각 변경, 과거 백필(WISE 3표는 2026-09-01 부터다).

## 설계

### 표 3개 (S26, `rules_s26.py`)

| 표 | grain | 원천 | 하는 일 |
|---|---|---|---|
| `consensus_matrix` | (ticker, fetched_date, target_period, acc_cd, lookback) | `stg_consensus_matrix` | stage 그대로 + `fy_rank`(수집 달 이후로 끝나는 결산기의 가까운 순서, 지난 기는 NULL) |
| `consensus_annual` | (ticker, fetched_date, period_label) | `stg_consensus_annual` | stage 그대로 |
| `fin_ratio_wise` | (ticker, fetched_date, seq, slot) | `stg_fin_wise` 중 `ep='cF4002'` | 연간 열 6개(`period_label_i`·`val_i`)를 행으로 펴고 `period`(YYYYMM)·`is_estimate` 를 붙인다 |

- `fy_rank` 는 compat(`mappings.py` `per`)·fi 엔진(`v3_zscore._consensus_pair`)·#609 가 각자 쓰던 "수집 달 이후 가장 가까운 기"
  규칙을 표에 한 번 적은 것이다(P4).
- `fin_ratio_wise` 는 stage 의 연속 판 접기(fold, stage 2.7.0)를 그대로 잇는다 — `fetched_date` = 그 원문을 처음 본 날.

### 필드 (워크벤치가 읽는 id 는 #609 이름 그대로)

| field_id | 표 · 행 | 빈도 | 랙 |
|---|---|---|---|
| `wise.consensus_op`·`_1w_ago`·`_1m_ago`·`_3m_ago`, `wise.consensus_ni`·… (8) | `consensus_matrix` acc_cd 121500·122710 × lookback, `fy_rank = 1` | session | 1 |
| `wise.fy0_op_estimate`·`wise.fy0_ni_estimate` (2) | `consensus_annual` `period_kind='E'`, period = 수집 연도 12월 | session | 1 |
| `wise.per`·`wise.pbr`·`wise.dividend_yield` (3) | `fin_ratio_wise` accode 382100·382500·431800, 추정 아닌 마지막 12월 | report | 1 |
| `flow.private_fund_net_buy` (1) | `flow_daily.samo_fund_krw`(키움 ∪ KIS 사모, 다른 순매수와 같은 합집합) | session | 1 |

YAML 에서 이름이 바뀌는 것은 사모 하나다(`kiwoom.private_fund_net_buy` → `flow.private_fund_net_buy`).

## 결정

### D1. 가용일 = 수집일, 랙 1 세션 (권고 · 사용자 확인 대기)

- **선택**: `available_date = fetched_date`(basis measured), `recommended_lag_sessions = 1`. 기존 `consensus_daily`·`opinion_daily`
  WISE 축과 같은 선언이다.
- **대안**: 행마다 '처음 쓸 수 있는 세션'(06:00 수집분은 그날, 18:05 수집분은 다음 세션)을 가용일로 두고 랙 0.
- **이유**: stage 에는 수집 **날짜**만 있고 시각이 없다(`observed_src='fetched_at'` 이 날짜로만 남는다). 대안은 "10-20 부터 06:00"
  같은 날짜 경계를 코드에 박아야 하고, 수집 시각을 다시 옮기면 그 상수를 놓친 순간 조용한 look-ahead 가 된다(P1).
  선택안의 대가: 06:00 수집분(10-20~)은 하루 늦게 쓴다(내용은 전 거래일 것인데 다음다음 세션에 보인다). 당기려면 stage 가
  수집 시각을 실어야 한다 — 후속(열린 것 1).

### D2. 비율의 이월은 선언으로

- `fin_ratio_wise` 는 접힌 판이라 워크벤치가 최신값으로 읽고, 다음 판이 올 때까지 값이 이어진다. 값은 결산기 말 주가 기준
  실적 비율이라 원래 해마다 바뀐다. WISE 가 종목을 더 보여 주지 않으면 옛 값이 남는다. 접힌 표에서는 "원문이 그대로라
  새 판이 없음"과 "더 보여 주지 않음"을 가를 수 없어 원장이 그 수를 세지 않는다 — 한도로 자르는 것은 소비 규칙이라
  워크벤치에서 정한다(#609 리뷰 P3).
- 매트릭스·연간 추정은 워크벤치가 격자(그날 수집분)로 읽어 이월이 없다 — 추정이 사라진 종목은 그날 빈 값이다.

## 테스트

- 재현: S26 표가 없는 지금 `RULES` 에 세 표가 없음을 확인(FAIL) → 구현 뒤 stage 절단본(5종목 · 09-01~02) 왕복 PASS.
- EG3_* 부정 픽스처(키 중복·어휘 밖 lookback·`fy_rank` 어긋남).
- S19: 절단본 체인에 세 표를 더해 dataset_profile 행 수와 새 필드 14개 확인.
- 운영 픽스처(`src/equity/fixtures/*.json`)는 서버 stage 실값(005930 2026-10-21 등).

## 배포

- 10-23 되돌리기 창 판정과 원장 계보 정리(로컬 전용 커밋의 원격 반영·main 병합) 뒤, 운영 체인 밖(10:30~15:10)에
  `deploy.sh --apply --allow-branch <브랜치>`. 그 전에는 서버 임시 폴더 빌드만 한다(T4).

## 열린 것

1. stage WISE 표에 수집 시각(`fetched_at`)을 실으면 D1 대안(행마다 처음 쓸 세션)으로 06:00 수집분을 하루 당길 수 있다.
2. fi·compat 를 equity 표로 옮기는 B-23 나머지.
