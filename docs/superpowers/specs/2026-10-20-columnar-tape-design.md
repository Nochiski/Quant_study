# tape 열 지향 계산 설계

- 작성: 2026-10-20
- 상태: 승인(2026-10-20 사용자 지시: 측정 결과로 성능 개선안을 기획·구현·리뷰·머지까지 진행, 묻지 않는다. 범위는 제품 전체)
- 기획 패키지: [docs/planning/columnar-tape](../../planning/columnar-tape/README.md)
- 측정 근거: [run-worker-process BENCHMARK](../../planning/run-worker-process/BENCHMARK.md) 4~5절
- 짝 initiative: [run 별도 프로세스 실행](./2026-10-19-run-worker-process-design.md)

이 문서가 이 initiative의 계약 SoT다. PR 범위·순서는 [WORKFLOW.md](../../planning/columnar-tape/WORKFLOW.md),
진행 상태는 [PLAN.md](../../planning/columnar-tape/PLAN.md)가 소유한다.

## 1. 문제

### DEFECT-CT-01: tape 단계가 계산이 아니라 객체 변환에 시간을 쓴다

- **상황**: 실데이터 duckdb 어댑터, `krx.common-stock`, 12-1 모멘텀 + 거래대금 상위 30% + 월별 상위 20, 2020-01-02~2026-09-18.
- **인풋**: `PortfolioDesignService.run_pipeline`(백테스트 run의 tape 단계, `application/portfolio_design/_service.py:307`).
- **에러 위치**: 단계별 시간과 원인은 다음과 같다(`BENCHMARK.md` 4절).

  | 단계 | 시간 | 위치 | 비용 |
  |---|---|---|---|
  | 원시 관측 로딩 | 98.6초(그중 SQL 14초) | `adapters/outbound/equity_duckdb/_adapter.py:1173-1270`, `1840-1889` | Python 행·셀 객체 조립 |
  | 재검증 | 7.6초 | `ports/outgoing/raw_observations.py:227-285` | 같은 검증을 두 번 |
  | 팩터 입력 | 33.4초 | `_service.py:931-951` | 관측마다 객체 |
  | 팩터 평가 | 28.6초 | `domain/factor/_evaluation.py` | 노드 × 행 Python 루프 |
  | 포트폴리오 관측 | 64.4초 | `_service.py:954-1029` | 쓰지 않는 날짜까지 객체. 월별 6.7년이면 80일만 필요한데 약 1,650일치를 만든다 |
  | 컴파일 | 13.8초 | `domain/portfolio/_compiler.py` | |

  py-spy 기준으로 객체 생성 14%, 변환 generator 12%, 래퍼 10%, 재검증 7%이고 시계열 계산 자체는 5%다.
- **위험성**: run 하나에 수 분이 걸린다. 실험 trial마다 같은 비용이 반복되고, 5년 run 하나가 5.5GB를 쓴다. 카테고리: 성능·자원.

### DEFECT-CT-02: 상한 배분이 문자열 set 순서에 기대 tape 해시가 프로세스마다 갈린다(T0-02로 먼저 고친다)

- **상황**: `factor_score` 또는 `risk` 가중이고 선정 종목이 3개 이상인 모든 프레임. 상한이 걸리지 않아도 해당한다. 첫 반복의 분모 합부터
  set 순서를 따르기 때문이다(리뷰 실측: cap=1.0에서도 seed마다 해시가 달랐다). `rank`·`equal` 가중은 정수 합이라 안전하다.
