# 백테스트 run 별도 프로세스 실행 설계

- 작성: 2026-10-19
- 상태: 승인(2026-10-20 사용자 지시로 기획·구현·머지까지 진행)
- 기획 패키지: [docs/planning/run-worker-process](../../planning/run-worker-process/README.md)
- 측정 기록: [BENCHMARK.md](../../planning/run-worker-process/BENCHMARK.md)
- 고치는 결정: [검증 랩 설계 spec](./2026-09-29-validation-lab-design.md) D6의 "스레드 모델은 유지한다 … 프로세스
  워커는 non-goal이다"와 non-goal "프로세스 워커·분산 실행"

이 문서가 이 initiative의 계약 SoT다. PR 범위와 순서는 [WORKFLOW.md](../../planning/run-worker-process/WORKFLOW.md)가,
진행 상태는 [PLAN.md](../../planning/run-worker-process/PLAN.md)가 소유한다.

## 1. 문제

백테스트 run은 API 서버(uvicorn) 프로세스 안의 데몬 스레드에서 돈다
(`backend/src/strategy_workbench/application/backtest_run/_service.py:1101-1113`). run 계산은 대부분 순수
Python 루프라 실행 내내 GIL을 쥔다. 그래서 같은 프로세스의 HTTP 요청과 다른 run이 함께 멈춘다.

### DEFECT-RW-01: run이 도는 동안 API 응답이 수백 ms~10초 늦어진다

- **상황**: 실데이터(duckdb 어댑터, `krx.common-stock`), 12-1 모멘텀 전략 하나, 기본 슬롯 2개, 다른 run 없음.
- **인풋**:
  1. `POST /api/v1/backtests`(2021-01-04~2026-09-18, Rust 코어)
  2. run이 도는 동안 0.25초 간격으로 `GET /api/v1/health`
- **에러 위치**: `_service.py:1108`이 run을 API 프로세스 스레드로 띄운다. 계산 본문은
  `application/portfolio_design/_service.py`의 `run_pipeline`(원시 관측 조립·팩터 평가·포트폴리오 관측·컴파일,
  모두 Python 루프)과 `adapters/outbound/backtest_engine/_adapter.py:445-456`의 Rust `engine.run`이다. Rust 쪽도
  GIL을 놓지 않는다(`rust/backtest_core/src/persistent.rs:704`, `allow_threads`·`py.detach` 0건).
- **위험성**: health 응답이 유휴 p50 1.2ms에서 tape 단계 p50 468ms, p95 1.45초, 최대 10.1초로 늘었다. 그동안 프로세스는
  12코어 중 정확히 1.0코어만 썼다. 설정 화면의 AI 공급자 연결 테스트가 멈춘 것처럼 보인 것도 같은 원인으로 추정한다(따로 재지 않았다).
  카테고리: 사용자 대면 지연(가용성). 데이터 손상은 없다.

### DEFECT-RW-02: 슬롯을 2개로 늘려도 처리량이 늘지 않고 줄어든다

- **상황**: 위와 같은 조건, 초기자본만 1원 다른 run 두 개(같은 입력 잇기를 피하려고).
- **인풋**: 두 `POST /api/v1/backtests`를 연달아 보낸다.
- **에러 위치**: `_service.py:146`의 `DEFAULT_RUN_SLOTS = 2`. 두 스레드가 GIL 하나를 나눠 쓴다.
- **위험성**: 조용한 상태에서 1년 구간 run이 혼자 44.6초인데, 둘이 동시에 돌면 각각 83.1초가 걸린다. 사실상 직렬이다.
  같은 두 run을 별도 프로세스로 돌리면 각각 39.7초다([BENCHMARK.md](../../planning/run-worker-process/BENCHMARK.md) 1절).
  5년 구간에서는 각각 724초까지 늘었다. 메모리 압박과 다른 부하가 함께 있던 측정이다.
  검증 랩 D6의 제품 의도("여러 실험을 비동기 대기열에 넣어 병렬로 돌린다", `docs/planning/validation-lab/PLAN.md:63`)가
  실제로는 직렬보다 느린 실행으로 구현돼 있다. 실험 남은 시간 어림도 이 전제("run 이 한 프로세스의 스레드")를 깔고
  있다(`.claude/rules/strategy-workbench-sot.md:75`). 카테고리: 처리량 역전(성능), 남은 시간 어림이 틀어짐.

### DEFECT-RW-03: 끊을 수 없는 단계에 걸린 run은 취소해도 끝까지 돈다

