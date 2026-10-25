# 열 지향 tape가 보존할 의미 목록

[설계 spec](../../superpowers/specs/2026-10-20-columnar-tape-design.md) 4절이 가리키는 목록이다. 경로는 따로 적지 않으면
`backend/src/strategy_workbench/` 기준이고, 줄 번호는 2026-10-20 main(`51b0bf96d`) 기준이다. 대조 하네스(spec 5절)는 각 항목을
말뭉치 사례로 덮는다. 항목을 고치는 PR은 이 파일과 하네스 사례를 함께 고친다.

## 1. 원시 관측 포트 계약 (`application/portfolio_design/ports/outgoing/raw_observations.py`)

| 규칙 | 위치 | 열에서 지킬 점 |
|---|---|---|
| `available_date <= as_of`. 늦은 값은 빼고 셀을 지어내지 않는다 | 9-13. 소비 경계는 `_service.py:934-940`(`LookAheadViolationError`) | 열 비교. 첫 위반을 (행 순서, 그 관측의 `fields` 튜플 순서)로 골라 같은 메시지 |
| 생략(셀 없음)과 값 None(셀 있음·값 없음)은 다르다 | 12-14 | `present` 플래그를 따로 둔다. 팩터 값 공개일(6절)과 trace 원시 투영이 이 구분을 쓴다 |
| OBSERVED·SOURCE_OMITTED_ZERO는 값 필수, 나머지는 값 없음 | 141-153 | `kind`(int8)와 값 유무의 정합을 열로 검사 |
| 세션 오름차순·유일, history는 모두 sessions보다 앞 | 211-224 | 같다 |
| 관측은 (as_of, security_id) 엄격 증가 | 227-235 | 정렬 코드 열의 인접 비교 |
| 선언되지 않은 날짜 금지(창은 날짜가 아니라 행 위치를 센다, D-003) | 236-253 | 행 날짜 코드가 선언 세션 범위 안 |
| `previous_weight`와 숫자 값은 유한 | 255-284 | `np.isfinite`, 첫 위반 메시지 맞춤. 검증은 세 번 훑는다(정렬 → 미선언 날짜 → 행별 previous_weight·필드). 첫 위반은 이 순서로 정한다. 셀 생성 시점 검사(빈 field_id, 종류와 값 유무)는 객체에서 집합보다 먼저 일어나므로 열 검증도 맨 앞에서 한다 |
| 값 타입은 정확히 float·str·None. bool·int는 위반(T2-01에서 bool, T2-02에서 int를 좁힘) | 52, 행별 필드 검사 | 열은 숫자 `value`와 문자 `text` 코드만 담아 bool 칸이 없다. `from_observation_set`이 bool·int를 같은 메시지로 거절한다 |
| 관측의 `fields`는 질의 `field_ids` 순서의 부분열이다(T2-02) | 계약 docstring. 집합은 질의를 몰라 생성 시 검사하지 않는다 | 열 순서가 곧 질의 순서다. `from_observation_set(raw, field_ids=...)`가 어긋난 관측을 계약 위반으로 거절하고, mock·duckdb는 계약 테스트가 고정한다 |
| 실패는 `status`/`detail` 값 | 29-30 | 같다 |
| 취소·진행은 선택 능력, 콜백은 스레드 안전 | 31-39, 288-322 | 열 포트도 같은 방식으로 능력 협상 |
| `universe_member`·`sector_id`는 공개일 없음. as_of 시점 값은 어댑터 책임 | 18-28 | 같다 |

## 2. 파이프라인 관문 순서 (`application/portfolio_design/_service.py:307-550`)

예외 종류마다 HTTP 코드가 다르다. 순서를 바꾸지 않는다.

1. `_prepare`(569-599): 문서 검증 → 실행 설정 → 파라미터 해소 → 엔진 판정 → trace 능력 → plan 컴파일 → 숫자가 아닌 출력 거절
2. 원시 질의(341-351). `history_sessions_before_start = max(minimum_history_sessions - 1)`
3. 소비 경계 재검증(371-377)
4. `ok`(381) → 스냅숏 일치(383)
5. 구간 밖 세션(388, 903-928)
6. 리밸런싱 일정(389) → trace 기본 날짜(390) → trace 범위(391-396, 739-827). 행이 없는 (날짜, 종목) 쌍은 거절한다
7. 팩터 입력 변환과 look-ahead(399-409)
8. 평가. `NonFinite`는 `strategy.expression.calculation_non_finite`(463-471)
9. 포트폴리오 관측(483) → 컴파일(499-527)
10. 판본 유일성(530-535)