- **인풋**: 같은 spec·관측을 `PYTHONHASHSEED`가 다른 두 프로세스에서 `compile_target_tape`로 컴파일한다.
- **에러 위치**: `domain/portfolio/_compiler.py:1350` `remaining = set(scores)`. 합(1355)과 남은 예산 차감(1385)이 set 순회 순서를 따른다.
- **위험성**: 같은 입력의 `tape_hash`와 run 지문이 프로세스마다 다르다(비결정, silent). run worker 트랙의 V-3 "두 모드 결과 동일"도 깨진다.
  수정 뒤에는 `factor_score`·`risk` 가중 run의 지문이 사실상 전부 한 값으로 고정된다. `target_tape_hash`가 지문에 들어가므로
  (`domain/backtest/_canonical.py:42`) 옛 run과 지문이 다를 수 있다. 같은 입력 잇기(`run_input_key`)와 시도 키는 tape를 담지 않아 영향이 없다.
  재현 테스트: `tests/domain/test_portfolio_pipeline.py::test_capped_allocation_does_not_depend_on_the_string_hash_seed`.

## 2. 측정이 가리키는 답

시제품은 어댑터 격자 SQL을 Arrow로 받고 numpy로 같은 의미를 계산했다(`BENCHMARK.md` 5절). 결과는 6.7년 tape 247초에서 약 3.4초였고, 80/80 프레임 선정이 같았다.
GPU(CuPy)는 run 하나에는 numpy보다 2~3배 느리고, 데이터를 GPU에 둔 200조합 스윕에서만 35~60배 빠르다. 이 initiative의 범위는 CPU numpy다.

## 3. 결정

### D1. 비트 단위 동일, 판본은 올리지 않는다

- **선택**: 새 구현은 지금 구현과 모든 칸의 값이 비트 단위로 같아야 한다. 그래서 `FACTOR_EVALUATION_VERSION`, `plan_hash`, `tape_hash`, run 지문,
  `run_input_key`, 시도 키, factor-regression 캐시 키, 연속 계좌 판본 대조가 모두 그대로다.
- **대안**: 허용오차(예: 상대 1e-12) + 선정·비중 동일 + 판본 올림.
- **선택 이유**:
  - 판본을 올리면 연속 계좌 평가가 기존 실험 선택을 모두 거절한다(`application/continuous_account/_service.py:271-277`,
    `adapters/outbound/continuous_pipeline/_adapter.py:149-166`). 새 run 지문도 옛 run과 갈린다.
  - 지금 정의는 이미 구현과 무관한 수학적 정의다. `statistics.mean`과 `pstdev`(3.11)는 정확한 유리수를 한 번만 반올림한다.
    그래서 같은 정의를 벡터로 재현할 수 있다(D3).
  - 비트 동일이면 대조 하네스의 허용오차를 0으로 둘 수 있어 판정이 단순하다.

### D2. numpy를 domain·application의 순수 배열 계산에 허용한다

- numpy는 이미 core 의존성이다(`backend/pyproject.toml`의 `numpy>=1.26`). IO를 하지 않는다.
- pyarrow(optional extra `parquet`)와 duckdb는 adapter에서만 쓴다. 근거는 `parquet` extra 없는 운영 설치다(CI `backend-no-extras` job은 dev 그룹으로
  pyarrow를 설치하고 assistant·architecture 테스트만 돌려 이 경계를 잡지 못한다). 강제 수단은 `test_forbidden_imports`다(T2-01).
- `.claude/rules/backend-package-boundary.md`와 `tests/architecture/test_forbidden_imports.py`에 이 규칙을 적는다(T2-01).

### D3. 수치 동일성 방식(연산별)