- **상황**: run이 data 단계(`load_backtest_dataset`)나 engine 단계(`engine.run`)에 있다.
- **인풋**: `POST /api/v1/backtests/{run_id}/cancel`
- **에러 위치**: 취소 신호는 `threading.Event`이고, 계산 코드가 확인할 때만 반영된다(`_service.py:1177`, `1256`).
  data 질의와 엔진 커널에는 확인 지점이 없다(`backend/README.md:78-81`, `strategy-workbench-sot.md:87`).
- **위험성**: 취소한 run이 끝날 때까지 슬롯과 메모리를 쥐고, 대기열의 다음 run을 막는다. 카테고리: 자원 고착(가용성).

### DEFECT-RW-04: run이 끝나도 서버 프로세스가 메모리를 돌려주지 않는다

- **상황**: 실데이터 run 4건을 돌린 뒤의 서버 프로세스.
- **인풋**: 없음(유휴 상태에서 관측).
- **에러 위치**: 원시 관측·팩터 값·TargetTape를 수백만 개의 Python 객체로 들고 다닌다(`_gc_policy.py:3-6`). CPython은
  해제한 객체의 arena를 OS에 잘 돌려주지 않는다.
- **위험성**: 5년 구간 run 하나의 최대 RSS가 5.5GB였다(별도 프로세스에서 측정). 동시 2개를 돌린 서버의 최대 working
  set은 8.6GB였고, 끝난 뒤에도 private commit 1.67GB가 남았다. 16GB PC에서 run이 겹치면 페이징이 생긴다. 카테고리: 자원 누수·성능 저하.

## 2. 측정이 가리키는 원인

| 관측 | 값 | 뜻 |
|---|---|---|
| run 중 프로세스 CPU | 1.0코어(tape 1.02, data 1.0, engine 0.96) | GIL에 묶임. 11코어가 논다 |
| 시간 배분(5년) | tape 약 194초, data 약 9초, engine 약 2초, artifact 1초 | 비용은 Python 계산 |
| 별도 프로세스 run(5년) | 206초, 부팅 6.2초(import 3.4 + 조립 2.8) | 프로세스를 나눠도 run 하나의 속도는 같다 |
| 별도 프로세스 2개 동시(1년) | 각 39.7초(혼자 37.4초), API p95 3.2ms | 진짜 병렬. API가 run의 영향을 받지 않는다 |
| 결과 동일성 | 별도 프로세스 run의 머리 지표가 서버 run과 비트 단위로 같다 | 같은 계산 경로 |
| tape 비용 구성(6.7년) | 247초 중 SQL 14초, 나머지는 객체 변환·검증·Python 루프 | 열 지향으로 바꾸면 약 3.4초(10절) |

그래서 이 initiative가 고치는 것은 **API 응답성, 동시 run의 처리량, 취소 지연, 메모리 회수** 네 가지다.
**run 하나의 계산 시간은 이 initiative로 줄지 않는다.** 그것은 tape 단계의 벡터화가 할 일이다(10절 후속).

## 3. 결정

### D1. 격리 단위: run 하나 = worker 프로세스 하나, 끝나면 종료

- **선택**: run마다 worker 프로세스 하나가 tape → data → engine → artifact 기록을 수행하고, 결과를 알린 뒤 종료한다.
  worker는 run 하나만 처리한다(max-tasks-per-child = 1).
- **대안**:
  - (a) 스레드 유지 + GIL 해제(Rust `py.detach`, duckdb 질의 중 해제): 비용의 95%인 tape가 순수 Python이라 효과가
    없다.
  - (b) 오래 사는 프로세스 풀(`ProcessPoolExecutor`): 메모리 단편화(DEFECT-RW-04)가 worker로 옮겨갈 뿐이다. 취소하려면
    결국 프로세스를 죽여야 하므로 풀을 다시 채우는 경로가 필요하다. `_gc_policy`의 프로세스 전역 상태도 run 사이에 남는다.
  - (c) free-threaded CPython 3.13t: 프로젝트 요구는 3.11+이다. duckdb, pydantic-core, pyo3 0.23 확장의 nogil 휠과 동작을
    모두 검증해야 하고, 단일 스레드 성능이 떨어진다.
  - (d) 외부 작업 큐(Celery·RQ + Redis): 로컬 단일 사용자 앱에 브로커 운영 부담을 더한다. 슬롯·잇기·공유 취소 규칙을
    브로커 의미로 다시 써야 한다.
- **선택 이유**: 프로세스가 끝나면 OS가 메모리를 전부 회수하고(RW-04), 취소는 프로세스 종료로 어느 단계에서든
  즉시 끝난다(RW-03). worker 안에서는 `_gc_policy`가 프로세스 전역이어도 다른 요청에 영향이 없다. 부팅 비용(6.2초)은
  실데이터 run 시간의 3%이고, D7의 예비 worker로 숨긴다.

