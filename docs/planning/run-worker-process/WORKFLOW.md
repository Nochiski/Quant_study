# run 별도 프로세스 실행 WORKFLOW

> For agentic workers: 이 파일은 PR 단위 scope packet과 공통 gate의 정본이다. 계약은
> [설계 spec](../../superpowers/specs/2026-10-19-run-worker-process-design.md), 진행 상태는 [PLAN.md](./PLAN.md)가 소유한다.

- **Goal**: 백테스트 run을 API 프로세스의 스레드에서 run마다 하나씩 뜨는 worker 프로세스로 옮긴다. 목표는 API 응답성,
  동시 run 처리량, 즉시 취소, 메모리 회수다(spec 1~2절).
- **Architecture**: `BacktestRunService`(접수·대기열·상태 전이)는 부모에 남는다. 계산은 `application/backtest_run`의
  함수 하나(spec D10)로 모으고, 그 함수를 어디서 돌릴지만 outgoing port `RunExecutionPort`로 고른다
  (`run_thread` | `run_worker_process`). worker composition root는 `bootstrap/run_worker`다.
- **Tech Stack**: Python 3.11 표준 `subprocess`·`threading`·`json`. 새 라이브러리는 없다. 측정 도구만 `psutil`을
  `uv run --with`로 임시 사용한다(저장소 의존성 아님).

## 1. 실행 순서와 스택

```
W0-01 기획 패키지·측정 도구
  └─ W1-01 계산 경계 추출(동작 불변)
       └─ W1-02 worker 진입점·IPC·프로세스 adapter(opt-in)
            └─ W1-03 취소·고장·판본 경로
                 └─ W2-01 예비 worker
                      └─ W2-02 메모리 가드
                           └─ W3-01 기본값 전환·Windows job·SoT 개정
                                └─ W4-01 factor-regression 계산 이전
                                     └─ W5-00 대화형 경로 결정 게이트(측정만)
                                          └─ W9-01 종료 리뷰
```

- PR ID는 `W<phase>-<nn>`이다. 스택 PR은 직전 PR 브랜치를 base로 연다. 장기 통합 브랜치는 만들지 않는다(`pr-review.md`).
- 각 PR은 diff 150~450줄을 권장한다. 600줄이나 파일 10개를 넘으면 나눈다
  (`docs/planning/strategy-workbench-yaml-ui/WORKFLOW.md:1012-1022`).
