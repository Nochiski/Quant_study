# tape 열 지향 계산 WORKFLOW

> For agentic workers: 이 파일이 PR 단위 scope packet과 공통 gate의 정본이다. 계약은
> [설계 spec](../../superpowers/specs/2026-10-20-columnar-tape-design.md), 의미 목록은 [SEMANTICS.md](./SEMANTICS.md),
> 진행 상태는 [PLAN.md](./PLAN.md)가 소유한다.

- **Goal**: tape 단계(원시 관측 → 팩터 평가 → 포트폴리오 관측 → 컴파일)를 numpy 열 계산으로 바꾼다. 6.7년 구간 247초를 10% 이하로 줄이고,
  모든 값을 비트 단위로 보존한다(spec D1).
- **Architecture**: `RawPanel` 포트 → `FactorPanel`·`FactorNodeCache` 열 평가기 → `PortfolioPanel` 열 컴파일러. 객체는 필요한 날짜·종목만 경계에서 만든다.
  엔진 선택은 `PortfolioDesignService` 생성자 기본값이다(spec D8).
- **Tech Stack**: numpy(core 의존성), duckdb `fetchnumpy`(adapter). 새 라이브러리는 없다.

## 1. 실행 순서와 스택

```
T0-02 상한 배분 결정성 ─┐
T0-01 기획 패키지 ──────┴─ T1-01 대조 하네스 골격
                              └─ T2-01 RawPanel 포트
                                   ├─ T2-02 duckdb GRID ── T2-03 duckdb LATEST
                                   └─ T3-01 평가기 골격 ── T3-02 끝점·min/max ── T3-03 정확 평균 ── T3-04 정확 pstdev
                                        ── T3-05 남은 연산 ── T3-06 trace
                                   (T2·T3 끝) ── T4-01 파이프라인 열 경로 ── T4-02 결과 지연 생성 ── T4-03 기본값 전환
                                        ├─ T5-01 컴파일러 1단계 ── T5-02 2차 적격·순위 ── T5-03 fold ── T5-04 해시 ── T7-02 객체 컴파일러 삭제
                                        └─ T6-01 trace 서비스 · T6-02 S03·연속 계좌 · T6-03 factor-regression(W4-01 뒤)
                                   ── T7-01 객체 평가기 삭제 ── T7-03 어댑터 단일화 ── T9-01 종료 리뷰
```

- PR ID는 `T<phase>-<nn>`이다. 스택 PR은 직전 PR 브랜치를 base로 연다. T2와 T3, T5와 T6은 서로 독립이라 병렬로 쌓을 수 있다.
- PR 크기는 diff 150~450줄을 권장한다. 600줄이나 파일 10개를 넘으면 나눈다.

### run worker 트랙(W)과 맞물리는 지점

1. **T0-02는 W1-02보다 먼저** 머지한다. W1-02의 V-3(두 모드 tape 해시 동일)이 해시 seed 비결정성 때문에 깨지면 안 된다.
2. `run_pipeline(...).preview` 시그니처를 유지한다. W1-01의 계산 함수가 그 호출을 옮긴다.
3. bootstrap을 건드리지 않는다. 엔진은 서비스 생성자 기본값으로 고른다(W1·W2가 `bootstrap/_container.py`·`bootstrap/run_worker`를 고친다).
4. W2-02 메모리 추정식 계수는 T4-03 측정 뒤 W 쪽에서 다시 맞춘다.
5. T6-03은 W4-01 머지 뒤에 한다(같은 factor-regression 구간).
6. W5-00 측정은 T6-01 뒤에 한다(trace·preview가 빨라지면 판단이 달라진다).
7. SoT 행: W3-01은 "실험 시간 어림"·"백테스트 run 접수"·"백테스트 run 기록" 행, T7-01은 팩터 관련 행(T7-01 packet)을 고친다. 행이 겹치지 않는다.
8. 비트 동일의 기준 판은 CPython 3.11이다(spec D3). Python 판 상향은 이 initiative 밖에서 판본 결정과 함께 한다.

## 2. 공통 gate