| 연산 | 방식 |
|---|---|
| field·constant·parameter·negate·lag·conditional·comparison | 복사·선택·단일 비교. 같다 |
| binary 4종 | IEEE 단일 연산, 같은 결합 순서. 분모 `== 0`(−0.0 포함) 마스크 |
| momentum·delta | 두 번의 IEEE 연산을 같은 순서로 |
| ts min·max | `np.minimum`은 `min(0.0,-0.0)`과 다르다. 오래된 칸부터 `acc = where(x < acc, x, acc)`(max는 `>`)로 누적해 "같으면 앞 원소"를 재현한다 |
| mean·demean·neutralize·zscore 중심 | **인증 커널**: +0.0에서 시작한 TwoSum double-double 합 → `q=(s+c)/n` → Dekker TwoProduct 잔차 보정 1회(numpy에 fma가 없다) → 반올림 경계(`(q+pred(q))/2`, `(q+succ(q))/2`)까지 거리가 오차 상한보다 크면 확정, 같거나 작으면(정확한 중점 포함) 그 칸만 `statistics.mean`. 극단 크기(\|q\| > 1e300, 0이 아닌 \|q\| < 1e-290 — Dekker 오차항이 2^-1074 밑으로 떨어지는 경계)와 비유한값은 정확 경로 |
| std·zscore 편차 | 이동값을 뺀 double-double Σd·Σd²로 분산 dd를 만들고 후보 s의 반올림 경계 `((s+pred(s))/2)²`·`((s+succ(s))/2)²`(2의 거듭제곱 경계는 아래 간격이 절반)와 비교해 인증한다. 경계와 같으면(작은 창에서 흔하다, n=2에서 21%) 인증 실패로 보고 정확 경로. 극단 크기(\|x\| > 1e150, 0이 아닌 \|x\| < 1e-150)와 인증 실패 칸은 `statistics.pstdev` |
| median 채움 | 동료 입력 순서를 유지하는 **안정 정렬**(`kind="stable"`) 뒤 홀수는 가운데 원소, 짝수는 `(a+b)/2`를 직접 계산한다(numpy median 금지). 기본 정렬은 −0.0·0.0 순서를 바꿔 부호가 달라진다(리뷰 실측 2만 건 중 5,744건) |
| rank(횡단면·그룹·정규화) | 평균 순위 `(pos+1+end)/2`(정확), IEEE 나눗셈 한 번, 안정 정렬과 `==` 동점 |
| winsorize | **안정 정렬** 뒤 `p×(n-1)`, `int()`, `(1-w)·a + w·b` 순서 그대로. `min(max())`는 `where`로 Python 의미를 재현 |
| 컴파일러 점수 | 후보마다 팩터 순서대로 누적. 결측 팩터는 `where(mask, s+t, s)` |
| 컴파일러 합 | `np.sum`(쌍별)을 쓰지 않는다. 왼쪽부터 차례로 더하는 순차 누적을 명시한다(3.12+ `sum`의 보정 합산과도 무관해진다) |
| 해시 payload | `np.float64` repr은 float와 같지만 `np.bool_`·`np.int64`는 JSON이 거절한다. 객체로 만들 때 반드시 Python 타입으로 바꾼다 |
| 원시 숫자(어댑터) | `fetchnumpy`와 `CAST AS DOUBLE`은 둘 다 DECIMAL을 항상 정확히 반올림하지는 않는다(리뷰 실측: scale > 0이고 비스케일 정수 > 2^53이면 `fetchnumpy` 24~28%, CAST 1~27% 다름). 규칙: 열 선언 타입마다 \|비스케일 정수\| ≤ 2^53이고 scale ≤ 22이면 비스케일 정수를 받아 `unscaled / 10**scale` 나눗셈 한 번(두 수가 정확히 표현되므로 정확 반올림)으로 만든다. 그 밖의 셀만 `float(Decimal)` 정확 경로다. DOUBLE 열은 그대로 받는다. Tier 2 전 셀 비트 대조로 확정한다 |

시제품: 인증 평균 20만 창에서 오답 0건, 정확 경로로 넘긴 칸 0.99%, 0.15초(`statistics.mean` 7.1초). 정확 경로 비율은 데이터에 따라 다르다
(리뷰 실측: 수익률 n=252 0.09%, n=20 2.1%, 상쇄가 큰 데이터 17.6%, n=2 21.7%). 오차 상한 식은 T3-03·T3-04 PR이 문서로 고정한다.

**비트 동일의 기준 판은 CPython 3.11이다.** 3.12부터 builtin `sum`이 보정 합산을 쓰고(`requires-python`에 상한이 없다), 3.13에서는 지금 구현의
tape 해시도 바뀐다(run worker BENCHMARK 3절). 대조 하네스와 digest는 3.11 기준이며, 판을 올리는 일은 엔진·평가 판본 결정과 함께 따로 한다.