## 3. 평가 공통 규칙 (`domain/factor/_evaluation.py`)

- **행 공간과 동료 집단**
  - 출력은 입력 관측 순서(189-204)다.
  - 시계열은 종목별 **행 위치**로 센다(`_indices_by_security` 791-801, `_history.py:17-28`).
  - 횡단면·그룹 동료는 `(as_of, universe_member[, 그룹])`(`_peer_indices` 763-788)이다. 그룹 값이 문자열이 아닌 행은 어느 그룹에도 들지 않는다(784-785).
  - 그룹 값은 같은 필드의 **첫** 셀이다(`next(...)`, 780-783). 숫자 필드 값은 마지막 셀이다(`_field_values`의 dict, 537). 열 패널(`FactorPanel.from_observations`)도 둘을 따로 읽는다(T3-05).
- **값 타입**
  - float·bool·None 세 가지다.
  - 필드 원값 중 bool·str은 숫자 입력에서 None이다(`_field_values` 541-545). `_as_number`도 bool을 숫자로 보지 않는다(817-822).
  - 노드 출력 타입은 검증기가 하나로 고정한다(`_validation.py:492-536`).
- **가림(MASKED)**
  - 가린 셀은 값이 없다(76-83).
  - 값의 시점 구간은 `_value_spans`(737-760)가 정한다. lag는 옮기고, 창은 넓히고, 섞는 연산은 두 구간을 덮는다.
  - 구간에 가린 칸이 들면 결측이다(`_masked_in` 310-320).
  - 판정은 값 계산보다 먼저다. 시계열은 그 창을 읽지 않는다(375-378, 628). 나머지 노드는 계산 뒤 None으로 덮는다(448-449).
  - 노드마다 가린 칸 집합을 보존한다(459).
- **결측 채움(`_filled` 322-356)**
  - 정책은 `zero`·`cross_sectional_median`(`_nodes.py:90`)이다.
  - 자리는 셋이다: 횡단면 노드 입력(435), 그룹 노드 입력(442, 그룹 동료), 그래프 출력(452-453).
  - 동료에 값이 없으면 채우지 않는다(350-351). 가린 칸은 채우지 않는다(354).
  - 중앙값은 `statistics.median`(352)이다.
- **유한값 검사**
  - 채운 뒤 노드마다 한다(454-456).
  - 노드 평가 순서(재귀 DFS)에서 처음 걸린 노드, 그 안의 가장 작은 행에서 `NonFiniteFactorCalculationError(node_id, as_of, security_id, value)`를 낸다(507-521).
- **출력**: 숫자만 float로 바꾸고, bool·None은 None이다(198-200).

## 4. 연산자 (`_nodes.py:41-90`, `_evaluation.py`, `_statistics.py`)

