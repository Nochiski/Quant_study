# 검증 랩 구현 워크플로우

> **For agentic workers:** PR마다 [YAML Strategy Workbench WORKFLOW 13절](../strategy-workbench-yaml-ui/WORKFLOW.md)
> 절차(scope packet → self-check → diff freeze → Opus reviewer 1명 → 재검토 → APPROVE → merge)를 지킨다.
> 진행 상태는 [PLAN.md](./PLAN.md)만 갱신한다. 계약은 [설계 spec](../../superpowers/specs/2026-09-29-validation-lab-design.md)이
> 소유한다.

**Goal:** 백테스트 결과가 운·과최적화·비용 착시인지를 앱이 묻고 기록하게 한다. 연구 구간 잠금, 시도 원장,
실험(그리드·워크포워드)과 비동기 대기열, 비용 현실화, 검증 통계, 결과 검증 카드, 홀드아웃 1회 개봉.

**Architecture:** backend가 봉인 판정(`domain/backtest`), 시도 키 분류표(`domain/backtest`), 실험 모델과 통계
(`domain/experiment`), 실행 접수·슬롯(`application/backtest_run` 스케줄러), 실험 유스케이스
(`application/experiment_run`), 영속(`adapters/outbound/research_sqlite`)을 소유한다. frontend는 새 라우트
`/research/experiments`와 결과 화면 검증 카드를 그리고, 판정·통계·공식을 복제하지 않는다.

**Tech Stack:** Python 3.11 dataclasses · FastAPI · SQLite · DuckDB · Rust(`backtest_core`) · React 19 ·
TanStack Router/Query · Vitest · MSW · Playwright

---

## 1. 실행 순서와 스택

```text
main
 └─ V0-01  docs/validation-lab-plan
     ├─ V1-01 ─ V1-02                              연구 구간 잠금 (지금)
     ├─ [#161 머지] ─ V1-03 ─ V1-04 ─ V1-05         실행 영속화 · 결과 재적재 · 시도 원장
     ├─ V2-01 ─ V2-02 ─ V2-03                      비용 (지금)
     ├─ [#274 머지] V4-01                          PSR
     └─ V3-01 ─ V3-02                              domain.experiment · 파라미터 배선 (지금)
         └─ (V1-05) V3-03 ─ V3-04 ─ [#274 머지] V3-05   실험 저장소·API · 대기열 · 워크포워드
             ├─ (V4-01) V4-02 ─ V4-03 ─ V4-04 ─ V4-05   DSR · 고원 · 용량 · 팩터 회귀
             │    └─ V6-01                          홀드아웃 개봉 backend (V1-01·V1-05·V4-02 뒤)
             └─ V5-01 … V5-06 (각 화면의 backend PR 뒤) ─ V5-07 (lang2 P4-04·P5-02 뒤)   화면
                  └─ V6-02                          홀드아웃 개봉 화면 (V5-02 뒤)
```

- PR ID는 `V<phase>-<번호>`다(lang2의 `P*`, AI 어시스턴트의 `A`~`D`와 겹치지 않게). 브랜치 이름은
  `feat/vlab-<slug>`. 첫 세 구현 브랜치(`feat/vlab-p1-01-…` 등)는 ID 개정 전에 만들어져 이름만 옛 형식이다.
- 스택 첫 PR의 base는 main이다. 선행 PR이 main에 머지되기 전에 착수하면 직전 PR 브랜치를 base로 쌓는다.
- **외부 선행 작업**: #161·#160 브랜치(`fix/backtest-run-concurrency`, 실행 접수 대기열)와 #274 스택(지표
  공식·CAGR 기간·무위험수익률·샤프 표준오차)은 다른 작업 흐름이 소유한다. 이 initiative는 그 브랜치를
  건드리지 않고, 머지를 기다렸다가 그 위에서 시작한다.