### D2. 기동 방식: `subprocess`로 모듈 진입점을 띄운다

- **선택**: `subprocess.Popen([sys.executable, "-m", "strategy_workbench.bootstrap.run_worker"], ...)`. 인자는 배열로
  넘기고 셸 문자열을 만들지 않는다.
- **대안**: `multiprocessing`(spawn).
- **선택 이유**: 개발 서버는 `--reload`로 뜨고(`bootstrap/_server.py:7-15`), 그 서버 자체가 이미 multiprocessing
  spawn 자식이다. spawn은 부모의 `__main__`(`server.exe` 스크립트)을 다시 import하므로 동작이 진입 방식에 좌우된다.
  모듈 진입점은 Windows와 Linux에서 같게 돌고, 인코딩·환경·작업 디렉터리를 명시할 수 있다. 저장소 선례도
  있다(`adapters/outbound/research_snapshot/_guard.py:56-76`).
- **고정 사항**:
  - 자식 환경에 `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`을 넣고, 부모는 파이프를 UTF-8로 읽는다. cp949 출력을 UTF-8로
    읽다 난 `UnicodeDecodeError`가 2026-10-19 로그에 실제로 있었다.
  - Windows에서는 `CREATE_NEW_PROCESS_GROUP`으로 띄워, 개발 터미널의 Ctrl+C가 worker에 직접 닿지 않게 한다.
    종료는 D5의 부모 종료 감지가 맡는다.

### D3. IPC: stdin 작업·명령 줄, stdout 이벤트 줄, pickle 금지

- **선택**: 줄 단위 JSON(JSON Lines) 프로토콜 v1. 인코딩·디코딩은 한 모듈이 소유하고, 버전이 다르면 양쪽이 거절한다.
  - 부모 → worker(stdin): `job` 한 줄, 필요할 때 `proceed`(D11). 이후 stdin은 열어 둔다(D5 부모 사망 감지).
  - worker → 부모(stdout): `ready`(부팅 완료, D6 판본 묶음 포함), `progress`(stage·fraction·message), `engine_ready`(D11,
    요청한 job에서만), `committed`(D4), `failed`(error_code·error) 중 하나씩. 종결 이벤트는 `committed`·`failed` 둘이다.
  - stderr는 로그다. 부모가 run_id를 붙여 자기 로거로 중계한다.
- **대안**: pickle(`multiprocessing.Queue`), 공유 메모리, gRPC.
- **선택 이유**: 작업 입력은 이미 직렬화 계약이 있다. `ResolvedBacktestRunSpec.to_wire()`(`domain/backtest/_models.py:402-414`)와
  `BacktestRunSpec`의 pydantic 표현을 그대로 쓴다. pickle은 코드 판본에 묶이고 역직렬화가 코드 실행이다. 결과 묶음(이번 run
  6.7MB JSON)은 파이프로 넘기지 않으므로(D4) 대역폭 문제가 없다.
- **진행 이벤트 상한**: worker는 단계 메시지가 바뀌거나 직전 전송 뒤 0.1초가 지났을 때만 `progress`를 보낸다. 마지막 값은
  항상 보낸다. 0.01 미만 상승을 버리는 규칙(`_service.py:1339-1357`)은 부모가 계속 소유한다.
- **파이프 교착 방지**: 부모는 worker마다 stdout·stderr를 각각 끝까지 읽는 스레드를 둔다. 읽기를 멈추는 경로는 없다(D12).

### D4. 쓰기 소유권: SQLite는 부모만, worker는 결과 파일만

- **선택**:
  - worker는 결과를 기존 `LocalArtifactStore.commit`으로 `<root>/<run_id>/result.json`에 원자 기록한다.
  - 이어서 `committed{sha256, session_sharpe, metric_registry_version, headline}`을 보낸다. 값은
    `_record_trial_result`(`_service.py:1081-1092`)가 쓰는 것과 같은 계산 함수로 worker가 구한다.
  - 상태 전이, 실행 기록, 시도 원장(SQLite)은 지금처럼 부모의 `BacktestRunService`가 같은 잠금 안에서 쓴다. 완료와 취소의
    선형화(`_service.py:1271-1297`)도 그대로다. `committed`를 받은 부모는 잠금 안에서 취소 플래그를 확인한 뒤에만 완료로
    바꾸고, 취소가 이겼으면 `discard`한다.
- **대안**: (a) worker가 SQLite까지 쓴다. (b) 결과 전체를 파이프로 받아 부모가 commit한다.
- **선택 이유**:
  - (a)는 "파일 하나를 서버 프로세스 하나가 쓴다"는 SoT(`strategy-workbench-sot.md:90`)와 부팅 복구
    (`_close_interrupted_runs`, `_service.py:929-957`)를 깬다.
  - (b)는 큰 결과를 API 프로세스가 다시 파싱하게 만들어 GIL 문제를 되살린다.