- 다른 흐름이 같은 파일(`_service.py`, `_container.py`)을 고치고 있으면, 머지 순서는 리드가 정하고 이 스택은 rebase한다.
- tape 열 지향 트랙([columnar-tape WORKFLOW](../columnar-tape/WORKFLOW.md))과의 순서:
  - W1-02는 T0-02(상한 배분 결정성, #571) 머지 뒤에 연다. V-3이 해시 seed 비결정성에 걸리면 안 된다.
  - W5-00 측정은 T6-01 뒤에 한다.
  - W2-02 메모리 추정식 계수는 T4-03 측정 뒤에 다시 맞춘다. 그때 열리는 첫 W PR(늦으면 별도 후속 PR)이 맡는다.

## 2. 공통 gate

| 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run pytest -q`(backend 전체) → `uv run pytest database/tests -q` → `ruff check`·`ruff format --check` → `pyright`(`uv sync --all-extras` 뒤 `maturin develop --release`) |
| API 어휘 변경(W1-03) | `npm run api:generate` 결과를 별도 커밋으로. frontend `typecheck`·`lint`·`test` |
| 기본값·e2e 영향(W2-01, W3-01) | 전체 `npm run test:e2e`(Windows, 다른 e2e와 동시 실행 금지 — 포트 오염) |
| 성능 주장 | [BENCHMARK.md](./BENCHMARK.md) 절차로 다시 재고, 숫자를 PR 본문과 BENCHMARK에 남긴다. 워밍업 1회를 버리고, 측정 중 다른 무거운 작업(테스트·빌드)을 돌리지 않는다 |
| 실데이터 확인 | 로컬 원장(duckdb 어댑터)으로 1년·최장 허용 구간(2020-01-02~) run이 두 모드에서 같은 결과를 내는지 본다(V-3). 2020-01-02 이전 구간은 열지 않는다(`holdout-window.md`) |

리뷰어 프롬프트 체크리스트(사용자 우선순위):

1. 최소 코드량. 이 PR 범위 밖 리팩터링이 없는가.
2. SoT. 계산 경로가 하나인가(spec D10). 설정 해석이 `bootstrap/_http.py` 한 곳인가. 실패 코드 어휘가 `RunFailureCode` 한 곳인가.
3. 책임 분리. 서비스는 상태 기계만, adapter는 전송만, worker composition root는 조립만 하는가. application이
   concrete adapter를 import하지 않는가(`test_dependency_direction.py`).
4. 안정성·잠재 버그. 고아 프로세스, 파이프 교착(stdout을 읽지 않아 버퍼가 차는 경우), 인코딩, 완료와 취소의 경합,
   staging 누수.
5. 하네스. 사용자 동작이 바뀐 PR이면 스토리·e2e 태그를 갱신했는가.

Phase가 끝날 때마다 작업분 SoT·책임 분리 점검을 하고, W9-01에서 backtest_run 도메인 전체와 frontend·backend 경계 리뷰를
따로 돌린다.

## 3. Phase W0 — 기획

### W0-01 — 기획 패키지와 측정 도구

**Intent**: 이 패키지(spec, README, PLAN, WORKFLOW, BENCHMARK)를 들이고, 측정을 누구나 같은 절차로 재현할 수 있게 한다.

**Acceptance**:
- `backend/scripts/bench_run_isolation.py` 하나를 둔다. BENCHMARK.md의 표를 다시 만든다(스레드 1·2, 프로세스 1·2, 구간 1년·최장 허용).
  실행은 `uv run --no-sync --with psutil python scripts/bench_run_isolation.py`다.
- 라이브 DB를 건드리지 않는다. process 모드는 임시 디렉터리 격리(`build_http_app`에 경로를 명시)를 쓰고, thread 모드는 DB·산출물
  경로를 임시 디렉터리로 준 측정 전용 서버를 대상으로 한다(BENCHMARK 절차).
- 홀드아웃 하한(2020-01-02) 밖 구간을 인자로 받으면 거절한다.
- `tools/quant_study_dev/plan_progress.py`의 `PROFILES`에 `run-worker-process`를 더하고, `--check`를 통과한다.

**Non-goal**: 제품 코드 변경.

## 4. Phase W1 — 경계와 worker

### W1-01 — 계산 경계 추출(동작 불변)

**Intent**: `_execute`(`_service.py:1149-1337`)에서 계산 부분을 떼어, 스레드 모드와 worker가 함께 부를 함수 하나로 만든다.

**Acceptance**:
- `application/backtest_run/_computation.py`(가칭)의 `compute_run(deps, job, *, progress, cancelled) -> ComputedRun`이 다음을 소유한다.
  - 벤치마크 확인, `run_pipeline`, tape 해시 대조, `load_backtest_dataset`, 경고 합치기, `executor.execute`, artifact commit
  - 시도 요약(`representative_sharpe`·`run_headline`)
  - 실패 분류(`_failure_code`·`_describe_failure`)를 값으로 돌려준다. 예상한 실패는 Result 값이다(`python.md`).
- `BacktestRunService`는 접수, 대기열, 상태 전이, 완료·취소 선형화, durable 저장, 시도 원장 기록만 남긴다.
- `RunExecutionPort`와 `adapters/outbound/run_thread`(현행 스레드)를 두고, `bootstrap`이 주입한다.
- `LocalArtifactStore`의 staging 정리를 명시 메서드로 떼고, 부모 composition root가 부팅 때 부른다(spec D4).
- 기존 backend 테스트가 수정 없이 전부 통과한다. 테스트를 고쳐야 한다면, 그 이유가 "스레드 이름·`_service.Thread`
  monkeypatch처럼 전송 세부에 묶인 단언"일 때만 고치고, 그 목록을 PR 본문에 적는다.

- 계약은 spec D13(`start`·`cancel`·listener·`shutdown`)이고 잠금 규율은 D12다. 진행률 구간 상수는 계산 함수 모듈이 소유한다(D10).
- 새 노드 `run_thread`를 `.claude/rules/backend-package-boundary.md`에 적는다.

**Non-goal**: 프로세스, 동작 변경, API 변경.

**예상 파일**: `_service.py`, `_computation.py`(새), `ports/outgoing/run_execution.py`(새),
`adapters/outbound/run_thread/`(새), `artifact_local/_store.py`, `bootstrap/_container.py`, 관련 테스트.

### W1-02 — worker 진입점, IPC, 프로세스 adapter(opt-in)

**Intent**: spec D2·D3·D10대로 `python -m strategy_workbench.bootstrap.run_worker`와 `adapters/outbound/run_worker_process`를 둔다.
`STRATEGY_WORKBENCH_RUN_ISOLATION=process`일 때만 쓴다.

**Acceptance**:
- 프로토콜 v1(job, ready, progress, committed, failed)의 인코딩·디코딩이 한 모듈에 있다. 버전이 다르면 양쪽 모두 거절한다.
- job은 `to_wire()` 표현으로 넘긴다. worker는 `ResolvedBacktestRunSpec`을 다시 만들고, tape 해시 대조로 동일성을 확인한다.
- 부모 리더 스레드는 stdout과 stderr를 모두 끝까지 읽는다(파이프 버퍼가 차서 생기는 교착 방지).
- 진행 이벤트 상한은 0.1초다.
- 통합 테스트(mock 어댑터, 실제 하위 프로세스):
  - 완료까지의 수명 주기
  - 진행률 단조 증가
  - V-3 결과 동일성: 같은 요청을 두 모드로 돌려 지문, tape 해시, 지표가 같다
  - 한글 메시지 왕복
- 하위 프로세스 경로는 테스트가 직접 격리한다(`testing.md`).
- 잠금 규율(D12): 부팅 중인 worker에 큰 job을 보내는 동안에도 `GET /health`가 막히지 않는다(테스트로 확인).
- CI에 Windows backend job을 더한다. worker 통합 테스트만 돌리며 예산은 10분 이하다(opt-in 단계부터 Windows 경로를 밟는다).
- 새 노드 `run_worker_process`·`bootstrap/run_worker`를 `backend-package-boundary.md`에 적는다.

**Non-goal**: 취소, 고장, 예비 worker, 기본값 변경.

### W1-03 — 취소, 고장, 판본

**Intent**: spec D5·D6·D11. 종결 발행은 감독 스레드 하나가 맡는다(D5 순서 1~4).

**Acceptance**:
- 취소: tape·data·engine·artifact 각 단계에서 취소하면 2초 안에 `cancelled`가 된다(V-4). staging과 결과 디렉터리가 남지 않는다.
  완료와 취소의 경합은 부모 쪽 장치로 재현한다. 감독 스레드가 `committed`를 받은 뒤 잠금을 잡기 전 barrier에서 취소를 넣고,
  결과가 `cancelled`이며 결과 디렉터리가 지워졌는지 본다. worker 안의 commit에는 같은 프로세스 barrier를 걸 수 없기 때문이다
  (현행 `_CommitBarrierStore`, `tests/integration/test_backtest_http_api.py:279`).
- worker 강제 종료(테스트가 kill)는 `backtest.run.worker_exited`로 닫고, 이미 rename된 결과 디렉터리를 지운다.
- 부모 사망: 중간 부모 프로세스가 worker를 띄운 뒤 그 부모를 실제로 kill하면 worker가 끝난다(Windows Job Object, POSIX EOF).
  stdin을 닫는 것만으로 대신하지 않는다.
- 판본 묶음(D6: snapshot·엔진 규칙·지표 레지스트리·팩터 평가·팩터 레지스트리·코드 지문)이 다르면 `backtest.run.worker_mismatch`로 닫는다.
- 서버 정상 종료(lifespan) 때 도는 worker가 정리된다.
- e2e hold가 프로세스 모드에서도 같은 의미로 동작한다(D11 `engine_ready` → 부모 판정 → `proceed`). 실험이 나중에 이은 사용자 run도 붙잡힌다.
- 실패 코드 두 개를 `RunFailureCode`에 더하고, OpenAPI·SDK 재생성 커밋과 frontend `backtest.run.error.*` 번역을 함께 넣는다.
- #575 리뷰 P3: 부모가 부팅 때 한 번 계산하는 판본 묶음(코드 지문 포함)의 비용을 재서 PR 본문에 남긴다.

**Non-goal**: 예비 worker, 메모리 가드.

## 5. Phase W2 — 지연과 메모리

### W2-01 — 예비 worker

**Intent**: spec D7. 배정 지연(V-5)과 e2e 시간(V-7)을 지킨다. V-7은 이 PR의 gate다.

**Acceptance**:
- 슬롯과 무관하게 `ready`까지 부팅한 예비 worker를 항상 1개 둔다. 쓰면 바로 새 예비를 띄운다.
- 예비 부팅 실패·판본 불일치 정책은 spec D7을 따른다. 서버 종료와 reload 때 예비 worker도 정리된다.
- 프로세스 모드 전체 `npm run test:e2e`(Windows) 시간이 스레드 모드 대비 5% 이하로 늘어난다(V-7). 두 시간을 PR 본문에 남긴다.

### W2-02 — 메모리 가드

**Intent**: spec D8.

**Acceptance**:
- spec D8의 식(가용 − Σ 도는 run의 남은 예약분 ≥ 새 run의 예약치)을 대기열에서 꺼내기 전에 판정한다. 도는 run이 없으면 가드를 적용하지 않는다.
- 예약치는 세션 수 추정식(spec D8) 하나가 소유하고, 접수 때 데이터를 읽지 않고 정한다. 환경 변수로 고정값을 덮어쓸 수 있다.
- 연달아 접수한 두 run 가운데 둘째가 가드에 걸리는지 테스트로 확인한다(첫 run의 RSS가 아직 작을 때).
- 실험 trial 처리량(가드 전후, 1년 구간 실험)을 재서 PR 본문에 남긴다.
- 최대 RSS 로그에 읽은 세션 수·필드 수·팩터 수·유니버스 행 수를 함께 남긴다. 추정식(세션 수 하나)을 다시 맞출 때 다른 전략 모양의 근거가 된다.
- US-SM-15 비고에 "메모리가 모자라면 슬롯이 비어도 `queued`로 기다린다"를 적는다.
- worker가 보고한 최대 RSS를 로그에 남긴다.
- #575 리뷰 P3: worker의 최대 RSS 코드(`run_worker_process/_worker.py`의 `peak_rss_bytes`, ctypes·`resource`)를 가용 메모리 adapter와 같은 곳으로 모은다.
- 가용 메모리 조회는 부모의 작은 adapter 하나가 소유한다. Windows는 `GlobalMemoryStatusEx`, Linux는 `/proc/meminfo`를 쓰고, 새
  라이브러리는 들이지 않는다.
- 테스트는 조회를 주입해 대기·해제 순서를 확인한다.

## 6. Phase W3 — 기본값

### W3-01 — 기본값 전환, Windows job, SoT 개정

**Intent**: `process`를 기본으로 바꾸고, 문서와 규칙을 한 PR에서 맞춘다(spec 7절).

**Acceptance**:
- BENCHMARK 재측정으로 V-1, V-2, V-4, V-5, V-6, V-7을 확인하고 숫자를 남긴다(부모가 진행 줄을 파싱하는 실제 제품 경로·기본 설정으로 잰다).
  V-2 측정에는 배정 시점의 가용 메모리와 메모리 가드 판정(대기 여부)을 함께 남겨, 실패했을 때 병렬성 문제인지 가드인지 가른다.
- `strategy-workbench-sot.md` 75·87·90행과 "백테스트 run 접수" 행, validation-lab spec D6 개정 링크, `backend/README.md`, `README.md`,
  `.claude/rules/testing.md`(하위 프로세스 경로 격리 예)를 고친다.
- 실험 남은 시간 어림을 슬롯 병렬로 나눌지는 V-2 결과로 정한다. 사용자 문구가 바뀌면 US-SM-15 수용 기준과 e2e를 고친다.
- 전체 e2e가 green이다.

## 7. Phase W4 — 다른 백그라운드 계산

### W4-01 — factor-regression 계산 이전

**Intent**: `factor_regression`의 백그라운드 스레드(`_service.py:289-290`, `757-838`)가 유니버스 전체 `run_pipeline`을 API 프로세스에서
돌린다(실데이터 약 2분). 이 계산을 같은 worker 기반의 두 번째 job 종류로 옮긴다.

**Acceptance**: API 응답 V-1 기준을 factor-regression 계산 중에도 지킨다. 결과 캐시(LRU 32)와 한 번에 하나 규칙은 부모가 계속 소유한다.

## 8. Phase W5 — 결정 게이트

### W5-00 — 대화형 무거운 경로 측정

**Intent**: `/strategies/debug/trace`, `/portfolio/preview`, `/equity/panel/preview`, 연속 계좌 평가가 API 응답에 주는 영향을 같은 방법으로 잰다.
worker 기반에 올릴지 리드가 정한다. 이 PR은 측정과 결정 기록만 하고 코드는 바꾸지 않는다.

## 9. Phase W9 — 종료

### W9-01 — 종료 리뷰

backtest_run 도메인 전체 리뷰, frontend·backend 경계 책임 분리 리뷰, SoT 감사를 따로 돌린다. 지적 사항은 이슈로 남기거나 이
Phase에서 고친다.

## 10. Backlog

- tape 벡터화(spec 10절). run 하나의 시간과 메모리를 줄이는 별도 initiative다.
- Rust `PersistentEngine.drive`의 GIL 해제. 프로세스 모드에서는 이득이 없어 보류한다. 스레드 모드 성능이 다시 문제가 되면 다룬다.