- 화면 PR(V5)은 그 화면이 부르는 backend PR이 머지되면 시작한다. 남은 lang2(P3-03~P6-03)는 전략 IDE 가운데
  편집 영역만 바꾸므로 V5-01~V5-06과 겹치지 않는다. IDE에 붙는 V5-07만 lang2 P4-04·P5-02 뒤다(2026-09-29 제품
  소유자 확인). V5-05는 lang2 P4-02가 실행 설정 요약 띠를 옮기므로 그 PR과 파일이 겹치면 늦게 머지되는 쪽이
  rebase한다.
- OpenAPI를 바꾸는 backend PR은 같은 PR에서 `frontend/src/shared/api/generated`를 재생성해 별도 커밋으로
  넣는다(CI `api:generate` diff 게이트). 새 422 코드는 `backtest.error.<code>` 번역 키를 ko·en으로 같은 PR에
  넣는다.

## 2. 공통 gate

| PR 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run pytest` 전체 → `uv run ruff check src tests` → `uv run pyright` → 루트에서 `uv run --project backend pytest database/tests -q`(Windows 기존 실패는 main 트리와 id 대조) |
| 엔진(Rust) | 위 + `maturin develop --release` → `uv run pytest tests/test_core_parity.py` |
| API contract | backend 전체 → `uv run python scripts/export_openapi.py openapi.json` → `npm run api:generate` → frontend 전체 |
| frontend | focused vitest → `npm run typecheck` → `npm run lint` → `npm test` → `npm run build` |
| E2E 포함 | 위 + `npm run test:e2e`(머신 잠금 러너) |

- 실행 설정 모델이 바뀌면 `uv run python tools/export_runtime_schema.py`로 스키마 fixture를 재생성한다.
- `uv sync`를 돌렸으면 게이트 전에 `maturin develop --release`를 다시 돌린다(editable `backtest_core` 휠 제거).
- PR 크기는 [YAML Strategy Workbench WORKFLOW 12절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다. 상한을
  넘기면 PR 본문 `제약사항`에 사유를 적는다.
- 리뷰어 프롬프트의 우선순위: 전체 코드량 최소 → SoT 단일 → 책임 분리·의존 방향 → 결함 보고 4요소·한글 →
  유저 스토리 하네스 → 손계산 정답 기반 테스트 → 정확성.
- Phase(V1~V6)의 마지막 PR이 머지되면 다음 Phase에 들어가기 전에 리뷰 두 가지를 따로 돌린다.
  (a) 그 Phase 작업분의 SoT·책임 분리 감사, (b) 그 Phase가 건드린 도메인 **전체**(기존 코드 포함) 코드 리뷰.
  (b)에는 frontend·backend 경계 책임 분리(화면이 backend 규칙·기본값을 복제하는지, backend 요청 칸을 화면이
  빠짐없이 싣는지)를 반드시 넣는다. 블로커는 다음 Phase 전에 고치고, 나머지는 이슈로 남긴다.

---

## 3. Phase 0 — 기획 패키지

### V0-01 — 기획 패키지·설계 spec·유저 스토리

**Intent**: 이 패키지와 설계 spec을 main에 올리고, 새 유저 스토리를 `예정`으로 등록한다.

**Acceptance**

- `docs/planning/validation-lab/{README,WORKFLOW,PLAN}.md`, `tools/update-plan-progress.ps1`(`-Check` 통과).
- `docs/superpowers/specs/2026-09-29-validation-lab-design.md`.
- 로드맵 머리말에 M6·M7을 이 initiative가 구체화한다는 링크.
- 유저 스토리 US-DM-10·11, US-SM-12~15, US-CS-09~13을 `예정`으로 더하고 `user_story_trace --write` 통과.

**Non-goal**: 코드 변경, SoT 대장 행(구현 PR이 owner 파일과 함께 더한다).

---

## 4. Phase 1 — 봉인과 기록

### V1-01 — 연구 구간 잠금(실행·미리보기·추적)

**Intent**: 2020-01-02 이전을 측정하는 실행·포트폴리오 미리보기·추적 요청을 서버가 코드화된 422로 막는다(spec D1).

**Acceptance**

- `domain/backtest/_research_window.py`: 봉인 구간·연구 하한 상수와 판정 함수 하나. facade로 공개.
- `require_environment`가 판정을 호출한다. `RunEnvironment.__post_init__`은 건드리지 않는다(회귀 테스트로 고정).
- 실행 경로 422 `backtest.run.research_window_violation`, 미리보기·추적은 `portfolio.strategy.invalid` 안 issue
  code `run_environment.research_window`. `_require_environment_or_reject`가 새 오류도 잡는다.
- `metric_windows`는 실행 서비스가 이미 실행 범위 안으로 강제하므로 따로 검사하지 않는다.
- 실행 경로 422 detail이 봉인 구간과 연구 하한 값을 싣고, `backtest.error.<code>` ko·en 문장은 자리표시자로
  쓴다(날짜를 frontend에 적지 않는다). 미리보기·추적 issue 문장은 backend가 날짜를 넣어 완성한다.
- 테스트: 2019-12-31·2020-01-01 거부, 2020-01-02 허용, 워밍업 관측을 읽어도 통과, HTTP 세 경로의 코드·문장.
- SoT "실행 설정 진단 코드" 행에 새 코드와 owner 파일. OpenAPI·생성 SDK.

**Non-goal**: 팩터·equity 미리보기(V1-02), 화면의 빠른 피드백(V5-02 이후), 홀드아웃 개봉(V6).

### V1-02 — 연구 구간 잠금 확장(팩터·equity 미리보기)

**Intent**: 측정·데이터 열람 경로 중 남은 `/api/v1/factors/preview`, `/api/v1/equity/panel/preview`,
`/api/v1/equity/preview`에 같은 판정을 건다. `/api/v1/equity/universe/preview`는 구성 종목 이력만 보여 측정이
아니므로 뺀다.

**Acceptance**: 같은 판정 함수를 호출(복제 금지), 각 경로 기존 422 체계에 issue code 싣기, 2020 이전 날짜를
쓰던 테스트는 연구 구간 날짜로 옮기거나 판정이 걸리지 않는 포트 수준 테스트로 내린다.

**Non-goal**: 원시 포트·엔진 직접 호출(측정 경로가 아님).

### V1-03 — 실행 기록 영속화

**선행**: #161 브랜치(`fix/backtest-run-concurrency`) main 머지.

**Intent**: 실행 목록과 상태를 `.local/research.sqlite3`에 저장해 재시작 뒤에도 남긴다(spec D3).

**Acceptance**

- `application/backtest_run/ports/outgoing/run_repository.py` 포트, `adapters/outbound/research_sqlite` 어댑터
  (스키마 v1, application_id, DDL manifest 대조, 빈 파일만 claim, 다른 앱 파일 거부).
- 파일 claim·application_id·manifest 대조는 세 번째로 복제하지 않고 공통 routine으로 뽑아 `strategy_sqlite`·
  `assistant_sqlite`도 그것을 쓰게 한다.
- 상태 전이 시점 저장, 진행 이벤트는 메모리 링.
- 재시작 시 비종결 단일 실행은 `failed` + `backtest.run.interrupted`.
- 재시작 뒤 목록·상태가 남는지는 backend 통합 테스트(저장소 재생성)로 확인한다(e2e로 재현하지 않는다).
- `bootstrap` 경로·환경 변수 `STRATEGY_WORKBENCH_RESEARCH_DB_PATH`·파일 권한 가드, `tests/conftest.py`·
  `runtime_path_guard` 격리.
- 404 설명의 "process-lifetime" 문구 제거, 백테스트 이력 화면의 "현재 서버 프로세스에서" 문구 i18n 갱신.

**Non-goal**: 결과 파일 재적재(V1-04), 시도 원장(V1-05).

### V1-04 — 결과 재적재

**선행**: #277(`artifact_uri` 절대 경로 노출, 이슈 큐 작업 흐름이 소유) 머지.

**Intent**: 재시작 뒤에도 `/result`와 AI 결과 설명이 동작하게 `result.json` 디코더를 둔다. artifact 키 형식은
#277의 결과를 그대로 따른다.

**Acceptance**: 인코더와 같은 모듈의 디코더(왕복 테스트), 재시작 뒤 `/result`·AI 결과 포트 동작, 고아 `.tmp`
staging 청소.

### V1-05 — 시도 원장

**Intent**: 실행마다 시도 키를 계산해 계열 원장에 남기고, 새로 고를 거리가 생긴 실행만 N에 센다(spec D2).

**Acceptance**

- `domain/backtest/_trial_key.py`: RunEnvironment 칸 분류표(키·방향·키 밖)와 시도 키 함수. architecture
  테스트가 모든 필드의 분류를 강제한다. 먼저 머지된 V2 칸도 여기서 분류한다.
- 전략 의미 해시: `spec_hash` payload에서 제목·설명·파라미터 범위 정의를 뺀 해시(spec D2). owner는
  `domain/strategy/_canonical.py`의 `strategy_spec_hash` 옆 함수 하나이고 `_trial_key.py`는 부르기만 한다. 해소된
  파라미터 값은 파라미터 배선(V3-02) 전이라도 문서 기본값으로 키에 싣는다.
- 기본값인 칸은 키 표기에서 빠진다. "칸 추가 전후 같은 키" 테스트. 스키마 기본값을 바꾸는 것은 시도 키 변경으로
  보고, 분류표 테스트가 기본값 스냅샷과 대조해 그 PR이 변경을 명시하게 한다.
- 원장 테이블(실행 ↔ 시도 키 ↔ 계열 ↔ 대표 샤프·레지스트리 판본). 대표 샤프는 세션 단위(연율화 전) 샤프로
  적는다(실행마다 `annualization_days`가 다를 수 있다). 결과 없는 실행은 기록하되 N 제외. 원장에 남는 거절은
  전략이 확정된 뒤의 거절뿐이고, 봉인 겹침 거절의 정본은 봉인 원장이며 시도 원장은 그것을 조회로 보인다.
- 실행 요청의 선택 칸 `lineage_strategy_id`(인라인 초안의 계열, `run_fingerprint` 밖).
- API: 계열 원장 조회(시도 묶음·재확인 실행·N 제외 사유), 계열 합치기(되돌릴 수 없음), 실행 전 미리 계산
  ("새 시도인가, N이 얼마가 되나").
- 봉인 원장: 백테스트 실행 경로에서 봉인 겹침으로 거절한 요청을 "차단한 시도"로 기록(미리보기 경로는 기록하지
  않는다).
- 테스트: 분류표 칸마다 변주(새 시도/같은 시도), 비용 유리·기본·불리, 같은 설정 재실행, 제목만 바꾼 저장,
  결과 없는 실행 제외, 합치기 뒤 N, 대표 샤프 선택.

---

## 5. Phase 2 — 비용 현실화

### V2-01 — 매도 거래세

**Intent**: `sell_tax`(`krx_statutory`·`custom`·`none`)와 `sell_tax_bps`를 RunEnvironment에 더하고 두 엔진 코어가
매도 체결에만 부과한다(spec D7).

**Acceptance**

- `domain/backtest/_krx_tax.py`: 시장·날짜 구간별 법정 세율표(근거 법령·시행일 주석). 2026년 세율은 공포
  법령으로 확인해 PR 본문에 출처를 적는다.
- Python `broker.py`, Rust `session.rs`·`session/group.rs`·`driver.rs`·`persistent.rs`, `engine/core.py`, 어댑터.
  세금은 `RawCost`의 별도 kind로 두고 `total_fees`와 나눈다(지표 레지스트리에 `total_taxes`).
- `environment_hash`는 판본 없이 바뀌게 둔다(비교하는 곳이 없다). `RunManifest` 평면 호환 집합 고정.
- 시도 키 분류표(V1-05)가 이미 있으면 새 칸을 같은 PR에서 분류한다. 없으면 V1-05가 분류한다.
- 두 코어 parity, 손계산 소형 체결(매도만 과세), 스키마 fixture·OpenAPI·생성 SDK·어휘 i18n.

### V2-02 — ADV 배선과 참여 기준

**Intent**: `participation_basis`(`session_volume`·`adv20`)를 더하고 20일 평균 거래대금 기준 한도를 두 코어에
넣는다.

**Acceptance**: `BacktestDataQuery` 워밍업 칸, `MarketBarRecord` ADV 칸, mock·duckdb 어댑터가 같은 계약을 답함
(SoT "equity 필드 계약" 행 갱신), 원화 ADV → 주식 수 환산(판단일 종가), 두 코어 cap 기준 parity, 첫 20세션
ADV 테스트. 시도 키 분류표가 있으면 새 칸 분류.

### V2-03 — √ 시장충격 모델

**Intent**: `impact_model`(`fixed_bps`·`sqrt`)과 `impact_coefficient`를 더한다. 비용 ≈ k × σ일 × √(주문/ADV).

**Acceptance**: 두 코어 구현과 손계산 테스트, Rust 미지원 모델 거절 문장, σ 추정 창(20세션) 명시. 시도 키
분류표가 있으면 새 칸 분류.

---

## 6. Phase 3 — 실험 backend

### V3-01 — domain.experiment

**Intent**: `SearchSpec`(그리드), `SplitSpec`(롤링·앵커드·엠바고), trial 상태 머신, 창 선택 규칙을 순수
domain으로 둔다(spec D5).

**Acceptance**: 새 노드 facade·`DEPENDS_ON`, 그리드 결정성, 모든 창이 연구 구간 안(연구 하한은 인자로 받는다 —
owner는 V1-01의 `_research_window.py`), 엠바고 계산, 상태 역행 금지, 이웃 정의(체비셰프 거리 1, 3^d − 1칸,
경계는 있는 칸만)와 이웃 평균 함수. 파라미터 허용값 술어는 `domain/strategy`의 파라미터 정의 옆 하나로 두고
validator와 SearchSpec이 함께 부른다.

**Non-goal**: 저장·실행(V3-03 이후).

### V3-02 — 파라미터 값 배선

**Intent**: `BacktestRunSpec.parameter_values`를 더해 문서 기본값 대신 해소된 값으로 실행한다(spec D4).

**Acceptance**: `portfolio_design`의 파라미터 해소 한 곳만 변경, 허용값 밖 422(V3-01의 술어), 해소된 값이
`run_fingerprint`·시도 키를 가름, 저장 리비전 provenance 불변, SoT 금지 절 캐시 키 목록에 "해소된 파라미터 값".

### V3-03 — 실험 저장소·유스케이스·API

**Intent**: `application/experiment_run`과 research DB의 experiments·trials·selection 테이블, 생성·조회·
trial 목록·취소 API.

**Acceptance**: 기반은 저장된 리비전만(인라인·동결은 422), 실패 trial 보존, 재시도는 새 attempt, 배정 뒤
trial 상태는 실행 상태에서 파생, 시작 전 미리 계산("새로 세는 조합 수"). `experiment_run`은 자기
`ports/outgoing`의 trial 실행 포트만 쓰고 bootstrap이 backtest_run 스케줄러를 감싸 주입한다(새 application →
application 화살표 없음).

### V3-04 — 대기열 확장

**Intent**: #161 대기열을 전역 슬롯 상한·단일 실행 전용 슬롯·실험 간 공정 분배·`run_fingerprint` 중복 제거·
재시작 복구·실험 단위 일시정지/재개/우선순위로 넓힌다(spec D6).

**Acceptance**: #161의 `MAX_CONCURRENT_RUNS` 상수와 SoT 행을 설정 하나(기본값 = 그 상수, 환경 변수
`STRATEGY_WORKBENCH_RUN_SLOTS`로 덮어씀)로 대체, 슬롯 2개 이상일 때만 단일 실행 전용 1개, 굶주림 없음, 공정
분배, 같은 `run_fingerprint` 입력은 도는 run 만 잇고 끝난 결과는 재사용하지 않는다(재확인은 원장에 남는다,
2026-09-30 리드 결정. 시도 키로 결과를 공유하지 않는다), 재기동 복구, 일시정지 뒤
순서, 실험 SSE(keepalive), e2e가 실행을 붙잡아 둘 수 있는 테스트 훅, `_gc_policy` 주기 기반 재설계.
`RunStatus` 값 불변.

**V3-03 인계**: (1) 같은 입력 잇기는 사용자의 단일 실행도 잇는다 — 실험을 취소하면 trial 이 이은 단일
실행도 취소된다. (2) `_submit` 이 예상 밖 예외로 멈추면 남은 trial 이 `queued` 로 남고 프로세스 안에서는
풀 방법이 없다(재시작 복구와 같은 경로로 닫는다). (3) 이슈 #335 P2(`engine_version` 리터럴이 오르지 않아
지문 기반 결과 공유가 옛 엔진 결과를 재사용한다)를 착수 전에 정한다. (4) 실험 목록 API(`GET
/api/v1/experiments`)의 자리(V3-04 또는 V5-01)를 정한다. (5) `get`·`trials`·`retry`·`select` 가 trial 마다
실행 상태를 한 번씩 읽는다 — V5-01 폴링 전에 `TrialRunPort` 일괄 상태 조회를 둔다(#334 리뷰 P3-9).

**나눔(2026-09-30 리드)**: 스택 2개로 낸다. 1/2 = #335 DOMAIN-V1-01(엔진·비용 규칙 판본
`ENGINE_RULES_VERSION` 을 매니페스트·실행 지문에 싣고 digest 가드 테스트)·V1-02(지표 창 `label` 을 지문·
같은 입력 잇기에서 뺌), 슬롯 설정, 단일 실행 전용 슬롯, 실험 간 라운드로빈, 같은 입력 잇기의 공유 run
취소(소유자가 모두 빠질 때만, 인계 (1)), 일괄 상태 조회(인계 (5)), 실험 목록 API(인계 (4)). 2/2 = 재시작
복구(인계 (2) 포함), 일시정지·재개·우선순위, 실험 SSE, e2e 훅, `_gc_policy` 재설계. #335 DOMAIN-V1-03(이름·
순서 무관 의미 해시)은 V4-02 전 별도 PR 이다.

### V3-05 — 워크포워드 실행

**선행**: #274 스택 머지(구간 지표 경계일·1년 미만 연율화).

**Intent**: SplitSpec의 창마다 학습 → 선택 → 검증 실행을 이어 돌리고, 검증 창 수익률만 이어 붙인 곡선과
유지율을 만든다.

**Acceptance**: 창별 선택 기록, 워밍업은 검증 창 앞에서 읽되 성과 제외, 이어 붙인 곡선의 경계일 손계산 테스트
(#274 구간 지표 수정 위에서).

**결정(2026-09-30 리드)**: 창별 학습 점수는 spec D2 대표 샤프(세션 단위), 동점은 좌표가 앞선 칸이고 규칙은
domain 한 곳(`pick_window_cell`)이다. 검증 실행은 새 선택이 아니라 고른 칸의 시도 키로 원장에 적어 재확인이 된다
(N 불변). 창별 자동 선택(`WindowPick`, `experiment_window_picks`)은 사용자 후보 선택(`ExperimentSelection`)과
이름·저장을 나눈다. 엠바고는 실험을 만들 때 세션 달력(`EquityDataPort.trading_sessions`)으로 학습 끝을 당겨
설계에 저장한다. 유지율 = 이어 붙인 곡선의 세션 샤프 ÷ 고른 칸 학습 대표 샤프 평균(평균 0 이하면 없음).

**V3-05 인계**: (1) 검증 실행 접수가 거절되면 선택 행에 코드·문장만 남고 다시 넘기지 않는다 — 재시도 경로가
필요하면 V5-01 화면과 함께 정한다. (2) 워크포워드 결과는 `GET /api/v1/experiments/{id}/walk-forward` 한
곳이고 화면(곡선·유지율·창별 선택 표)은 V5-01 이다.

---

## 7. Phase 4 — 검증 통계

### V4-01 — PSR

**선행**: #274 4/4(샤프 표준오차) 머지. Metric Registry에 `probabilistic_sharpe`를 더하고 골든·ko·en 문구.

### V4-02 — DSR과 선택 기록

`domain/experiment`의 DSR 함수(원장의 N·시도 대표 샤프의 분산), 알파 t 기준 상수(N ≥ 20이면 3.0), 인라인 초안
결과는 DSR 없음. selection record API(spec D9)는 V3-03에 들어갔다. **V3-03 인계**: 실험 trial 은 한 칸의 창들이
한 시도라(`experiment_trial_key`) 그 시도의 대표 샤프가 먼저 **완료된** 창의 학습 샤프가 된다 — 슬롯·대기 순서에
따라 창이 바뀌어 DSR 입력이 실행 타이밍에 달린다. 실험 시도의 대표 샤프를 정하는 규칙을 이 PR 에서 정한다(#334
리뷰 P3-7). **V3-05 확인**: 워크포워드 검증 실행도 같은 시도 키지만 그 칸의 학습 실행이 완료된 뒤에야 넘기므로
대표가 되지 않는다. 창 사이 완료 순서 의존은 남는다 — 원장 규칙("나중 완료가 대표를 바꾸지 않는다")을 깨지
않고는 실행 단계에서 닫을 수 없어 V4-02 가 DSR 입력 쪽에서 정한다(예: 실험 시도는 창별 학습 샤프로 따로 정하기). 테스트는 수학 노트의 예시 값(연 샤프 0.86, 889세션, N 238, 시도 샤프
흩어짐 연 0.25, 왜도 -0.41, 원 첨도 5.8, 252일 기준 → 0.61)을 손계산 기준으로.

### V4-03 — 고원·민감도

±20% 민감도(가장 가까운 격자값)와 봉우리 판정 기준. 이웃 평균은 V3-01 것을 쓴다.

**V3-05 인계(#364 리뷰 P3-2)**: 이웃 평균(`neighbor_mean`, 워크포워드 창 선택 `pick_window_cell`)은 학습이
실패·파산한 칸을 "없는 칸"으로 빼서, 파산 칸 옆 칸이 경계 칸처럼 고원으로 보인다. 실패 칸을 최하 점수로
넣을지 이 PR 에서 정한다.

### V4-04 — 용량 스윕

실험 종류 "용량 스윕"(초기 자본만 변주, N 제외), 한계 금액(최고 샤프의 절반) 계산, 금액별 참여율·충격 비용·
반올림 오차·미체결 비율.

### V4-05 — 팩터 회귀

시장 팩터(유니버스 시가총액 가중 수익률), 규모·가치·모멘텀 롱숏 수익률(Factor Registry의 기존 `factor_id`와
평가 경로 재사용, 새 팩터 정의 금지), OLS + HAC(Newey–West) 표준오차. 데이터를 읽는 application 노드와 포트를
이 PR에서 정한다. 손계산 소형 데이터로 계수·t값 검증.

---

## 8. Phase 5 — 화면

각 PR은 디자인보드의 해당 보드를 따른다. 스토리 태그 e2e를 같은 PR에 넣는다. 선행 backend PR은 PLAN
Dependency 칸이 정본이다.

| PR | 범위 | 스토리 |
|---|---|---|
| V5-01 | `/research/experiments` 목록·대기열·새 실험·모니터·완료 알림, 내비 "실험" 활성화 | US-SM-14, US-SM-15 |
| V5-02 | 결과 화면 검증 카드(단일 실행 빈 칸·“튼튼한지 확인하기”·계산 근거 펼침·워크포워드 곡선·DSR), AI 결과 설명에 검증 요약, 추적·미리보기 요청에 parameter_values 추가(V3-02 인계) | US-DM-10, US-DM-11, US-CS-09 |
| V5-03 | 백테스트 이력 종류 칼럼·필터, 전략 이력 시도 원장 탭·계열 합치기 | US-SM-12 |
| V5-04 | 후보 탐색 히트맵(팔레트 토큰), 봉우리, 창별 선택, selection 이유, 추적·미리보기 요청에 parameter_values 추가(V3-02 인계) | US-CS-10 |
| V5-05 | 실행 설정 잠금 UX(시작일 교정 버튼)·시도 영향 미리 알림(비용 칸 어휘는 V2-01~03이 소유) | US-SM-13, US-SM-16 |
| V5-06 | 용량 스윕 화면, 검증 카드의 팩터 회귀 항목 | US-CS-11, US-CS-12 |
| V5-07 | IDE 제목 옆 계열 시도 배지, 상단 바 “실험으로 보내기”(lang2 P4-04 뒤), 레시피 탐색 토글(lang2 P5-02 뒤) | US-SM-12 |

- V5-01 인계(V3-03, #334 리뷰 P3-4): 재시도 가능(실패·취소 trial, 실험 미취소)·선택 가능(완료 trial) 조건이
  `ExperimentRunService` 에만 있다. 화면이 버튼 활성화를 위해 이 규칙을 복제하지 않도록 착수 전에
  `ExperimentTrialState` 에 `retryable`·`selectable` 을 싣는다.
- V5-01 인계(V3-04, #346 리뷰 P3-4): US-SM-11(같은 계산이 겹쳐 돌지 않는다)의 스토리 e2e 를 V3-04 의 trial
  붙잡기 훅으로 붙인다(붙잡힌 trial 을 사용자가 이어 같은 실행 ID 로 가고, 취소해도 실험이 쓰는 동안 돈다).
- V5-01 인계(V3-04 2/2, #348 리뷰 P3-6): (1) playwright 서버 환경에 `STRATEGY_WORKBENCH_E2E_TRIAL_HOLD_SECONDS`
  를 켜 도는 trial 을 붙잡는다(켜지면 기동 경고 로그). (2) 진행 스트림(`/experiments/{id}/events`)은 실험이
  끝나고 도는·대기 trial 이 없을 때 최종 수를 보낸 뒤 닫힌다 — 화면은 닫힌 스트림을 끝으로 본다.
  (3) US-SM-15 의 "동시 실행 슬롯 사용량"은 V5-01 에서 기존 응답(실험 목록)에 최소 칸 하나(예: 슬롯 수·도는
  run 수)를 더해 보인다 — 화면이 trial 수로 추정하지 않는다.
- V5-02 인계(V4-01, #363 리뷰 P3-3): PSR(`probabilistic_sharpe`)이 1.0으로 포화되면 percent 포맷이 100.00%로
  보인다. 검증 카드는 ">99.99%" 같은 상한 표기를 정한다.

## 9. Phase 6 — 홀드아웃 개봉

### V6-01 — 개봉 backend

**선행**: V1-01, V1-05, V4-02(화면 신호와 무관).

사양 동결·기준 사전 등록·개봉 원장·되돌릴 수 없는 열람 상태, 봉인 구간을 여는 유일한 경로(연구 구간 잠금의
명시 예외), 개봉 뒤 재실행 분류.

### V6-02 — 개봉 화면

**선행**: V6-01, V5-02.

디자인보드 ⑥의 4단계 흐름과 검증 카드 미달 항목 표시. 스토리 US-CS-13, 스토리 태그 e2e 포함.

## 10. Backlog

- `tools/update-plan-progress.ps1`은 패키지마다 복제된 다섯 번째 사본이다(phase 맵과 ID 패턴만 다르다). 공용
  도구 하나가 PLAN 경로·phase 맵·ID 패턴을 인자로 받게 하는 정리를 initiative 밖 후속으로 둔다(V0-01 리뷰 P2).