- **함께 고치는 숨은 부작용**: `LocalArtifactStore.__init__`이 부팅 때 `.*.tmp` staging을 모두 지운다(`_store.py:61-73`).
  worker가 이 생성자를 부르면 다른 run의 staging을 지운다. 그래서 정리를 명시 메서드로 떼고, 부모 composition root만
  부팅 때 한 번 부른다.

### D5. 취소와 고장: 프로세스 종료가 정본, 종결 발행은 감독 스레드 하나

run마다 부모에 **감독 스레드** 하나가 있다(D12). 그 run의 종결 상태(`COMPLETED`·`CANCELLED`·`FAILED`)를 발행하는 것은
이 스레드 하나다. 순서는 다음과 같다.

1. **취소**: `cancel()`은 서비스 잠금 안에서 취소 플래그를 세우고(소유자가 모두 빠졌을 때, `_service.py:881-904`) worker
   종료(Windows `TerminateProcess`, POSIX `SIGKILL`)를 요청만 한다. 기다리지 않는다.
2. **EOF 처리**: 감독 스레드는 stdout EOF를 받으면 `wait()`로 프로세스가 끝난 것을 확인한다. 그 뒤에 그 run의
   staging(`.{run_id}.*.tmp`)과, 완료로 바뀌지 않은 결과 디렉터리를 지운다(`discard`는 멱등, `_store.py:118-122`).
   프로세스가 끝나기 전에는 지우지 않는다(살아 있는 worker의 rename과 Windows 열린 파일 경합).
3. **상태 결정**: 종결 이벤트가 없었으면 취소 플래그가 서 있을 때 `CANCELLED`, 아니면 `FAILED` + 새 코드
   `backtest.run.worker_exited`다. 메시지에는 run_id·종료 코드·마지막 단계를 싣고, stderr 원문과 서버 경로는 로그에만
   남긴다(`error-messages.md`).
4. **`committed`가 먼저 온 경우**: D4대로 잠금 안에서 플래그를 확인해 완료하거나 `discard` 뒤 `CANCELLED`다.

- worker 안의 협조적 취소 확인(`checkpoint`, duckdb interrupt 감시 스레드)은 스레드 모드에서만 쓴다. 프로세스 모드에서는
  끊을 수 없는 단계가 없어진다.