### D4. 데이터 모양

- **행 공간**: 정본 순서는 (as_of, security_id)이고, 지금 `RawObservationSet`·`FactorValue` 순서와 같다. `security_id`는 정렬된 사전 코드다.
  코드 순서는 Python 문자열 순서와 같다. 날짜는 history와 sessions를 합친 세션 index(int32)다.
  시계열은 종목 우선 안정 순열·종목 안 위치·세그먼트 시작으로 셈한다. 창은 **세션 수가 아니라 종목 안 행 위치**를 센다(D-003,
  `raw_observations.py:236-237`).
- **`RawPanel`**(포트 타입, `application/portfolio_design/ports/outgoing/raw_panel.py`):
  - 행 열: `row_date`, `row_security`, `universe_member`, `sector`(코드, −1=None), `previous_weight`
  - 필드별 `RawFieldColumn`: `present`(셀 유무), `has_value`, `value`(float64), `available`(int32 일 서수), `kind`(int8). 범주형 필드는 코드와 사전
  - `sessions`, `history_sessions`, `status`, `detail`, `warnings`, `data_snapshot_id`
  - 열 단위 `validate_contract`(지금 계약과 같은 첫 위반 메시지), `from_observation_set()`, `observations(rows)`
  - 능력 프로토콜 `ColumnarRawObservationPort.load_raw_panel(query, *, checkpoint, progress)`
- **`FactorPanel`**(domain/factor 입력): 행 공간, 구성원, 숫자 필드(값·유효·가림), 그룹 코드. application이 `RawPanel` 뷰로 복사 없이 만든다.
- **`FactorNodeCache`**: 노드마다 값 열·유효 열·가린 칸 열. trace용 `value(node_id, row)`는 Python 값을 돌려준다.
- **`PortfolioPanel`**(domain/portfolio 입력): 측정 구간 행, 컴파일러가 읽는 필드 열, 팩터별 (값·유효·공개일), sector, previous_weight.
  `observations_on(dates)`는 필요한 날짜만 객체로 만든다. 프레임 하나는 행 공간에서 연속 구간이다.

### D5. 배치와 SoT

| 구성 요소 | 위치 | SoT 유지 |
|---|---|---|
| 열 원시 포트·검증·변환 | `application/portfolio_design/ports/outgoing/raw_panel.py`, facade `facade/ports.py` | 포트는 유스케이스가 소유. 원천 값·공개일 계약(`raw_observations.py`)과 같은 셀에 같은 값 |
| duckdb 열 로딩 | `adapters/outbound/equity_duckdb/_raw_panel.py` | `_grid`·`_latest` SQL 생성을 함수로 떼어 행 경로와 같이 쓴다. `_cell_kind` 규칙은 어댑터 안 한 벌(최종적으로 행 경로를 열 경로에서 파생). 취소는 `_fetchall` 감시 스레드 방식 |
| mock | 바꾸지 않는다 | 능력이 없으면 application이 `RawPanel.from_observation_set`으로 구조 변환 |
| 열 평가기 | `domain/factor/_panel_evaluation.py`, 정확 통계 커널 `_statistics.py` | 팩터 값 계산자는 `portfolio_design` 하나(SoT "팩터 **값**" 행). `cross_sectional_rank`·`zscore` 스칼라 API는 그룹 커널을 감싸 공식 owner 하나(SoT "결합 전 정규화 방식과 합성 공식" 행) |
| trace | `domain/factor/_trace.py` | `evaluate_factor_graph_with_trace` 진입점 유지(SoT "팩터의 세션 읽기 창" 행). 노드 값 접근만 캐시 뷰로 |
| 열 컴파일러 | `domain/portfolio/_frame_panel.py` + `_compiler.py` | 1단계(점수·1차 적격·정규화·2차 적격·순위)는 전 프레임 벡터, 2단계(버퍼·비중·상한·섹터·예산·finalize)는 프레임 fold 스칼라·순차 합. `compile_target_tape`와 `compile_scheduled_target_tape`가 같은 구현 |
| 파이프라인 | `application/portfolio_design/_service.py` | 관문 순서(4절)·팩터 값 공개일(SoT "팩터 값의 공개일" 행)을 열로 |
| 객체 경계 | 각 타입 owner | `TargetTape`·`PortfolioPreview`·HTTP 모양 불변. 필요한 날짜·종목만 객체로 |