| 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run --no-sync pytest -q`(전체) → `pytest ../database/tests -q` → `ruff check src tests examples scripts tools`·바꾼 파일 `ruff format --check` → `pyright` |
| 대조 | `tests/parity`(Tier 1) 통과. 평가기·어댑터·컴파일러를 고친 PR은 Tier 2 실원장 리포트(불일치 0)를 PR 본문에 붙인다 |
| 성능 주장 | 같은 기계·전략, 워밍업 1회 제외, 측정 중 다른 무거운 작업 금지. 숫자는 BENCHMARK에 남긴다 |
| 홀드아웃 | 실데이터 구간 시작 2020-01-02 이후, 출력은 불일치 개수와 키만(`holdout-window.md`) |

리뷰어 프롬프트 체크리스트(사용자 우선순위):

1. **수치 동일성**: 비트 동일을 실제로 보장하는가. 합 순서, ±0, NaN, bool·numpy 스칼라 타입이 섞이지 않는가.
2. **SoT**: 팩터 값 계산자가 하나인가, 공식 owner(`_statistics.py`)가 하나인가, 두 구현이 대조 기간 밖에 남지 않는가.
3. **책임 분리·경계**: domain에 numpy 외 IO·pyarrow·duckdb가 없는가. facade·`DEPENDS_ON`을 지키는가.
4. **최소 코드량**: 범위 밖 리팩터링이 없는가.
5. **안정성·잠재 버그**: 취소 간격, 진행 단조, 오류 우선순위, 메모리.
6. **하네스**: 사용자 동작이 바뀐 PR이면 스토리·e2e를 고쳤는가. 이 initiative는 대부분 "해당 없음"이다.

Phase마다 작업분 SoT·책임 분리 점검을 하고, T9-01에서 portfolio_design·factor·portfolio 도메인 전체와 frontend·backend 경계 리뷰를 따로 돌린다.

## 3. PR packet

| PR | Intent | Acceptance | Non-goal | 예상 파일 | 테스트 |
|---|---|---|---|---|---|
| T0-01 | 기획 패키지 | spec·SEMANTICS·PLAN·WORKFLOW·README, `plan_progress` 프로필 `--check` 통과 | 제품 코드 | `docs/planning/columnar-tape/*`, spec, `tools/quant_study_dev/plan_progress.py` | 도구 check |
| T0-02 | 상한 배분 결정성(DEFECT-CT-02) | `_capped_allocate`가 `scores` 순서로 순회한다. 해시 seed 6개 하위 프로세스에서 tape_hash 같음 | 다른 정책 | `domain/portfolio/_compiler.py`, 테스트 1개 | 결정성 테스트, 기존 tie·weight 테스트 |
| T1-01 | 대조 하네스 골격 | `tests/parity/_corpus.py`(말뭉치·합성 패널), 비트 비교기·차이 보고기, `scripts/parity_tape.py`(홀드아웃 관문, 개수만 출력). 처음에는 객체 경로의 자기 일관성(seed 결정성, 창 불변성)을 검사 | 새 구현 | `tests/parity/*`, `backend/scripts/parity_tape.py` | 하네스 자체 테스트 |
| T2-01 | `RawPanel` 포트 | 타입·열 검증(SEMANTICS 1절, 같은 메시지)·`from_observation_set`·`observations()` 왕복·능력 프로토콜. 원시 계약 값 타입(`RawFieldValueType`)에서 bool을 빼 두 포트가 한 계약을 쓴다(mock 계약 테스트로 확인). numpy 사용 규칙을 경계 문서·`test_forbidden_imports`에 적음 | 어댑터 | `ports/outgoing/raw_panel.py`, `facade/ports.py`, 규칙 문서, 테스트 | mock·손 픽스처 왕복, 음성 사례가 `validate_contract`와 같은 메시지 |
| T2-02 | duckdb 열 로딩(GRID) | 숫자 열 규칙(spec D3 원시 숫자 행: 선언 타입별 비스케일 정수 ÷ 10^scale, 넘치면 `float(Decimal)`), lag 조인(span_seq), `_cell_kind` 벡터판(행 경로와 공유), 감시 스레드 | LATEST | `equity_duckdb/_raw_panel.py`, `_adapter.py`, 테스트 | L0 손 픽스처, Tier 2 L0 |
| T2-03 | duckdb 열 로딩(LATEST·법인 축) | asof `searchsorted`, 늦은 필드 규칙, `_corp_map`. 어댑터가 프로토콜 구현 | 운영 연결 | 같은 파일 | L0 전 필드 |
| T3-01 | 열 평가기 골격 | `FactorPanel`, 행·위치 배치, field·constant·parameter·unary·binary·comparison·conditional·lag, `_value_spans`·`_masked_in` 벡터판, zero 채움, 유한값 첫 위반, checkpoint·progress | ts·cs | `domain/factor/_panel_evaluation.py`, facade, 테스트 | L1 부분 행렬 |
| T3-02 | 시계열 끝점·min/max | 창 유효성(정확히 window칸), momentum·delta, Python 의미 min·max | mean·std | `domain/factor/_panel_time_series.py`(창 커널), `_panel_evaluation.py` 배선, 테스트 | L1, ±0 |
| T3-03 | 정확 평균 커널 | 인증 dd 평균 + 정확 경로, 창·그룹 형태, checkpoint. 오차 상한 식·중점(동점) 처리·+0.0 시작·극단 크기 가드를 코드 주석과 spec에 고정, 실데이터 정확 경로 비율 측정 | std | `_statistics.py` | CI는 seed 고정 차분 2만 건(예산 내), PR 증거로 로컬 수백만 건 차분 결과를 본문에 첨부. 동점·정수·극단 크기·n=2 |
| T3-04 | 정확 pstdev 커널 | 이동 dd 분산 + 반올림 경계 `((s+pred)/2)²`·`((s+succ)/2)²` 인증 + 정확 경로(경계와 같으면 실패 처리) | - | 같은 파일 | T3-03과 같은 CI·로컬 분량 나눔, n=2 중점 사례 |
| T3-05 | 남은 연산 | ts mean·std, cs rank·zscore·winsorize·demean, group neutralize·rank, 중앙값·그룹 중앙값 채움. 스칼라 rank·zscore를 그룹 커널 위로 | trace | 평가 모듈(`_panel_evaluation.py`·`_panel_time_series.py`·동료 묶음 `_panel_cross_section.py`), `_statistics.py`, `scripts/parity_tape.py` L1 층 | 전체 L1, L1 퍼저(`tests/parity/_fuzz.py`), 기존 `test_factor_*` |
| T3-06 | trace를 캐시에서 | `_trace.py`가 `FactorNodeCache` 뷰를 읽는다 | 서비스 | `_trace.py`, 두 캐시의 뷰(`_evaluation.py`의 `_NodeEvaluator.cache`, `_panel_evaluation.py`의 `FactorNodeCache.is_masked`) | 기존 `test_factor_trace.py` + 두 캐시 trace 동등(`tests/parity/test_trace_parity.py`) |
| T4-01 | 파이프라인 열 경로 | `tape_engine` 인자(기본 object), 능력 협상·변환, 관문 순서(SEMANTICS 2절), 열 평가, `PortfolioPanel`, 열 팩터 값 공개일, 신호일만 객체로 기존 컴파일러에. 부팅 뒤 run에서 프로젝트 모듈을 처음 import하지 않는다(worker 가드가 거절, run-worker spec D7) | 기본값 전환 | `application/portfolio_design/_service.py`(또는 `_columnar.py`), `domain/portfolio/_frame_panel.py` | L3 전 말뭉치, 오류 순서 사례 |
| T4-02 | 결과 호환 | `raw_observations`·`observations`·`values` 지연 생성, `.preview` 불변 | 소비자 이전 | 같은 파일, `_models.py` | 소비자별 L3 투영 동등 |
| T4-03 | 기본값 전환 | 기본 `columnar`, Tier 2 전 구간 불일치 0, BENCHMARK 재측정(tape·RSS, C-1·C-2), 취소 확인 간격 실측(C-6), 진행 구간 상수 재측정 | 삭제 | `_service.py`, BENCHMARK | 전체 pytest, 진행 단조 테스트 |
| T5-01 | 컴파일러 1단계 | 점수·1차 적격·정규화·사유 이벤트 행렬·비유한 첫 위반 | fold | `_frame_panel.py`, `_compiler.py` | L2 후보 단위 |
| T5-02 | 2차 적격·순위 | 횡단면 컷·보조 순위·숏 순위·선정 수 | 비중 | 같음 | L2 순위 |
| T5-03 | fold·객체 생성 | 버퍼·가중 4종·상한·최소 거래·섹터·예산·finalize(순차 합), `TargetFrame`·construction trace. 연속 일정 컴파일러도 같은 구현 | 해시 | 같음, `_scheduled_compiler.py` | L2·L3 tape_hash, `test_scheduled_portfolio.py` |
| T5-04 | 해시 고속화 | 프레임 단위 encoder로 `_hash_payload`와 바이트 동일, `_require_finite_tree`를 열 검사로 | 해시 계약 | `_compiler.py` | 바이트 동일 단언 |
| T6-01 | trace 서비스 | 기준일 원시 행·구성원·팩터 값을 열에서 읽는다 | - | `_trace_service.py` | trace HTTP 통합 |
| T6-02 | S03·연속 계좌 | 정합 검사를 열로, 필요한 날짜만 객체 | analytics | `signal_pipeline/_adapter.py`, `continuous_pipeline/_adapter.py` | 기존 S03·S04 통합 |
| T6-03 | factor-regression 입력 | `factor_periods`가 `PortfolioPanel`을 읽는다(W4-01 뒤) | 회귀 공식 | `backtest_run/_factor_regression.py`, `_service.py` | `test_factor_regression*` |
| T7-01 | 객체 평가기 삭제 | 옛 평가기·변환 삭제, digest 고정, SoT("팩터 정의"·"팩터의 세션 읽기 창"·"팩터 **값**"·"팩터 값의 공개일"·"결합 전 정규화"·"팩터 평가 규칙 판본" 행)·경계 규칙·`test_forbidden_imports` 갱신 | - | `_evaluation.py`, `_service.py`, tests, rules | 전체 |
| T7-02 | 객체 컴파일러 삭제 | T5 대조 끝, 옛 `_compile_frame` 경로 삭제. `compile_target_tape(observations=...)`는 패널 변환 경계 함수로 남김 | - | `_compiler.py`, tests | 전체 |
| T7-03 | 어댑터 단일화 | duckdb 행 API를 열 경로에서 파생, `_signal_labels`도 열 경로로, `RawObservationSet.validate_contract`를 `RawPanel` 검증에서 파생(계약 검증 하나). L0 대조 규칙도 한 벌로 합친다 — 지금은 `scripts/parity_tape.py`의 열 대조기 `panel_differences`(실원장용, 칸 수만)와 테스트의 `_projection`+`tests/parity/_compare.py`(손 픽스처용, 값까지) 두 벌이다(#582 리뷰 nit-1) | 포트 행 메서드 제거 결정 | `equity_duckdb/*` | L0, 계약 테스트 |
| T9-01 | 종료 리뷰 | 경로 단일성·SoT·경계 감사, 도메인 전체 리뷰 | - | - | - |

## 4. Backlog

- tape 해시 v2(매일 리밸런싱 전략의 객체 생성·해시 비용), GPU 스윕, factor-regression analytics 열 지향화(spec 7절).
- 평균·pstdev 동점 벡터 결정(증명·퍼저·변이 필수, #584 리뷰 후속). 지금은 정확한 중점(동점)을 정확 경로로 보내서
  작은 창의 비용 대부분이 여기서 나온다(T3-03 합성 수익률 n=2 21%, n=20 1.9%). T3-04 pstdev도 n=2에서 약 21%가 경계 동점이라
  효과가 더 크다. 방법: c 누적도 TwoSum으로 나르고 그 오차 절대값 합 e2를 함께 나른다. e2 == 0이고 잔차를 만드는 TwoSum 세 번의
  오차가 모두 0이면 r이 정확하다. 이때 r == ±n·gap/2이면 정확한 중점이므로 짝수 쪽(half-even)을 고르고, 나머지 칸은 지금처럼
  정확 경로로 보낸다(충분조건만 쓴다). 리뷰어 시제품은 비트 동일, 200만 창 불일치 0건, 홀수를 고르는 변이 검출,
  n=2 4.2M원소 9.15초 → 1.96초(정확 경로 20.8% → 0%)였다. 리드 결정(2026-10-20): T3-04에 넣지 않고 T3-04 다음 별도 PR로 한다(평균과 pstdev를 함께).