- **부모 사망**: 주 경로는 Windows Job Object(`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, 표준 라이브러리 `ctypes`)다. 부모가
  어떻게 죽든 OS가 worker를 끝낸다. 보조 경로로 worker는 stdin EOF 감시 스레드를 두고, EOF면 결과를 쓰지 않고 `os._exit`한다.
  Rust 엔진 호출이 GIL을 쥐는 동안은 감시 스레드가 늦으므로 worker는 commit 직전에 EOF 여부를 한 번 더 확인한다.
  POSIX는 `start_new_session=True`로 띄우고 EOF 경로를 쓴다. 새 부모는 부팅 복구로 그 run을 `interrupted`로 닫는다(현행과 같다).
- **서버 정상 종료**: 부모 lifespan 종료 시 자기가 띄운 worker(예비 포함)를 모두 종료하고 감독 스레드를 기다린다.

### D6. 판본 고정: 데이터·규칙·코드

- **선택**: worker는 부팅 뒤 `ready`에 판본 묶음을 싣는다. 묶음은 다음 다섯 가지다.
  - equity 어댑터의 `snapshot_id`
  - `ENGINE_RULES_VERSION`, Metric `REGISTRY_VERSION`, `FACTOR_EVALUATION_VERSION`, 팩터 레지스트리 판본
  - 코드 지문: `strategy_workbench`·`backtest_engine` 소스와 `backtest_core` 확장 파일의 해시

  부모는 같은 묶음을 자기 부팅 때 한 번 계산해 두고 비교한다. 하나라도 다르면 그 worker를 버린다. 배정된 run이면 `FAILED`
  + 새 코드 `backtest.run.worker_mismatch`로 닫는다. 메시지는 어긋난 항목과 "서버를 다시 시작하라"를 안내한다.
- **이유**: 같은 입력 잇기 키에서 snapshot·엔진 규칙·지표 레지스트리 판본을 빼는 근거는 "프로세스 안에서 고정"이라는 점이다
  (`strategy-workbench-sot.md` "백테스트 run 접수" 행). worker는 부모보다 늦게 디스크의 코드와 데이터를 읽는다. 그 사이
  `ledger_sync`, 브랜치 전환, `maturin develop`이 일어나면 부모가 매긴 지문·잇기와 다른 규칙으로 계산한다. 조용히 다른 판으로
  계산하지 않는다(`python.md` 조용한 대체 금지).

### D7. 예비 worker

- **선택**: 부모는 슬롯 수와 무관하게 `ready`까지 부팅한 예비 worker를 **항상 1개** 유지한다. 배정 때 예비에 작업을 주고 곧바로
  새 예비를 띄운다. 예비가 아직 준비되지 않았으면 그 run의 감독 스레드가 새 worker를 직접 띄운다.
  - 예비 부팅 실패나 판본 불일치는 run이 없으므로 로그만 남기고 다시 띄운다. 연속 3회 실패하면 예비 띄우기를 멈추고, 이후 배정마다
    감독 스레드가 새 worker를 띄운다. 그 부팅이 실패하면 그 run은 `worker_exited`(부팅 실패), 판본 불일치면 `worker_mismatch`다.
- **이유**: mock run은 1초 안에 끝나는데 부팅이 3~6초다. 예비가 없으면 e2e와 실험 trial이 run마다 그만큼 늘어난다. 실험 trial은
  슬롯 1개를 연달아 쓰므로 부팅이 직렬로 쌓인다. `browser-e2e` job은 이미 88개 테스트에 약 18분을 쓰고 30분 제한 아래 있다
  (`docs/planning/validation-lab/V5-06-capacity-factor-reports.md:68-99`). 유휴 worker 하나의 RSS는 118MB다.

### D8. 슬롯과 메모리 가드

- **선택**: 슬롯 의미(`STRATEGY_WORKBENCH_RUN_SLOTS`, 기본 2, 단일 실행 전용 1개)는 바꾸지 않는다(US-SM-11·US-SM-15).
  대기열에서 run을 꺼내기(`pop`) **전에** 다음을 판정하고, 거짓이면 슬롯이 비어 있어도 대기열에 둔다.

  `가용 물리 메모리 − Σ(도는 run마다 max(0, 예약치 − 그 worker의 현재 RSS)) ≥ 예약치`

  - 도는 run의 남은 몫을 빼므로, 막 시작해 아직 작은 run이 곧 쓸 메모리도 센다. 연달아 들어온 두 run이 함께 통과하지 않는다.
  - `pop` 전에 판정하므로 레인 가중치 계수가 흐려지지 않는다.
  - 예약치는 `STRATEGY_WORKBENCH_RUN_MEMORY_RESERVE_GB`로 정하고, 기본은 보수적으로 6GB다(5년 실데이터 run 하나의 최대 RSS
    5.5GB를 올림). 16GB PC에서는 긴 구간 run 둘이 함께 돌지 못하게 되는데, 의도한 동작이다. 짧은 구간에는 지나치게 보수적이다. tape
    열 지향화로 run 메모리가 줄면 예약치를 다시 정한다(10절).
  - 도는 run이 하나도 없으면 가드를 적용하지 않는다. 적어도 하나는 돈다(굶주림 없음).
  - worker는 종결 이벤트에 최대 RSS를 싣고, 부모는 로그에 남긴다.
- **이유**: 프로세스를 나눠도 메모리는 줄지 않는다. 16GB PC에서 5년 run 둘이 겹치면 페이징으로 둘 다 느려진다
  (DEFECT-RW-02의 일부 원인으로 추정). 슬롯 수를 메모리로 자동 결정하면 화면의 "n / 2" 계약과 단일 실행 전용 슬롯
  스토리가 기계마다 달라지므로, 슬롯은 두고 배정만 늦춘다. 슬롯이 비었는데 기다리는 run은 `queued`로 보인다. US-SM-15 비고에
  이 사실을 적는다(W2-02).

### D9. 실행 방식 설정과 기본값

- `STRATEGY_WORKBENCH_RUN_ISOLATION=process|thread`. W1에서 `thread`를 기본으로 들어오고, W3에서 `process`로 바꾼다.
- `thread`는 없애지 않는다. 같은 계산 함수(D10)를 같은 프로세스에서 부르는 전송 방식일 뿐이다. 서비스 상태 기계의 단위 테스트와
  디버깅·프로파일링에 쓴다. 전송 방식이 달라도 결과는 같아야 한다(4절 검증 기준 V-3).

### D10. 계산의 단일 정본

- run 계산은 `application/backtest_run` 안의 함수 하나가 소유한다. 스레드 모드와 worker가 같은 함수를 부른다. 팩터 값 계산의 정본이
  `portfolio_design/_service.py` 하나라는 규칙도 그대로다. 계산 함수가 소유하는 것은 다음과 같다.
  - 벤치마크 확인 → `run_pipeline` → `load_backtest_dataset` → `executor.execute` → artifact commit → 시도 요약 계산
  - 실패 분류 `_failure_code`·`_describe_failure`
  - 진행률 구간 상수(tape 0.02~0.8, data 0.82, engine 0.84~0.92, artifact 0.93)
- 서비스(`BacktestRunService`)는 접수·대기열·슬롯·잇기·공유 취소·상태 전이·완료와 취소 선형화·부팅 복구를 계속 소유한다.
- concrete 선택은 `bootstrap`만 한다(`backend-package-boundary.md`). 새 노드(`run_thread`, `run_worker_process`, `bootstrap/run_worker`)를
  더하는 PR이 같은 PR에서 그 규칙 문서에 노드를 적는다.
- worker 쪽 composition root는 `bootstrap/run_worker`다. 부모가 해석한 설정(equity 어댑터 종류·루트, 산출물 루트, hold 여부)을 job으로
  받는다. worker는 환경 변수를 다시 해석하지 않는다(설정 해석의 정본은 `bootstrap/_http.py`). equity 어댑터와 실행기는 `bootstrap/_container.py`의
  생성 함수를 재사용해 만들고, 목록(`EQUITY_ADAPTERS`)을 복제하지 않는다. worker는 `BacktestRunService`를 만들지 않는다. 만들면 부팅 복구가
  부모의 도는 run을 닫는다(`_service.py:368`).

### D11. e2e hold 훅: 판정은 부모, 대기는 worker

- `_TrialHold`(`bootstrap/_container.py:461-488`)의 hold 대상 집합은 `start`가 돌아온 뒤에 채워진다(`_container.py:399-416`). 그래서 job을
  쓰는 시점에는 이 run이 hold 대상인지 알 수 없다. 프로세스 모드에서는 다음 순서로 판정을 엔진 단계 앞으로 미룬다.
  1. hold 기능이 켜져 있으면(`trial_hold_seconds > 0`, e2e 전용) 부모는 job에 `engine_gate: true`를 싣는다.
  2. worker는 엔진 단계 앞에서 `engine_ready`를 보내고 `proceed`를 기다린다.
  3. 부모의 감독 스레드가 지금과 같은 규칙(집합에 있고 기한 전이면 0.05초 간격으로 기다리고, 취소되면 끝)으로 판정한 뒤 `proceed`를 보낸다.
- hold가 꺼져 있으면 왕복이 없다. 실험이 나중에 이은 사용자 run도 붙잡는 지금 의미가 그대로다. `STRATEGY_WORKBENCH_E2E_TRIAL_HOLD_SECONDS`의 뜻은
  바뀌지 않는다.

### D12. 잠금 규율

- 서비스 잠금(`self._lock`) 안에서는 장부 갱신과 감독 스레드 시작만 한다. `Popen`, 파이프 읽기·쓰기, `ready` 대기, 프로세스 `wait`, 파일 정리를
  잠금 안에서 하지 않는다. 배정(`_dispatch`)은 잠금 안에서 불리므로(`_service.py:1094-1131`, 호출처 `:427`·`:469`·`:596`·`:886`·`:1147`), job 쓰기와
  `ready` 대기는 그 run의 감독 스레드가 맡는다.
- 감독 스레드는 진행을 `_update`로 넘긴다. 취소 중이면 `_update`가 `RunCancelledError`를 낸다(`_service.py:1368-1369`). 감독 스레드는 이 예외를 삼키고
  파이프를 끝까지 읽는다. 읽기를 멈추면 worker가 쓰기에서 막힌다.
- 검증: 부팅 중인 worker에 큰 job을 보내는 동안에도 `GET /health`가 막히지 않는다(W1-02 acceptance).

### D13. `RunExecutionPort` 계약

`application/backtest_run/ports/outgoing`의 포트가 "run 하나를 어딘가에서 돌리고 진행·종결을 알려 달라"를 표현한다.
adapter는 둘이다. `adapters/outbound/run_thread`(현행 동작)와 `adapters/outbound/run_worker_process`(이 문서)다.

- `start(job, listener) -> handle`
  - 잠금 안에서 불려도 막히지 않는다. 스레드 시작까지만 하고 돌아온다.
  - `job`은 run_id, `ResolvedBacktestRunSpec`, 출처, 계열, hold 여부를 담는다.
- `handle.cancel()`
  - 막히지 않는다. 스레드 adapter는 협조적 취소 신호를 세우고, 프로세스 adapter는 종료를 요청한다.
- listener 콜백 `on_progress(stage, fraction, message)`, `on_finished(outcome)`
  - adapter의 감독 스레드에서 불리고, 서비스 잠금을 잡지 않은 상태로 호출된다.
  - `outcome`은 셋 중 하나다: 완료(sha256과 시도 요약), 실패(코드와 문장), 취소.
  - 완료와 취소의 선형화는 서비스가 `on_finished` 안에서 잠금을 잡고 한다.
- `shutdown()`: 서버 종료 때 도는 작업과 예비를 정리한다.

## 4. 검증 기준(완료 정의)

측정은 [BENCHMARK.md](../../planning/run-worker-process/BENCHMARK.md)의 절차와 같은 기계·같은 전략으로, 워밍업 1회를 버리고 한다.
홀드아웃 규칙에 따라 구간 시작은 2020-01-02 이후다.

| ID | 기준 | 현재(스레드) |
|---|---|---|
| V-1 | run 1개가 도는 동안 `GET /health` p95 ≤ 50ms, 최대 ≤ 250ms | p95 721ms, 최대 1,421ms(1년). 5년은 p95 1,450ms, 최대 10,103ms. 시제품은 p95 2.4ms |
| V-2 | 1년 구간 run 2개 동시 실행의 벽시계 시간 ≤ 혼자 실행 시간 × 1.25 | 1.86배(83.1초 대 44.6초). 시제품은 1.06배 |
| V-3 | 같은 요청을 두 방식으로 돌리면 `run_fingerprint`·`target_tape_hash`·지표·산출물이 같다(run_id·시각 제외) | — |
| V-4 | 취소 요청부터 `cancelled`까지 어느 단계에서든 ≤ 2초 | data·engine 단계는 끝날 때까지 |
| V-5 | 예비 worker가 있을 때 배정부터 첫 `tape` 진행까지 ≤ 0.5초 | — |
| V-6 | 실데이터 run 10개를 차례로 돈 뒤 서버 프로세스 private commit 증가 ≤ 200MB | run 4개 뒤 1.67GB 잔류 |
| V-7 | 전체 `npm run test:e2e`(Windows) 시간 증가 ≤ 5% | 약 17.9분 |

## 5. 바깥 계약(바꾸지 않는 것)

- HTTP API·OpenAPI·생성 SDK: `RunStatus`·`RunStage` 어휘, 진행률 구간(tape 2~80%, data 82%, engine 84~92%, artifact
  93%), 폴링 응답, `/events` SSE, `/cancel` 응답의 `kept_by_owners`, `GET /experiments`의 `slots`.
- 실행 오류 코드에는 두 개만 더한다: `backtest.run.worker_exited`(부팅 실패·비정상 종료), `backtest.run.worker_mismatch`(D6 판본 불일치). `RunFailureCode`
  (`domain/backtest/_models.py:654-665`)가 어휘 SoT다. frontend `backtest.run.error.*` 번역 두 개를 같은 PR에서 더하고,
  OpenAPI·SDK 재생성은 별도 커밋으로 넣는다.
- 같은 입력 잇기, 공유 취소, 대기 순서, 단일 실행 전용 슬롯, 재시작 복구(`interrupted`), 실험 trial 재대기.
- 결과 파일 형식(`backtest-run-v3`), 산출물 해시 검증, 시도 원장 기록 시점(완료와 같은 내구 경로, #381).

## 6. 하지 않는 것(non-goal)

- run 하나의 계산 시간 단축(tape 벡터화, 메모리 표현 변경). 10절 후속으로 둔다.
- 대화형 무거운 경로(`/strategies/debug/trace`, `/portfolio/preview`, `/equity/panel/preview`, 연속 계좌 평가)의 이전.
  W5에서 측정한 뒤 결정한다.
- 여러 기계에 나눠 돌리기, 외부 브로커, 원격 worker.
- 슬롯 수의 메모리 기반 자동 결정(D8).

## 7. 함께 고칠 SoT·규칙

- `.claude/rules/strategy-workbench-sot.md`
  - 75행: 실험 남은 시간 어림의 "run 이 한 프로세스의 스레드" 전제를 프로세스 모드에 맞게 고친다. 병렬로 나눌지는 V-2
    측정 값으로 정한다.
  - 87행: 끊을 수 없는 호출의 한계를 프로세스 모드에서 지운다.
  - 90행: run 기록 소유와 메모리 사본 행에 worker는 결과 파일만 쓴다는 점을 더한다.
  - "백테스트 run 접수" 행: 데이터 판 고정을 D6으로 고친다.
- `docs/superpowers/specs/2026-09-29-validation-lab-design.md` D6과 non-goal에 개정 링크를 단다(원문은 남긴다).
- `backend/README.md` 실행 모델 절, `README.md` 실행 절에 `STRATEGY_WORKBENCH_RUN_ISOLATION`·
  `STRATEGY_WORKBENCH_RUN_MEMORY_RESERVE_GB`를 더한다.
- `.claude/rules/testing.md`: 하위 프로세스가 여는 파일은 감사 훅이 보지 못하므로, worker 통합 테스트가 경로를 직접 격리한다는
  예를 더한다.
- `.claude/rules/backend-package-boundary.md`: 새 노드(`run_thread`, `run_worker_process`, `bootstrap/run_worker`)는 그 노드를 더하는
  PR이 같은 PR에서 적는다(D10).
- `docs/product/user-stories/stories/sm.md` US-SM-15 비고: 메모리 가드로 슬롯이 비어도 `queued`로 기다릴 수 있다(W2-02).

## 8. 위험과 완화

| 위험 | 완화 |
|---|---|
| Windows에서만 생기는 프로세스·파이프·인코딩 문제가 Linux CI에서 안 보인다 | W1-02에서 worker 통합 테스트만 도는 Windows backend job을 더한다(opt-in 단계부터). W3-01부터는 `browser-e2e`(Windows)도 기본 모드로 돈다 |
| 고아 worker가 메모리를 쥐고 남는다 | Windows Job Object(주), stdin EOF 감시(보조), lifespan 종료 정리, 부팅 시 같은 산출물 루트의 staging 정리(D5) |
| worker 부팅 실패(카탈로그 잠금 `catalog_locked`, extra 누락)가 run마다 반복된다 | D7의 예비 부팅 실패 정책(로그·재시도·연속 3회면 중단, 배정 시 직접 부팅 실패는 그 run만 `worker_exited`) |
| 진행 이벤트 폭주가 부모 GIL을 다시 잡는다 | D3의 0.1초 상한. 부모 감독 스레드는 줄 파싱과 `_update` 호출만 한다. 시제품의 API p95 2.4ms는 부모가 진행 줄을 파싱하지 않는 유휴 서버 값이라, W3-01 재측정이 실제 값이다 |
| 서비스 잠금 안의 블로킹 IPC가 API를 멈춘다 | D12 잠금 규율과 W1-02 acceptance(부팅 중 큰 job 전송 동안 health 무지연) |
| 두 모드의 결과가 갈린다 | V-3를 통합 테스트(mock)와 실데이터 확인(BENCHMARK)에 둔다 |

## 9. 롤백

`STRATEGY_WORKBENCH_RUN_ISOLATION=thread`로 즉시 현행 동작으로 돌아간다. W3의 기본값 전환 커밋만 되돌리면 기본값도 돌아간다.
결과 파일과 SQLite 스키마는 바뀌지 않으므로 데이터 이전은 없다.

## 10. 후속(이 initiative 밖)

- **tape 열 지향화(가장 큰 속도 개선)**: run 시간의 대부분은 계산이 아니라 칸마다 Python 객체를 만들고 옮기는 비용이다
  (BENCHMARK 4절). 시제품은 6.7년 구간 tape를 247초에서 약 3.4초로 줄였고, 80개 프레임 선정이 모두 실제와 같았다(BENCHMARK 5절).
  numpy와 pyarrow는 이미 의존성이라 새 라이브러리가 없다. 다만 시제품은 이 전략의 연산(필드·모멘텀·상위 비율·상위 N)만 다뤘다.
  전 언어로 넓히려면 다음을 같은 의미로 옮겨야 한다.
  - 횡단면 연산, 절대 규칙, 섹터 제약, 파라미터
  - 공개일·lag, 결측 정책
  - trace 화면이 쓰는 프레임별 후보 판정(탈락 사유)
  - tape 해시 정본화(같은 비트)

  팩터 값 계산의 정본(`portfolio_design`)을 바꾸는 별도 initiative다. 메모리도 함께 줄어 D8 예약치를 낮출 수 있다.
  두 구현을 나란히 돌려 tape 해시가 같은지 확인하는 대조 하네스를 먼저 둔다.
- **GPU**: run 하나에는 손해다(CuPy가 numpy보다 2~3배 느리다. 전송·초기화 비용 때문이다). 데이터를 GPU에 둔 대량 파라미터 스윕에서만
  35~60배 이득이다. 열 지향화 뒤에 실험의 팩터 평가 단계에 한해 선택 사항으로 검토한다. Windows에서는 CuPy만 쓸 수 있다(cuDF는 WSL2 필요).
- **작은 개선**: 재검증 생략 1.07배, GC 끄기 1.04배, Python 3.13 1.11배(tape 해시가 바뀌므로 엔진 규칙 판본 올림이 필요하다). 열 지향화를
  하면 대부분 의미가 없어지므로 따로 진행하지 않는다.
- **factor-regression 계산**(W4)도 worker로 옮긴 뒤에는, 대화형 경로(W5)를 같은 worker 기반 위에 올릴지 측정으로 정한다.