`DEPENDS_ON` 변경은 없다(`domain.portfolio → domain.factor`는 이미 있다).

### D6. 파이프라인 관문 순서 보존

열 경로도 지금 순서를 지킨다. 예외 종류마다 HTTP 코드가 갈리므로 대조 하네스의 음성 사례가 고정한다(`_service.py:307-550`).

1. `_prepare`(문서 검증 → 실행 설정 → 파라미터 해소 → 엔진 판정 → trace 능력 → plan → 숫자가 아닌 출력 거절)
2. 원시 질의(`history_sessions_before_start = max(minimum_history_sessions - 1)`)
3. 소비 경계 검증 → `ok` → 스냅숏 일치 → 구간 밖 세션 → 리밸런싱 일정 → trace 날짜·범위
4. look-ahead 검사(`LookAheadViolationError`)
5. 평가(`NonFinite`는 `strategy.expression.calculation_non_finite`)
6. 포트폴리오 관측 → 컴파일 → 판본 유일성

### D7. 진행 보고와 협조적 취소

- checkpoint는 노드마다, 창 offset·축 스캔 반복마다, 원소 단위 전체 연산은 2^18행 청크마다 부른다. 목표는 4.2M행에서 checkpoint 간격 0.1초 이하다.
  정확 경로 루프는 256칸마다 부른다. duckdb 질의는 `_fetchall` 감시 스레드를 그대로 쓴다.
- 단계 경계 상수(`_service.py:113-118`)와 메시지는 유지한다. 경계 비율과 노드 가중치(`_node_progress_weight`)는 전환 뒤 다시 재서 고친다.
  단조 증가·1.0 종료 계약은 그대로다.

### D8. 전환 순서와 경로 단일성

1. 새 구현을 새 모듈에 둔다. 운영 경로에는 아직 연결하지 않는다.
2. `PortfolioDesignService(..., tape_engine="object"|"columnar")` 생성자 인자. 환경 변수·bootstrap은 건드리지 않는다(run worker 트랙의 bootstrap과 분리).
3. 기본값을 `columnar`로 바꾼다. 이때 컴파일러는 기존 객체 컴파일러에 신호일 관측만 넘긴다. 실원장 대조 0건을 증거로 남긴다.
4. 객체 평가기·변환 코드를 지운다. 기존 domain 테스트가 단일 경로를 검증하도록 `evaluate_factor_graph(observations=...)`는 객체 입력을 패널로 바꾸는 경계 함수로 남긴다.
5. 열 컴파일러를 기존 `_compile_frame`과 대조하고, 해시가 같으면 기존 구현을 지운다. `compile_target_tape(observations=...)` 객체 API는 객체 입력을
   패널로 바꾸는 경계 함수로 남긴다(평가기와 같은 방식, 기존 컴파일러 테스트가 단일 경로를 검증).
6. duckdb 행 경로를 열 경로에서 파생한다. 원시 관측 계약 검증도 하나만 남긴다. `RawObservationSet.validate_contract`는 `RawPanel` 검증에서 파생한다(T7-03).

두 구현이 함께 있는 것은 대조 기간뿐이다. 최종 상태에서 계산 경로는 하나다.

### D9. 객체 결과 호환

`PortfolioPipelineResult.raw_observations`·`observations`와 `FactorEvaluationRecord.values`는 지연 생성한다. 백테스트 run이 쓰는 `.preview`
시그니처는 바꾸지 않는다(run worker 트랙의 계산 함수가 이 호출을 옮긴다). 소비자별로 필요한 부분만 객체로 만든다.

