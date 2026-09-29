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
 └─ P0-01  docs/validation-lab-plan
     ├─ P1-01 ─ P1-02                              연구 구간 잠금 (지금)
     ├─ [#161 머지] ─ P1-03 ─ P1-04 ─ P1-05         실행 영속화 · 결과 재적재 · 시도 원장
     ├─ P2-01 ─ P2-02 ─ P2-03                      비용 (지금)
     └─ P3-01                                      domain.experiment (지금)
         └─ (P1-05) P3-02 ─ P3-03 ─ P3-04 ─ P3-05  실험 backend · 대기열 · 워크포워드
             └─ [#274 머지] P4-01 ─ P4-02 ─ P4-03 ─ P4-04 ─ P4-05  검증 통계
                 └─ [lang2 머지 신호] P5-01 … P5-07  화면
                     └─ P6-01 ─ P6-02              홀드아웃 개봉
```

- 브랜치 이름은 `feat/vlab-<pr-id 소문자>-<slug>`. 예: `feat/vlab-p1-01-research-window`.
- 스택 첫 PR의 base는 main이다. 선행 PR이 main에 머지되기 전에 착수하면 직전 PR 브랜치를 base로 쌓는다.
- **외부 선행 작업**: #161·#160 브랜치(`fix/backtest-run-concurrency`, 실행 접수 대기열)와 #274 스택(지표
  공식·CAGR 기간·무위험수익률·샤프 표준오차)은 다른 작업 흐름이 소유한다. 이 initiative는 그 브랜치를
  건드리지 않고, 머지를 기다렸다가 그 위에서 시작한다.
- **화면 PR(P5)은 제품 소유자가 lang2 머지를 알린 뒤 시작한다**(2026-09-29 지시). P5-07은 lang2 P4-04·P5-02
  뒤다.
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

---

## 3. Phase 0 — 기획 패키지

### P0-01 — 기획 패키지·설계 spec·유저 스토리

**Intent**: 이 패키지와 설계 spec을 main에 올리고, 새 유저 스토리를 `예정`으로 등록한다.

**Acceptance**

- `docs/planning/validation-lab/{README,WORKFLOW,PLAN}.md`, `tools/update-plan-progress.ps1`(`-Check` 통과).
- `docs/superpowers/specs/2026-09-29-validation-lab-design.md`.
- 로드맵 머리말에 M6·M7을 이 initiative가 구체화한다는 링크.
- 유저 스토리 US-DM-10·11, US-SM-11~14, US-CS-09~13을 `예정`으로 더하고 `user_story_trace --write` 통과.

**Non-goal**: 코드 변경, SoT 대장 행(구현 PR이 owner 파일과 함께 더한다).

---

## 4. Phase 1 — 봉인과 기록

### P1-01 — 연구 구간 잠금(실행·미리보기·추적)

**Intent**: 2020-01-02 이전을 측정하는 실행·포트폴리오 미리보기·추적 요청을 서버가 코드화된 422로 막는다(spec D1).

**Acceptance**

- `domain/backtest/_research_window.py`: 봉인 구간·연구 하한 상수와 판정 함수 하나. facade로 공개.
- `require_environment`가 판정을 호출한다. `RunEnvironment.__post_init__`은 건드리지 않는다(회귀 테스트로 고정).
- 실행 경로 422 `backtest.run.research_window_violation`, 미리보기·추적은 `portfolio.strategy.invalid` 안 issue
  code `run_environment.research_window`. `_require_environment_or_reject`가 새 오류도 잡는다.
- `metric_windows` 시작일도 같은 판정(방어 검사).
- 테스트: 2019-12-31·2020-01-01 거부, 2020-01-02 허용, 워밍업 관측을 읽어도 통과, HTTP 세 경로의 코드·문장.
- SoT "실행 설정 진단 코드" 행에 새 코드와 owner 파일. OpenAPI·생성 SDK·`backtest.error.<code>` ko·en.

**Non-goal**: 팩터·equity 미리보기(P1-02), 화면의 빠른 피드백(P5-02 이후), 홀드아웃 개봉(P6).

### P1-02 — 연구 구간 잠금 확장(팩터·equity 미리보기)

**Intent**: 측정·데이터 열람 경로 중 남은 `/api/v1/factors/preview`, `/api/v1/equity/panel/preview`,
`/api/v1/equity/preview`에 같은 판정을 건다.

**Acceptance**: 같은 판정 함수를 호출(복제 금지), 각 경로 기존 422 체계에 issue code 싣기, 2020 이전 날짜를
쓰던 테스트는 연구 구간 날짜로 옮기거나 판정이 걸리지 않는 포트 수준 테스트로 내린다.

**Non-goal**: 원시 포트·엔진 직접 호출(측정 경로가 아님).

### P1-03 — 실행 기록 영속화

**선행**: #161 브랜치(`fix/backtest-run-concurrency`) main 머지.

**Intent**: 실행 목록과 상태를 `.local/research.sqlite3`에 저장해 재시작 뒤에도 남긴다(spec D3).

**Acceptance**

- `application/backtest_run/ports/outgoing/run_repository.py` 포트, `adapters/outbound/research_sqlite` 어댑터
  (스키마 v1, application_id, DDL manifest 대조, 빈 파일만 claim, 다른 앱 파일 거부).
- 상태 전이 시점 저장, 진행 이벤트는 메모리 링.
- 재시작 시 비종결 단일 실행은 `failed` + `backtest.run.interrupted`.
- `bootstrap` 경로·환경 변수 `STRATEGY_WORKBENCH_RESEARCH_DB`·파일 권한 가드, `tests/conftest.py`·
  `runtime_path_guard` 격리.
- 404 설명의 "process-lifetime" 문구 제거, 백테스트 이력 화면의 "현재 서버 프로세스에서" 문구 i18n 갱신.

**Non-goal**: 결과 파일 재적재(P1-04), 시도 원장(P1-05).

### P1-04 — 결과 재적재와 상대 artifact 키(#277)

**Intent**: 재시작 뒤에도 `/result`와 AI 결과 설명이 동작하게 `result.json` 디코더를 두고, 상태 응답의
`artifact_uri`를 루트 기준 상대 키로 바꾼다.

**Acceptance**: 인코더와 같은 모듈의 디코더(왕복 테스트), 응답에 절대 경로가 없다는 단언, 고아 `.tmp` staging
청소. `Closes #277`.

### P1-05 — 시도 원장

**Intent**: 실행마다 시도 키를 계산해 계열 원장에 남기고, 새로 고를 거리가 생긴 실행만 N에 센다(spec D2).

**Acceptance**

- `domain/backtest/_trial_key.py`: RunEnvironment 칸 분류표(키·방향·키 밖)와 시도 키 함수. architecture
  테스트가 모든 필드의 분류를 강제한다.
- 원장 테이블(실행 ↔ 시도 키 ↔ 계열), 결과 없는 실행은 기록하되 N 제외.
- 실행 요청의 선택 칸 `lineage_strategy_id`(인라인 초안의 계열).
- API: 계열 원장 조회(시도 묶음·재확인 실행·N 제외 사유), 계열 합치기(되돌릴 수 없음).
- 봉인 원장: 봉인 겹침으로 거절한 요청을 "차단한 시도"로 기록.
- 테스트: 분류표 칸마다 변주(새 시도/같은 시도), 비용 유리·기본·불리, 같은 설정 재실행, 결과 없는 실행 제외,
  합치기 뒤 N.

---

## 5. Phase 2 — 비용 현실화

### P2-01 — 매도 거래세와 environment_hash 판본

**Intent**: `sell_tax`(`krx_statutory`·`custom`·`none`)와 `sell_tax_bps`를 RunEnvironment에 더하고 두 엔진 코어가
매도 체결에만 부과한다(spec D7).

**Acceptance**

- `domain/backtest/_krx_tax.py`: 시장·날짜 구간별 법정 세율표(근거 법령·시행일 주석). 2026년 세율은 공포
  법령으로 확인해 PR 본문에 출처를 적는다.
- Python `broker.py`, Rust `session.rs`·`session/group.rs`·`driver.rs`·`persistent.rs`, `engine/core.py`, 어댑터.
  세금은 `RawCost`의 별도 kind로 두고 `total_fees`와 나눈다(지표 레지스트리에 `total_taxes`).
- `environment_hash` 판본 `env-v2`, 옛 manifest는 옛 해시 유지. `RunManifest` 평면 호환 집합 고정.
- 두 코어 parity, 손계산 소형 체결(매도만 과세), 스키마 fixture·OpenAPI·생성 SDK·어휘 i18n.

### P2-02 — ADV 배선과 참여 기준

**Intent**: `participation_basis`(`session_volume`·`adv20`)를 더하고 20일 평균 거래대금 기준 한도를 두 코어에
넣는다.

**Acceptance**: `BacktestDataQuery` 워밍업 칸, `MarketBarRecord` ADV 칸, mock·duckdb 어댑터가 같은 계약을 답함
(SoT "equity 필드 계약" 행 갱신), 원화 ADV → 주식 수 환산(판단일 종가), 두 코어 cap 기준 parity, 첫 20세션
ADV 테스트.

### P2-03 — √ 시장충격 모델

**Intent**: `impact_model`(`fixed_bps`·`sqrt`)과 `impact_coefficient`를 더한다. 비용 ≈ k × σ일 × √(주문/ADV).

**Acceptance**: 두 코어 구현과 손계산 테스트, Rust 미지원 모델 거절 문장, σ 추정 창(20세션) 명시.

---

## 6. Phase 3 — 실험 backend

### P3-01 — domain.experiment

**Intent**: `SearchSpec`(그리드), `SplitSpec`(롤링·앵커드·엠바고), trial 상태 머신, 창 선택 규칙을 순수
domain으로 둔다(spec D5).

**Acceptance**: 새 노드 facade·`DEPENDS_ON`, 그리드 결정성, 모든 창이 연구 구간 안, 엠바고 계산, 상태 역행 금지,
이웃 정의(그리드 인접 8칸)와 이웃 평균 함수.

**Non-goal**: 저장·실행(P3-03 이후).

### P3-02 — 파라미터 값 배선

**Intent**: `BacktestRunSpec.parameter_values`를 더해 문서 기본값 대신 해소된 값으로 실행한다(spec D4).

**Acceptance**: `portfolio_design`의 파라미터 해소 한 곳만 변경, 범위·step·선택지 밖 422, 해소된 값이
`run_fingerprint`·시도 키를 가름, 저장 리비전 provenance 불변.

### P3-03 — 실험 저장소·유스케이스·API

**Intent**: `application/experiment_run`과 research DB의 experiments·trials·selection 테이블, 생성·조회·
trial 목록·취소 API.

**Acceptance**: 기반은 저장된 리비전만(인라인·동결은 422), 실패 trial 보존, 재시도는 새 attempt,
backtest_run outgoing port 소비(규칙 문서 허용 목록 갱신).

### P3-04 — 대기열 확장

**Intent**: #161 대기열을 전역 슬롯 상한·단일 실행 전용 슬롯·실험 간 공정 분배·시도 키 중복 제거·재시작
복구·실험 단위 일시정지/재개/우선순위로 넓힌다(spec D6).

**Acceptance**: `STRATEGY_WORKBENCH_RUN_SLOTS`, 굶주림 없음, 공정 분배, 중복 1회 실행, 재기동 복구, 일시정지
뒤 순서, 실험 SSE(keepalive), `_gc_policy` 주기 기반 재설계. `RunStatus` 값 불변.

### P3-05 — 워크포워드 실행

**Intent**: SplitSpec의 창마다 학습 → 선택 → 검증 실행을 이어 돌리고, 검증 창 수익률만 이어 붙인 곡선과
유지율을 만든다.

**Acceptance**: 창별 선택 기록, 워밍업은 검증 창 앞에서 읽되 성과 제외, 이어 붙인 곡선의 경계일 손계산 테스트
(#274 구간 지표 수정 위에서).

---

## 7. Phase 4 — 검증 통계

### P4-01 — PSR

**선행**: #274 4/4(샤프 표준오차) 머지. Metric Registry에 `probabilistic_sharpe`를 더하고 골든·ko·en 문구.

### P4-02 — DSR과 선택 기록

`domain/experiment`의 DSR 함수(원장의 N·시도 샤프 분산), selection record API, 인라인 초안 결과는 DSR 없음.
테스트는 수학 노트의 예시 값(0.86, 889세션, N 238, σ 0.25, 왜도 -0.41, 첨도 5.8 → 0.61)을 손계산 기준으로.

### P4-03 — 고원·민감도

이웃 평균, ±20% 민감도(가장 가까운 격자값), 봉우리 판정 기준.

### P4-04 — 용량 스윕

실험 종류 "용량 스윕"(초기 자본만 변주, N 제외), 한계 금액(최고 샤프의 절반) 계산, 금액별 참여율·충격 비용·
반올림 오차·미체결 비율.

### P4-05 — 팩터 회귀

시장 팩터(유니버스 시가총액 가중 수익률), 규모·가치·모멘텀 롱숏 팩터, OLS + HAC(Newey–West) 표준오차. 손계산
소형 데이터로 계수·t값 검증.

---

## 8. Phase 5 — 화면 (lang2 머지 신호 뒤)

각 PR은 디자인보드의 해당 보드를 따른다. 스토리 태그 e2e를 같은 PR에 넣는다.

| PR | 범위 | 스토리 |
|---|---|---|
| P5-01 | `/research/experiments` 목록·대기열·새 실험·모니터·완료 알림, 내비 "실험" 활성화 | US-SM-13, US-SM-14, US-CS-09 |
| P5-02 | 결과 화면 검증 카드(단일 실행 빈 칸·“튼튼한지 확인하기”·계산 근거 펼침), AI 결과 설명에 검증 요약 | US-DM-10, US-DM-11 |
| P5-03 | 백테스트 이력 종류 칼럼·필터, 전략 이력 시도 원장 탭·계열 합치기 | US-SM-11 |
| P5-04 | 후보 탐색 히트맵(팔레트 토큰), 봉우리, 창별 선택, selection 이유 | US-CS-10 |
| P5-05 | 실행 설정 잠금 UX(시작일 교정 버튼)·비용 칸 어휘·시도 영향 미리 알림 | US-SM-12 |
| P5-06 | 용량 스윕 화면, 팩터 회귀 표 | US-CS-11, US-CS-12 |
| P5-07 | IDE 제목 옆 계열 시도 배지, 상단 바 “실험으로 보내기”(lang2 P4-04 뒤), 레시피 탐색 토글(lang2 P5-02 뒤) | US-SM-11 |

## 9. Phase 6 — 홀드아웃 개봉

### P6-01 — 개봉 backend

사양 동결·기준 사전 등록·개봉 원장·되돌릴 수 없는 열람 상태, 봉인 구간을 여는 유일한 경로(연구 구간 잠금의
명시 예외), 개봉 뒤 재실행 분류.

### P6-02 — 개봉 화면

디자인보드 ⑥의 4단계 흐름과 검증 카드 미달 항목 표시. 스토리 US-CS-13.
