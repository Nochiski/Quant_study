# equity WISE 필드 — 컨센서스 매트릭스·연간 추정·재무비율 + 사모 순매수 (2026-10-22)

| 작업 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|
| T1 S26 표 3개(`consensus_matrix`·`consensus_annual`·`fin_ratio_wise`) | 10-22 | 10-22 | — | 10-23 판정·계보 정리 뒤 | — | 첫 아침 빌드 EG3_* |
| T2 사모 순매수 필드 선언(`flow.private_fund_net_buy`) | 10-22 | 10-22 | — | T1 과 함께 | — | dataset_profile 행 |
| T3 S19 등재(`SOURCE_TABLES` 26 → 29) | 10-22 | 10-22 | — | T1 과 함께 | — | dataset_profile 커버율 |
| T4 서버 임시 폴더 빌드(운영 체인 밖) | 10-22 | 10-22 | 10-22 16:28~16:31 — 3표 rc 0(8초·1초·11초, RSS 최대 3.2GB, 게이트 전부 통과) · dataset_profile 98행 rc 0(1분 33초) | — | — | — |
| T5 워크벤치 어댑터(main, #609 선언을 equity 표로) | 10-22 | 10-23 | 어댑터·계약 테스트 620 통과 | PR | — | — |
| T6 scope_v1.0 근사 YAML 재대조 | 10-22 | 10-23 | 31거래일 상위 10 평균 8.94·최저 8·ρ 0.993 — stage 참조판과 일별로 같다 | — | — | — |

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

### D1. 가용일 = 수집 시각으로 센 지식 세션, 랙 1 세션 (사용자 10-23 결정)

- **사용자 결정**(10-23): "데이터는 신선도 최대한 좋을 때 써야지. 당연히 최신값을 사용할수 있도록." — 처음 권고안
  (가용일 = 수집일, 06:00 수집분 하루 보수적)을 버렸다.
- **선택**: stage WISE blob 표 7개가 원장 수집 시각 `fetched_at_utc` 를 싣는다(stage 2.10.0 — 이 브랜치에서 2.9.0 으로 매겼다가 갈래 병합 때 다시 매김, 서버 갈래의 2.9.0 은 ka10060 거래량). equity 가용일 = 수집 시각의 UTC
  날짜(장 시작 09:00 KST = 00:00 UTC 경계) 이하 마지막 거래일('지식 세션', basis derived), 랙 1 세션. 장 전 수집분은 그날,
  장중·장 뒤 수집분은 다음 세션부터 쓴다. 시각이 없으면 수집일(measured, 보수), 달력 끝 뒤면 당기지 않는다.
- **날짜 경계를 박지 않은 이유**: 수집 시각이 운영 중에 06:00(09-03~11) → 18:05(09-14~10-15) → 15:52(10-16) → 11:33(10-19 손)
  → 06:00(10-20~)으로 바뀌어 왔다(원장 ws_raw 실측). 상수는 다음 변경 때 조용한 look-ahead 가 된다(P1·P4).
- **효과**(10-23 서버 임시 빌드): 매트릭스 411만 행 중 151만 행(37%)이 장 전 수집이라 하루 당겨졌다. EG3_* 가 세 `.sql` 의
  가용일을 독립 재계산으로 대조한다(불일치 0).

### D2. 비율의 이월은 선언으로

- `fin_ratio_wise` 는 접힌 판이라 워크벤치가 최신값으로 읽고, 다음 판이 올 때까지 값이 이어진다. 값은 결산기 말 주가 기준
  실적 비율이라 원래 해마다 바뀐다. WISE 가 종목을 더 보여 주지 않으면 옛 값이 남는다. 접힌 표에서는 "원문이 그대로라
  새 판이 없음"과 "더 보여 주지 않음"을 가를 수 없어 원장이 그 수를 세지 않는다 — 한도로 자르는 것은 소비 규칙이라
  워크벤치에서 정한다(#609 리뷰 P3).
- 매트릭스·연간 추정은 워크벤치가 지식 세션 격자로 읽는다. 종목별로는 잇지 않아 추정이 사라진 종목은 그날 빈 값이다.
  다만 어떤 세션에 판(수집분)이 **통째로** 없으면(수집 시각이 바뀐 날·수집 실패) 직전 판을 5 세션까지 잇는다 — 09-11(금)
  06:00 → 09-14(월) 18:05 전환에서 09-14 세션이 통째로 비던 것을 막는다(워크벤치 `SourceSpec.carry_sessions`, 운영 fi 유예
  상한 5 와 같은 크기).

## 테스트

- 재현: S26 표가 없는 지금 `RULES` 에 세 표가 없음을 확인(FAIL) → 구현 뒤 stage 절단본(5종목 · 09-01~02) 왕복 PASS.
- EG3_* 부정 픽스처(키 중복·어휘 밖 lookback·`fy_rank` 어긋남).
- S19: 절단본 체인에 세 표를 더해 dataset_profile 행 수와 새 필드 14개 확인.
- 운영 픽스처(`src/equity/fixtures/*.json`)는 서버 stage 실값(005930 2026-10-21 등).

## 배포

- 10-23 되돌리기 창 판정과 원장 계보 정리(로컬 전용 커밋의 원격 반영·main 병합) 뒤, 운영 체인 밖(10:30~15:10)에
  `deploy.sh --apply --allow-branch <브랜치>`. 그 전에는 서버 임시 폴더 빌드만 한다(T4).

## 열린 것

1. ~~stage WISE 표에 수집 시각을 실어 06:00 수집분을 당긴다~~ — 10-23 반영(D1).
2. fi·compat 를 equity 표로 옮기는 B-23 나머지.
3. **계보 병합 때(이 브랜치 + main #614)**: 워크벤치 어댑터가 S26 표를 읽으므로 `tests/test_equity_s21_workbench.py` 의 `ALL_FIELDS` 에
   WISE 13필드를 더하고 `LEDGER_PENDING_FIELDS` 를 비운다. backend `tests/equity_fixture.py` 의 `LEDGER_PENDING_FIELDS` 도 비운다
   — 비우지 않으면 `test_원장_대기_필드는_아직_원장_선언에_없다` 와 S21 의 폴백 등식이 실패해 알려 준다.

## 기록

- 10-23 T6: 로컬 원장 복제(10-21 판) + 서버 임시 빌드 4표(S26 3표·dataset_profile)로 카탈로그를 다시 짓고(임시 루트라
  EG5c 는 `--rebase-asof`), 워크벤치 어댑터(feat/workbench-wise-fields)로 근사 YAML(노드 595, 사모 필드만
  `flow.private_fund_net_buy` 로 이름 교체)을 돌렸다. 2026-09-01~10-19 scope 31거래일 상위 10 겹침이 stage 참조판(#609)과
  날마다 같다 — 사모 원천이 stage 키움 단독에서 flow_daily 합집합으로 바뀐 영향은 이 구간에 없다.

- 10-23 `fin_ratio_wise.sql` 숫자 상수 제거(8ca0d213)는 산출 불변이다 — 로컬 stage 사본 `stg_fin_wise` m_20261021T001728 판에서 옛 SQL(62645cb5)과
  새 SQL 결과가 5,606,964행으로 같고 EXCEPT ALL 양방향 0행, 열 타입도 같다.
- 10-23 워크벤치 PR #614 머지(01a721dc). 계보 병합 때 할 일은 '열린 것' 3.
- 10-23 D1 반영(stage 2.9.0 — 갈래 병합 뒤 2.10.0 + S26 가용일 = 수집 시각) 뒤 서버 임시 빌드(snap_20261022T232524Z): stage 3표·equity 3표·dataset_profile
  rc 0, 게이트 전부 통과. 재대조(워크벤치 판 잇기 5 포함): 31거래일 상위 10 평균 8.65·최저 5·ρ 0.987(이전 8.94·8·0.993).
  내려간 날은 장 전 수집 구간(09-02~10, 10-19~)이다 — scope_v1.0 은 그 구간에 하루 묵은 WISE 를 썼고 워크벤치는 이제 그날
  판을 쓴다(사용자 지시대로 신선한 쪽이 기준). 판 잇기 전에는 09-14 세션이 통째로 비어 그날 겹침이 0 이었다.
- DECISIONS 번호 N-55 는 이 계보에서 매긴 것이라 다른 계보와 병합할 때 번호를 확인한다.