| 소비자 | 읽는 것 | 전환 |
|---|---|---|
| 백테스트 run | `.preview`(tape, warnings) | T4 |
| `/portfolio/preview` | `PortfolioPreview` 전체(모든 프레임 candidates) | 객체 경계 |
| `/strategies/debug/trace` | 기준일 원시 행·구성원·팩터 값·candidates·trace | T6-01에서 열로 |
| S03 신호 연구 | 모든 날짜 관측·팩터 값 | T6-02 |
| 연속 계좌 | 구간 모든 날짜 관측(`ScheduledCandidate`) | T6-02 |
| factor-regression | 모든 날짜 관측의 `factor_periods` | T6-03(run worker W4-01 뒤) |

## 4. 의미 목록

열 경로가 같은 의미로 지켜야 하는 규칙의 전체 목록과 file:line은 [SEMANTICS.md](../../planning/columnar-tape/SEMANTICS.md)가 소유한다.
범위는 다음과 같다.

- 원시 관측 계약
- 파이프라인 관문
- 평가 공통 규칙(행 공간·동료 집단·값 타입·가림·결측 채우기·유한값 검사)
- 연산자별 규칙
- 파라미터·plan·판본
- 포트폴리오 관측 변환
- 컴파일러(일정·fold·사유 순서·정규화·횡단면 적격·순위·선정·버퍼·가중·상한·섹터·예산·finalize·targets·candidates·trace·경고·해시)
- 소비자별 중간 결과
- duckdb 어댑터 규칙

대조 하네스는 이 목록의 각 항목을 말뭉치 사례로 덮는다.

## 5. 대조 하네스

| 층 | 비교 | 기준 |
|---|---|---|
| L0 어댑터 | `load_raw_panel(q)` 대 `RawPanel.from_observation_set(load_raw_observations(q))` | 셀마다 present·kind·available·member·sector·previous_weight 같음, 값은 int64 비트 같음, 메타 같음 |
| L1 평가기 | 객체 `_compute_nodes` 대 열 캐시 | 모든 노드·칸의 타입과 None 여부, float 비트, 가린 칸 같음. 오류는 예외 타입과 (node_id, as_of, security_id, value) 같음 |
| L2 컴파일러 | 같은 `PortfolioObservation` 입력의 기존 `_compile_frame` 대 열 컴파일러 | targets 비중 비트, candidates 전 칸(사유 tuple 순서 포함), construction trace·요약, 경고 문장, `tape_hash` 같음 |
| L3 파이프라인 | 두 엔진의 `run_pipeline` | tape_hash·판본·경고·파라미터·trace·소비자 투영 같음 |

- **허용오차는 0**이다(D1).
- **말뭉치**: fixture 전략(`backend/tests/fixtures/strategy_documents/**`, `ideas/*.yaml`), 레지스트리 `implemented` 기본 graph, 연산자 × 결측 정책 4종,
  결측 × 정규화 × 가중 × 선정 × 롱숏 × 버퍼·최소 거래·상한·섹터·보조 순위·top_percent·top_count 조합, `np.random.default_rng(seed)` 합성 패널.
  합성 패널에는 행 공백·상장 폐지·비구성원 변동·가림·생략/None 구분·결측 연속·동점·±0·0 나누기·넘침·그룹 결측·극단 크기·상수 창·정수 값을 넣는다.
  데이터 출처는 mock 어댑터, 원장 손 픽스처(`tests/equity_fixture.py`), 실데이터다.
- **실데이터 스크립트** `backend/scripts/parity_tape.py`(validator, 테스트 아님):
  - `require_research_window`로 시작일 ≥ 2020-01-02를 강제한다.
  - 출력은 불일치 개수와 키뿐이다. 워밍업 칸 값과 수익률·IC는 출력하지 않는다(`holdout-window.md`).
