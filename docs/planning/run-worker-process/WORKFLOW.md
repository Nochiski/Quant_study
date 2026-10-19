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
- 라이브 서버 DB를 건드리지 않는다. 임시 디렉터리 격리(`build_http_app`에 경로를 명시)를 쓴다.
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

**Non-goal**: 취소, 고장, 예비 worker, 기본값 변경.

### W1-03 — 취소, 고장, 데이터 판본

**Intent**: spec D5·D6·D11.

**Acceptance**:
- 취소: tape·data·engine·artifact 각 단계에서 취소하면 2초 안에 `cancelled`가 된다(V-4). staging과 결과 디렉터리가 남지 않는다.
  완료와 취소가 경합해도 현행 선형화 테스트(`test_cancel_accepted_during_artifact_commit_wins_and_exact_request_replays`)가
  프로세스 모드에서도 통과한다.
- worker 강제 종료(테스트가 kill)는 `backtest.run.worker_exited`로 닫는다. 부모가 사라지면(stdin을 닫으면) worker가 스스로 끝난다.
- snapshot이 다르면 `backtest.run.data_snapshot_changed`로 닫는다.
- e2e hold(`hold_seconds`)가 프로세스 모드에서도 같은 의미로 동작한다.
- 실패 코드 두 개를 `RunFailureCode`에 더하고, OpenAPI·SDK 재생성 커밋과 frontend `backtest.run.error.*` 번역을 함께 넣는다.

**Non-goal**: 예비 worker, 메모리 가드.

## 5. Phase W2 — 지연과 메모리

### W2-01 — 예비 worker

**Intent**: spec D7. 배정 지연(V-5)과 e2e 시간(V-7)을 지킨다.

**Acceptance**:
- 빈 슬롯이 있으면 예비 worker 하나를 `ready`까지 띄워 둔다. 쓰면 바로 새 예비 worker를 띄운다.
- 예비 worker 부팅 실패 정책(spec 8절)을 따른다. 서버 종료와 reload 때 예비 worker도 정리된다.
- 프로세스 모드로 전체 `npm run test:e2e`를 돌린 시간을 스레드 모드와 비교해 PR 본문에 남긴다.

### W2-02 — 메모리 가드

**Intent**: spec D8.

**Acceptance**:
- 가용 물리 메모리가 예약치보다 작으면, 슬롯이 비어도 배정하지 않고 대기열에 둔다. 도는 run이 없으면 가드를 적용하지 않는다.
- worker가 보고한 최대 RSS를 로그에 남긴다.
- 가용 메모리 조회는 부모의 작은 adapter 하나가 소유한다. Windows는 `GlobalMemoryStatusEx`, Linux는 `/proc/meminfo`를 쓰고, 새
  라이브러리는 들이지 않는다.
- 테스트는 조회를 주입해 대기·해제 순서를 확인한다.

## 6. Phase W3 — 기본값

### W3-01 — 기본값 전환, Windows job, SoT 개정

**Intent**: `process`를 기본으로 바꾸고, 문서와 규칙을 한 PR에서 맞춘다(spec 7절).

**Acceptance**:
- BENCHMARK 재측정으로 V-1, V-2, V-4, V-5, V-6을 확인하고 숫자를 남긴다.
- CI에 Windows backend job을 더한다. worker 통합 테스트만 돌리며 예산은 10분 이하다.
- `strategy-workbench-sot.md` 75·87·90행과 "백테스트 run 접수" 행, validation-lab spec D6 개정 링크, `backend/README.md`, `README.md`를
  고친다.
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