| 노드·연산자 | 규칙 |
|---|---|
| field | 원값 float. 가린 셀 위치를 경계로 등록(380-384) |
| constant / parameter | 모든 행 같은 값. 미해소·str·bool 파라미터는 `ValueError`(387-399). 상수는 hydrate가 float로 바꾼다(`domain/strategy/_hydrate.py:517-526`) |
| unary.negate | `-x`(−0.0 보존, 594-597) |
| unary.lag | `node_read_window`(periods, periods)로 한 칸 옮김. 시작이 음수면 None(720-734) |
| binary add/sub/mul/div | IEEE 한 번. 0 나누기(−0.0 포함)는 None(564). 넘침 inf는 유한값 검사가 잡는다 |
| comparison gt/gte/lt/lte/eq | 숫자 아니면 None, 결과 bool(567-584) |
| conditional | predicate `is True` → 참, `is False` → 거짓, 그 밖 None(410-416) |
| fill (#608) | None 칸 → `float(fill_value)`, 숫자 칸 그대로, 그 밖(검증이 막는 bool 입력) None(618-634). 워밍업 칸도 채운다. 가린 칸과 구간이 가린 칸에 닿는 칸은 다른 노드처럼 뒤에서 다시 None(같은 자리 연산이라 입력 구간 그대로) |
| time_series 공통 | 창 [p−(lag+window−1), p−lag], **정확히 window칸 모두 숫자**일 때만 값(630-633). 시작이 음수면 None |
| momentum / delta | `last/first − 1`(first == 0이면 None), `last − first`(636-641) |
| mean / std | `statistics.mean`, `pstdev`(643-646) |
| min / max | Python `min`/`max`. 같으면 앞 원소(647-650) |
| cs.rank | `(평균순위−1)/max(n−1,1)`. 키 (값, 입력 순서), 동점 `==`(`_statistics.py:7-18`, 39-51) |
| cs.zscore | `(v−mean)/pstdev`. 편차 0이면 모두 0.0(21-36) |
| cs.winsorize | `quantile`(54-61) 선형 보간 + `min(max(v,lo),hi)`(685-690) |
| cs.demean | `v − mean`(673-676) |
| group.neutralize / rank | 그룹 안 demean / rank. 빈 그룹 중심값 0.0(709-716) |

## 5. 파라미터·plan·판본

- 파라미터 해소: `_resolve_pipeline_parameters`(`_service.py:121-139`) → `ResolvedFactorParameter`(410-413).
- `plan_hash` payload: 결측 정책, `evaluation_version`, `missing_fill`, `as_of_policy`(`_planning.py:108-122`). 구현 식별 칸은 없다.
- `FACTOR_EVALUATION_VERSION = "factor-evaluation-v1"`(`_planning.py:24`), 대표 digest는 `tests/domain/test_factor_evaluation_version.py:34-37`.

## 6. 포트폴리오 관측 변환 (`_service.py:954-1029`)

- 측정 구간 행만 넣는다(history 제외, 963, 1010).
- 팩터 값은 (factor_id, as_of, security_id)로 찾는다.
- 팩터 값의 공개일(`_latest_input_publication` 1014-1029)은 같은 행의 **present** 셀 가운데 `plan.required_field_ids`(그룹 필드 포함, `_validation.py:133-141`)의 `available_date` 최댓값이다. 해당 셀이 없으면 `as_of`다(SoT 대장 55행).
- 필드는 kind를 버리고 (id, value, available_date)만 옮긴다(996-999).
- `starting_holdings`가 있으면 모든 행의 previous_weight를 대체하고, 없는 종목은 0.0이다(973-1005).

## 7. 컴파일러 (`domain/portfolio/_compiler.py`, `_scheduled_compiler.py`)

- **일정** `_rebalance_pairs`(379-409)
  - `every_n`은 `(i+1) % n == 0`이다. 주별(ISO 연·주), 월별, 분기별은 그룹의 마지막 세션을 고르고, 체결은 다음 세션이다.
  - 일정 객체와 프레임 수 대조는 117-151, 230-235다.
- **fold**: 첫 프레임만 관측의 `previous_weight`를 쓰고, 이후는 직전 targets 비중을 쓴다(256, 264-271, 314-316. SoT 61행).
- **1차 판정 `_score_candidate`(826-1016)**
  - 사유 순서(해시에 들어감): NOT_IN_UNIVERSE → 절대 규칙(규칙 순서마다 MISSING_ELIGIBILITY / FUTURE_DATA / ELIGIBILITY_FAILED) → LIQUIDITY_FAILED → REGIME_BLOCKED → 합성 팩터 순서마다 MISSING_FACTOR / FUTURE_DATA → SCORE_THRESHOLD. 중복은 `dict.fromkeys`로 첫 등장만 남긴다.
  - 점수는 `(dir × w) × signal`을 팩터 순서대로 누적한다. 결측 팩터는 분모에서도 빠진다. 그래서 일부 결측 후보와 비구성원 후보에도 점수가 생기고, 그 점수도 해시에 들어간다.
  - 비유한 stage는 `factor_input`, `composite_score`, `composite_denominator`, `factor_contribution`이다. `_number`(1571-1576)는 bool과 비유한값을 거부한다.
- **정규화 `_cross_sectional_signals`(766-823)**
  - 모집단은 (팩터, 구성원 여부)마다 따로 잡는다.
  - None·숫자 아님·늦은 공개 값은 뺀다. 역가중·보조 순위 팩터는 정규화하지 않는다(`composite_factors`, `domain/strategy/_models.py:360-366`).
  - 공식은 `cross_sectional_rank`·`zscore`를 공유한다(SoT 56행).
- **유동성·레짐**: 유동성·레짐에서 떨어진 종목도 2차 횡단면 모집단에는 남는다(414-416). long_short의 `score_threshold`는 `abs` 비교다.
- **2차 횡단면 적격 `_apply_cross_sectional_eligibility`(679-749)**
  - 규칙마다 모집단을 따로 잡는다(구성원, 절대 규칙 통과, 값 공개).
  - `(−값, security_id)`로 정렬한 뒤 `floor(Decimal(n) × Decimal(str(v)))`(1541-1568)개를 남기고, 컷 아래에 ELIGIBILITY_RANK_CUT을 단다.
  - `_with_exclusions`(752-763)가 `eligible`을 다시 판정한다.
- **순위 `_rank_candidates`(438-473)**: security_id로 정렬한 뒤(숏은 역순) 안정 정렬로 `(±점수 또는 0.0, 보조 결측, ±보조값)`을 건다. `_short_ranking`(476-489)은 보조 순위가 있으면 롱 선정을 뺀다.
- **선정 수** `_selection_counts`(1071-1078): percentile이면 `max(1, ceil(n×p))`.
- **비중 단계**
  - 버퍼 `_apply_turnover_buffer`(1081-1117)
  - 가중 `_weight_scores`(1222-1258)·`_margin_strengths`(1261-1313)
  - 무제약 배분 `_proportional_allocate`(1316-1339)
  - 상한 배분 `_capped_allocate`(1342-1391). 남은 종목은 `scores` 순서를 따른다(T0-02). 반복 정지는 남은 예산 `1e-15` 이하
  - 최소 거래(1176-1191): `sign_compatible`·`within_name_cap` 조건
  - 섹터 `_apply_sector_constraints`(1406-1451, 허용 오차 `1e-12` 1395, 모르는 섹터 제외). 섹터 중립은 `LONG_SHORT`일 때만 켜진다
  - 측별 예산 `_apply_side_budgets`(1203-1219): 롱 `(gross+net)/2`, 숏 `max((gross−net)/2, 0)`(1129-1138)
  - `_finalize_decision`(1454-1488): TURNOVER_BUFFER·OUTSIDE_SELECTION·MISSING_RISK·MINIMUM_TRADE 순서
  - `_risk_value`·`_field`(1491-1513)
- **targets·candidates**: targets는 security_id 순, `selected and weight != 0`(584-596). candidates는 프레임의 **모든** 관측을 security_id 순으로(597).
- **추적 산출물**: 요약 `_frame_summary`(625-648), 후보 추적 `_candidate_trace`(1019-1068, `math.isclose` 1035-1040), 경고 `_sector_unknown_warnings`(352-376).
- **해시**
  - `_canonical_payload`(1620-1646) → `JSONEncoder(sort_keys, (",",":"), allow_nan=False)`의 `iterencode`를 sha256으로 받는다(1649-1661).
  - payload 키는 data_snapshot_id, strategy_hash, execution_timing, frames다(328-334). date는 isoformat, Enum은 값이다(1664-1669).
- **연속 일정 컴파일러**(`_scheduled_compiler.py:186-349`): 같은 `_compile_frame`(283-290)을 쓰고, 해시는 `canonical_json_hash`(329-346)다.

## 8. 소비자별 중간 결과

| 소비자 | 읽는 것 |
|---|---|
| 백테스트 run(`application/backtest_run/_service.py:1172-1179`) | `.preview`(tape, warnings) |
| `/portfolio/preview`(`adapters/inbound/http_api/_app.py:686`) | `PortfolioPreview` 전체(모든 프레임 candidates) |
| `/strategies/debug/trace`(`application/portfolio_design/_trace_service.py`) | `factor_evaluations[f].trace`·plan(178-182, 270-273), 기준일 `values`(240-249), 기준일·요청 종목의 `raw_observations` 투영(214-219, 336-371), 기준일 구성원(234-239), 기준일 targets·candidates(374-421), `construction_trace`, warnings |
| S03 신호 연구(`adapters/outbound/signal_pipeline/_adapter.py:19-47`, `application/signal_research/_service.py:112-119`) | 모든 날짜 `observations`, 모든 행 `factor_evaluations.values`(정합 검사), `raw_observations` 키, tape |
| 연속 계좌(`adapters/outbound/continuous_pipeline/_adapter.py:217-238`) | 구간 모든 날짜 `observations`·plan(`ScheduledCandidate`). 강제 전환 프레임이 어느 날이든 생길 수 있다 |
| factor-regression(`backtest_run/_service.py:798-860`, `_factor_regression.py:131`) | 모든 날짜 `observations`(fields, factor_values) → `factor_periods`. tape는 판본만 |
| factor research(`application/factor_research/_service.py`) | validate·explain만 |
| equity 미리보기(`application/equity_workspace/_panel_preview.py:121`) | 파이프라인 밖 `EquityDataPort.load_panel`. 같은 셀·같은 값 계약만 공유 |
| 신호 라벨(`adapters/outbound/equity_duckdb/_signal_labels.py:49-68`) | 행 단위 원시 포트(`price.adj_close`, MASKED 키) |

## 9. duckdb 어댑터 (`adapters/outbound/equity_duckdb/_adapter.py`)

- 격자는 `universe_daily × security_span`이다. 종목 집합은 lag만큼 넓힌 fetch 창 안에서 한 번이라도 구성원인 종목이다(1775-1779, 1822-1839).
- 행은 창 세션만 낸다(1995-1999).
- GRID 원천의 lag는 **달력 세션 index − lag** 행에서 읽되 `span_seq`가 같아야 한다(2001-2013).
- LATEST 원천은 `bisect_right(공개일들, cutoff) − 1`이다. 늦은 필드는 값을 비우고 MISSING으로 둔다(2014-2027). 법인 축은 `_corp_map`이다.
- 셀 종류는 `_cell_kind`(232-253)가 정한다. 가림이 결측보다 먼저이고, `omitted_is_zero`면 0이다.
- `security_id = ticker:span_seq`(170, 290-292). 숫자는 `float(Decimal)`(482-489)이다. 열 경로의 숫자 규칙은 spec D3 원시 숫자 행이다(비스케일 정수 ÷ 10^scale, 넘치면 `float(Decimal)`).
- 열 경로(`_raw_panel.py`, T2-02·T2-03)는 행 경로와 같은 `_grid_sql`·`_latest_sql` 질의를 열 받기 투영으로 감싸 읽고, 셀 종류는 `_cell_kind`를 조합마다 평가한 표로 정한다. 질의 앞 판정(`_raw_request`)과 읽는 범위(`_fetch_range`), 법인 축 대응(`_corp_map`)도 두 경로가 함께 쓴다.
- 열 경로의 LATEST는 (축 키, 공개일) 정렬 열에서 `searchsorted(side="right") − 1`로 찾고 같은 축 키인지 본다. 축 키는 격자에 나온 티커 전부(랙 행 포함)이고, 법인 축은 `_corp_map`이 없는 티커를 셀 없음으로 둔다. 늦은 필드는 셀을 고른 뒤 필드 공개일 > 컷오프인 칸만 값 없는 MISSING·행 공개일로 바꾼다.
- 숫자가 아닌 선언 타입은 행 경로 `_as_float`처럼 행 경로가 읽는 행(GRID는 원천 행이 있고 공개일·내용일이 다 있는 행, LATEST는 전 행)에 값이 실렸을 때만 실패하고, 아니면 값 없는 셀이다. 날짜가 아닌 공개일·내용일 열도 같은 규칙이다(GRID 공개일은 원천 행·내용일이 있는 행, 내용일은 원천 행·공개일이 있는 행에서 읽고, LATEST 내용일은 NULL을 행 공개일로 대신한다). 실패 문장은 행 경로와 다르고 예외 타입(`EquityDuckdbSetupError`)만 같다(#582 리뷰 P3-1). duckdb 필드 계약에는 범주 필드가 없어 `text`는 -1, `categories`는 비어 있다.
- 질의 `field_ids`가 겹치면 열 경로는 행 경로를 불러 그 예외를 그대로 낸다. 열 패널은 같은 field_id 열을 두 번 담지 못해(`RawPanel` 생성의 ValueError) 그 질의는 어느 경로에서나 실패한다. 어느 예외가 먼저인지(셀 생성·집합 검증의 계약 위반, 없으면 `from_observation_set`의 ValueError)는 행 경로의 검사 순서가 정하고 열 경로는 그 순서를 다시 적지 않는다(#582 리뷰 nit-4).
- 어댑터는 `load_raw_panel`로 `ColumnarRawObservationPort`를 선언한다(T2-03). 운영 파이프라인은 T4-01부터 고른다.