- **CI**:

  | 등급 | 내용 | 언제 |
  |---|---|---|
  | Tier 1 | `tests/parity/`(L0 손 픽스처·mock, L1~L3 합성·fixture), 예산 60초, duckdb 필요분은 `importorskip` | 모든 PR |
  | Tier 1-b | 같은 L3 말뭉치를 `PYTHONHASHSEED` 0·1 하위 프로세스로 돌려 tape_hash 같음 | T0-02부터 |
  | Tier 2 | 실원장 리포트 불일치 0 | 평가기·어댑터·컴파일러를 고치는 PR과 기본값 전환 PR의 필수 증거(PR 본문) |
  | 퇴역 | 객체 구현을 지울 때 테스트가 직접 만든 입력의 digest를 상수로 고정(`test_factor_evaluation_version.py` 방식). 옛 코드를 tests로 복사하지 않는다 | T7 |

## 6. 검증 기준(완료 정의)

| ID | 기준 |
|---|---|
| C-1 | 6.7년 구간 tape 단계(같은 전략·기계, 워밍업 1회 제외)가 지금 247초의 10% 이하 |
| C-2 | 같은 run의 최대 RSS가 지금 대비 50% 이하(5년 5.5GB 기준) |
| C-3 | Tier 2 실원장 대조 불일치 0(1년·5년·최장 허용 구간) |
| C-4 | `FACTOR_EVALUATION_VERSION`·`tape_hash` 고정 테스트 digest 불변(T0-02 결정성 수정분 제외) |
| C-5 | 계산 경로 하나(객체 평가기·변환·컴파일러 제거, 경계 함수만) |
| C-6 | 취소 확인 간격 0.1초 이하, 진행률 단조 |

## 7. 하지 않는 것

- GPU 계산(2절). 파라미터 스윕용은 이 initiative 뒤에 측정 근거로 따로 결정한다.
- `TargetTape`·해시 모양 변경(tape 해시 v2). 매일 리밸런싱 전략은 객체 생성과 해시 비용이 남는다(추측: 수 초~10초). 측정 뒤 따로 결정한다.
- factor-regression analytics의 객체(`FactorPeriodInput`) 수백만 개. 입력 조립만 열로 바꾸고(T6-03) 회귀 계산은 범위 밖이다.
- equity 미리보기(`load_panel`)·신호 라벨 포트의 소비자 이전. 어댑터 계약만 공유한다.

## 8. 위험과 완화

| 위험 | 완화 |
|---|---|
| 인증 커널의 정확성 | 수백만 seed 차분 테스트, 정확 경로, 극단 크기는 무조건 정확 경로. 실데이터 정확 경로 비율은 T3에서 잰다 |
| 행 위치와 세션 혼동 | 위치는 종목 안 행 위치로 만든다. 합성 패널에 행 공백을 반드시 넣는다 |
| 오류 우선순위 변화 | D6 순서를 L3 음성 사례로 고정 |
| 열 포트가 bool·혼합 타입 셀을 받음 | 계약을 두 벌로 만들지 않도록 원시 계약의 값 타입(`RawFieldValueType`, `raw_observations.py:52`) 자체를 좁힌다(T2-01). 지금 그런 셀을 내는 어댑터는 없다. duckdb는 Tier 2, mock은 계약 테스트로 확인한다 |
| 실데이터 대조가 홀드아웃을 엶 | 스크립트가 시작일을 강제하고, 개수만 보고하고, 워밍업 값은 출력하지 않는다 |
| T0-02로 `factor_score`·`risk` 가중 run 지문이 옛 run과 달라짐 | 의미 변경이 아니라 비결정성 제거다(옛 값도 프로세스마다 달랐다). 판본은 올리지 않고 PR에 영향 범위를 적는다. 잇기·시도 키는 영향 없음 |

## 9. 롤백

T4-03 전에는 기본값이 객체 경로라 영향이 없다. T4-03 뒤에는 생성자 기본값 한 줄을 되돌린다. T7 삭제 뒤에는 PR revert다.
저장 형식·해시·판본이 바뀌지 않으므로 데이터 이전은 없다.
