---
plan_version: 2
project: ai-assistant
project_status: IN_PROGRESS
current_phase: P0,A,C
current_pr: P0-01,A-01,A-07,C-01
active_prs: [P0-01, A-01, A-07, C-01]
parallel_window: [P0-01, A-01, A-07, C-01]
last_updated: 2026-09-26T13:48:24+09:00
planned_prs: 14
merged_prs: 0
approved_prs: 1
progress_percent: 0
---

# AI 어시스턴트 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P0,A,C` |
| Current/next PR | `P0-01,A-01,A-07,C-01` |
| Active PR | `P0-01, A-01, A-07, C-01` |
| Progress | `0 / 14 merged (0%)` |
| Approved | `1 / 14` |
| Aggregated at | `2026-09-26 13:48 KST` |
<!-- PLAN:SUMMARY:END -->

## 현재 결정

- 2026-09-20 제품 소유자 요청: 설정 화면에 Claude·Codex 연결, 그래프·YAML 어느 탭에서든 우측
  사이드바 AI 채팅. LLM은 들어온 데이터 + 인터넷 검색으로 시장을 조사해 전략을 제안한다. 확장
  여지를 위해 책임 분리를 우선한다.
- 참고 UI: `michelo.frontend` 설정 모달의 DB 프로파일 섹션(목록·활성 전환·추가·삭제·거부 사유
  toast). 그 저장소 main에는 Claude·Codex 연결 코드가 없어 패턴만 차용한다.
- 공급자 SDK는 outbound adapter에만. 도구는 application이 선언·실행. 검색은 v1에서 공급자 내장
  도구. 비밀은 backend 로컬 파일. 제안은 사용자의 "적용"으로만 문서에 들어간다(자동 적용 없음).
- P0-01 리뷰 반영(2026-09-20): 턴은 backtest_run 패턴(POST로 시작, GET `after_sequence` SSE, cancel은
  영속 상태 + 프로세스 내 신호, `AssistantTurnRunner`가 owner, 단일 워커 전제). 제안 적용은 업그레이드와
  같은 `replaceRange` 전체 교체 + stale 가드. 검색 `max_uses`·출력 토큰·벽시계 상한. base_url은 https만.
  `Failure.message`에 SDK 예외 문자열 금지. 출처 링크는 http/https만 + noopener. PR을 13개로 분할.
- Anthropic 기본 모델 `claude-opus-5`(adaptive thinking, effort high). OpenAI 기본 모델은
  `gpt-6-astra`(reasoning effort high, summary auto).
- OpenAPI와 frontend SDK는 A-04가 같은 PR에서 갱신한다(12절).
- reviewer 서브에이전트는 Opus로만. Phase 종료마다 SoT·책임분리 점검.
- 2026-09-21 A-07: 실연결 smoke의 게이트 환경 변수를 `RUN_LLM_LIVE`에서
  `STRATEGY_WORKBENCH_LIVE_SMOKE`로 통일했다. 이 저장소의 다른 배포 설정이 전부
  `STRATEGY_WORKBENCH_*` 접두사를 쓰고, 이름이 둘이면 문서와 코드가 서로 다른 변수를 가리키게
  된다. 기존 표기 4곳(spec·WORKFLOW·README·adapter docstring 2건)을 같이 고쳤다.
- 2026-09-21 A-07: **live smoke 미실행.** 구현 세션에 공급자 키가 없어
  `backend/scripts/assistant_live_smoke.py`를 한 번도 돌리지 못했다. 사용자가 키로 실행해야 하며,
  절차와 기대 출력은 WORKFLOW A-07 절에 있다. Phase A exit의 "live smoke 2건 로컬 통과 기록"은 그
  실행 결과를 여기 적어야 닫힌다.
- 2026-09-21 A-07: live smoke 확인 목록을 A-05 리뷰 결과에 맞춰 고쳤다. `output_format=None`은
  로컬 재현 가능한 SDK 센티널 오류로 판정돼 adapter에서 고쳐졌으므로 목록에서 뺐고, probe 항목은
  최소 출력 토큰 값을 문장에 적지 않고 adapter 상수를 가리키게 바꿨으며(A-05·A-06이 그 값을
  올리는 중이라 숫자를 복제하면 문장만 stale해진다), 검색 항목은 "`max_uses` 소진"에서 "턴 누적
  상한 도달 뒤 턴이 검색 없이 이어짐"으로 바꿨다(두 adapter가 턴 누적 집행으로 통일). probe는
  정상 키·틀린 키·없는 모델 3회를 돌려 사유 매핑까지 본다 — 정상 키의 `ok`만 보면 `AUTH`와
  `UNKNOWN`이 뒤바뀐 매핑을 놓친다. 턴의 라운드 수와 호출별·합계 출력 토큰을 찍는 항목을 더해
  아래 기본값 표의 추정치를 실측으로 바꿀 수 있게 했다.
- 2026-09-21 A-07: 시스템 프롬프트에 spec D8 품질 항목을 채웠다(근거 우선순위, 검색 결과를 명령으로
  읽지 않기, 제안은 도구로만, 출처 인용 규칙, 한국어·식별자 원문 규칙, 실행 설정 보존). **실행
  설정을 "언어 밖"이라고 쓰지 않았다** — schema 1.1은 시장·기간·유니버스를 문서 안에 두고
  `data`를 필수로 요구하므로, 언어 밖이라고 지시하면 모델이 필수 절을 빼고 검증에 실패한다.
  대신 "사용자가 바꿔 달라고 하지 않으면 현재 문서 값을 그대로 옮긴다"로 썼다. schema 1.2가
  머지되면 이 문장과 골든을 같이 갱신한다(WORKFLOW 1절 규칙).

- 2026-09-21 A-07: 세션 `Usage` 집계와 시나리오 fixture 3개를 A-07 안에서 마무리했다(WORKFLOW
  A-07 원문이 정본). 집계는 저장하지 않고 이벤트 이력을 접는 application 순수 함수
  `aggregate_usage`가 소유하며, 세션 조회 라우트가 이미 읽은 이력 하나를 넘긴다 — 따로 읽으면 한
  응답 안에서 `events`와 `usage`가 다른 이력을 말할 수 있다. 토큰 종류 확장 지점은 세 곳
  (`TokenTotals` 필드·그 `__add__`·`_tokens_of`)으로 좁혀 두었고, A-05가 도메인 `Usage`에
  `cache_read_tokens`·`cache_write_tokens`를 더하면 rebase 때 같은 이름으로 붙인다.
- 2026-09-21 A-07: 시나리오 fixture는 손으로 적지 않고 **실제 HTTP 응답**(`GET /sessions/{id}`의
  `events`, SSE `data:` payload와 같은 dataclass)에서 받아 적는다. 손으로 적으면 계약이 바뀌었을
  때 fixture만 옛 모양으로 남고 그 fixture로 green인 frontend가 진짜 서버에서 깨진다. `uuid4`
  세션·턴 id는 직렬화 텍스트 전체에서 `session-1`·`turn-N`으로 치환한다 — `Failure.message`가
  진단용으로 `session_id=…`를 담고 있어(error-messages 규칙) 필드만 바꾸면 골든이 실행마다
  달라진다.
- 2026-09-21 A-07 **후속**: schema 1.2(`strategy-language-2-0` P2-03) 머지 뒤 프롬프트의 실행 설정
  문장과 `backend/tests/fixtures/assistant/` 골든·시나리오 fixture를 1.2 문서로 갱신한다
  (WORKFLOW 1절 규칙). 담당은 그 시점의 A-07 후속 또는 B-05.
- 2026-09-21 A-07 리뷰 APPROVE 반영: P2 1건·P3 3건을 마무리 커밋 하나로 닫았다. 검색 상한 통지를
  검색으로 세지 않도록 집계에 분기를 넣고, 모델이 읽는 고정 문장 2건을 `_prompt.py`로 옮겨 골든에
  넣었으며, live smoke가 검색 상한을 상수가 아니라 주입값에서 읽게 하고, "이력 `events` == SSE
  `data:` 프레임"을 통합 테스트로 고정했다.

### A-07 backlog: 검색 상한 통지 전용 이벤트 (담당 B-03)

- **상황**: OpenAI(Codex) 프로파일이 활성인 세션에서 한 턴의 누적 웹 검색이 `max_search_uses`에
  닿아 adapter가 다음 호출의 도구 목록에서 `web_search`를 빼는 경로를 탄 뒤, 사이드바가 세션
  이력을 다시 읽을 때.
- **인풋**:
  1. `POST /api/v1/assistant/sessions/{id}/turns` — 검색을 상한 이상 유도하는 질문.
  2. 턴 종료 후 `GET /api/v1/assistant/sessions/{id}`.
- **에러 위치**: `backend/src/strategy_workbench/adapters/outbound/llm_openai/_turn.py` — 검색이
  아니라 **통지**를 `SearchActivity(query=SEARCH_BUDGET_EXHAUSTED_NOTICE, sources=())`로 흘린다
  (A-06이 "임시"라고 주석에 적은 우회다). 받는 쪽
  `backend/src/strategy_workbench/application/assistant_chat/_usage.py`의 `_is_search`가 그 문구를
  보고 검색에서 빼는 것으로 지금은 막아 두었다.
- **위험성**: 통지와 검색이 같은 이벤트 종류를 쓰는 한, 그 둘을 가르는 근거가 **문구 비교**다.
  문구는 골든이 잠그고 있어 조용히 바뀌지는 않지만, 같은 문구를 쓰는 다른 경로가 생기거나 번역이
  들어오면 집계가 다시 틀린다. 화면(B-03)도 통지를 검색 활동 칩으로 그려 사용자가 하지 않은 검색을
  본다. 데이터 손실·look-ahead는 아니고 표시 오차다.
- **해결**: `ChatEvent` union에 통지 전용 이벤트를 더하고 adapter가 그것을 흘린다. 그때
  `_usage.py`의 `_is_search` 분기와 `test_the_search_budget_notice_is_not_counted_as_a_search`를
  함께 지운다.
- **재현 test**: `backend/tests/application/test_assistant_usage.py::test_the_search_budget_notice_is_not_counted_as_a_search`
  (현재 동작을 고정하는 테스트이며, 전용 이벤트가 생기면 이 테스트가 없어진다)

- 2026-09-21 A-07 **이관**: 검색 상한 8과 Anthropic adapter `MAX_PAUSE_RESUMES = 5`가 서로를 모르는
  건은 A-05로 넘겼다.

- 2026-09-21 C-01: Phase A 종료 감사의 비차단 10건을 닫았다. 문서 5건(NB-1~NB-4·NB-10)은
  stale 표기와 사실과 반대인 이월 체크박스였고, 나머지는 `MIN_CALL_OUTPUT_TOKENS` owner 단일화
  (NB-5), CI의 lint·type 범위와 no-extras 대상 확대(NB-6·NB-7), 비밀 누락 422와 anthropic env
  차단 전수 테스트(NB-8·NB-9)다. NB-11은 브랜치 상태라 머지 직전 rebase에서 처리한다.
- 2026-09-21 C-01: `backend/pyproject.toml`의 pyright `include`에 `"tools"`를 더했다.
  `.claude/rules/code-style.md`가 `[tool.pyright]` 변경에 사용자 확인을 요구하는 항목이다. 이
  변경은 룰을 끄거나 모드를 낮추는 것이 아니라 **검사 범위를 넓히는** 방향이며, 팀 리드를 통해
  확인을 받고 진행했다.
- 2026-09-21 C-01: `MIN_CALL_OUTPUT_TOKENS`의 owner가 `domain/assistant/_models.py`로 옮겨졌다.
  adapter가 같은 이름을 모듈 수준에서 다시 선언하면 `tests/architecture/test_turn_budget_constants.py`가
  실패한다. 공급자마다 다른 값이 정말 필요해지면 adapter 리터럴이 아니라 `TurnRequest`로 승격한다.
- 2026-09-26 C-01 1차 리뷰 반영: PLAN 페이즈 표 C 행이 cp949 깨진 글자로 커밋돼 있었다. 진행
  스크립트의 한글 값을 기존 A·B 행처럼 ASCII로 바꾸고, 스크립트가 자기 파일에 비ASCII 바이트가
  있으면 `-Check`를 포함해 멈추게 했다. NB-9 기준선 테스트는 SDK 하위 클래스로 전송을 바꿔 끼워
  auto-discovery 체인을 한 번도 지나지 않았다. 기본 클래스 인스턴스를 쓰고 `ANTHROPIC_API_KEY`·
  `ANTHROPIC_AUTH_TOKEN`을 뺀 경우를 더해, SDK gate 조건을 지우는 돌연변이에서 빨개지게 했다.
  B-05가 매뉴얼에 새로 넣은 `RUN_LLM_LIVE=1`도 `STRATEGY_WORKBENCH_LIVE_SMOKE`로 고쳤다.

### A-07 기본값 확정 근거 (2026-09-21)

`backend/tests/fixtures/assistant/` 골든의 실측 길이로 검토했고 **다섯 값 모두 그대로 둔다.**
토큰 수는 한글 1음절≈1토큰, ASCII 3.5자≈1토큰으로 보수적으로 환산한 **추정치**다. live smoke의
`turn_budget_measurements` 항목이 찍는 라운드 수·호출별 `usage.output_tokens`·턴 합계로 이 표를
실측으로 바꾼다.

| 값 | 기본값 | 근거 |
|---|---:|---|
| 호출당 출력(`DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL`) | 16000 | 한 호출이 내야 하는 최대치는 `propose_strategy` 한 번이다. 제안 YAML 골든(`quality_momentum.yaml`) 869자 ≈ 250토큰, 여기에 제목·요약·근거(한글 산문)와 사고 요약을 더해도 1500~2500토큰이다. 6배 이상 여유 |
| 턴 출력(`DEFAULT_MAX_TURN_OUTPUT_TOKENS`) | 64000 | 호출당 상한의 4배. 최장 경로는 도구 3회 + 검색 + 검증 3회 + 제출 3회이고 제출 라운드만 1500~2500토큰이므로 합계 추정 11000~15000토큰. 사고 요약이 몇 배로 늘어도 닿지 않는다 |
| 도구 라운드(`DEFAULT_MAX_TOOL_ROUNDS`) | 12 | 라운드는 우리 도구 호출(`stop_reason == "tool_use"`)만 세고 서버 검색은 세지 않는다. 결정적 최단 경로는 읽기·필드·팩터·검증·제출 5라운드, 제안 재시도 상한(3회)까지 쓰면 9라운드. 12는 3라운드 여유 |
| 검색(`DEFAULT_MAX_SEARCH_USES`) | 8 | 한 주제에 대한 교차 확인 2~3건 × 팩터 2~3개. 상한 집행은 공급자 쪽이며 Anthropic은 서버가, OpenAI는 adapter가 센다 |
| 타임아웃(`DEFAULT_TURN_TIMEOUT_SECONDS` + `DEFAULT_TURN_GRACE_SECONDS`) | 300 + 10 | 러너가 이벤트 사이에서만 보는 벽시계 상한. adapter HTTP 타임아웃 120초보다 길어 호출 하나가 멈춰도 러너가 깨어난다 |

남은 위험 두 가지는 live smoke로만 판정된다.

1. **타임아웃 여유가 가장 얇다.** 위 최단 경로도 공급자 호출 5~6회다. `effort: high` 한 호출이
   60초를 쓰면 300초에 닿는다. live smoke의 `probe` 지연과 턴 소요를 보고, 모자라면 A-01 상수가
   아니라 `build_assistant_services`에서 `AssistantTurnRunner(timeout_seconds=...)`로 주입해
   올린다(주입 자리는 이미 있다).
2. ~~검색 8회와 `MAX_PAUSE_RESUMES = 5`의 관계가 확인되지 않았다.~~ **해소됨**(2026-09-21,
   커밋 `57272513`). `llm_anthropic/_turn.py`의
   `_max_total_pause_resumes(max_search_uses) = max_search_uses + MAX_PAUSE_RESUMES`가 두 상한의
   결합을 구조적으로 끊었다 — 재개 예산이 검색 예산을 먹지 않는다. 재개 상한 케이스가
   `tests/test_adapters_llm_anthropic.py`에서 그 동작을 고정한다. live smoke에서 다시 볼 항목이
   아니다.

## 상태 값

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)의 상태 값과
tracker 규칙을 따른다(`PLANNED`·`READY`·`WAITING`·`IN_PROGRESS`·`SELF_CHECK`·`IN_REVIEW`·
`CHANGES_REQUESTED`·`APPROVED`·`MERGED`·`PAUSED`).

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package | 1 | 0 | `APPROVED` |
| A | Backend: ports, storage, HTTP, providers | 7 | 0 | `IN_PROGRESS` |
| B | Frontend: settings, entity, sidebar, e2e | 5 | 0 | `WAITING` |
| C | Phase A audit follow-up | 1 | 0 | `SELF_CHECK` |
| **Total** |  | **14** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-01` |
| Intent | 기획 패키지·spec을 main에 올린다 |
| Acceptance | WORKFLOW P0-01 |
| Non-goals | 코드 변경 |
| Branch/worktree | `docs/ai-assistant-plan` (PR #166) · A-01/A-02는 `wt-ai-a01` / `feat/ai-a-01-domain-ports` |
| Base SHA | `5f97f8c` (origin/main) |
| Head SHA | 4차 APPROVE 뒤 잔여 P2 반영 커밋 |
| Diff stat | 문서 8개 |
| Focused tests | `tools/update-plan-progress.ps1 -Check` |
| Full gate | CI(문서만) |

---

## P0 — 기획 패키지

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P0-01` | 기획 패키지·설계 spec·SoT 행 예약 | 없음 | `APPROVED` | [#166](https://github.com/Nochiski/Quant_study/pull/166) · `review_ai_p0_01` 4차 APPROVE(1~3차 REQUEST_CHANGES 전부 해소, 잔여 P2 1건 머지 전 반영) |

## A — backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `A-01` | `domain/assistant` 타입·도구 계약, `application/assistant_chat` 포트 5종·프로파일 서비스, SDK import 게이트, 경계 규칙 목록 | P0-01 | `IN_PROGRESS` | 구현자 `impl-ai-a01`, 워크트리 `wt-ai-a01`, 브랜치 `feat/ai-a-01-domain-ports` |
| [ ] | `A-02` | 채팅 유스케이스·컨텍스트 빌더·프롬프트·턴 러너, 가짜 공급자 테스트 | A-01 | `WAITING` | — |
| [ ] | `A-03` | `assistant_sqlite`·`secrets_local` adapter | A-02 | `WAITING` | — |
| [ ] | `A-04` | `/api/v1/assistant/*` + 턴 시작·SSE·취소, bootstrap, OpenAPI·SDK | A-03 | `WAITING` | — |
| [ ] | `A-05` | `llm_anthropic` adapter | A-04 | `WAITING` | — |
| [ ] | `A-06` | `llm_openai` adapter | A-05 | `WAITING` | — |
| [ ] | `A-07` | 프롬프트 골든 fixture·재생성 도구, D8 품질 항목, live smoke 스크립트, 턴 상한 기본값 확정 | A-06 | `SELF_CHECK` | 구현자 `impl-ai-a07`, 워크트리 `wt-ai-a07`, 브랜치 `feat/ai-a-07-prompt-fixtures` |

Phase exit:

- [ ] 가짜 공급자 SSE 시나리오 3개 green(재개 포함), live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 blocking 0.

## B — frontend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `B-01` | `/settings` 페이지, `configure-ai-providers` 섹션, `entities/assistant` 프로파일 query | A-07 | `WAITING` | — |
| [ ] | `B-02` | 세션·턴 query, 생성 SDK SSE 리더(재개·멱등), 이벤트 리듀서, property test | B-01 | `WAITING` | — |
| [ ] | `B-03` | `assist-strategy` 사이드바 feature(렌더 안전·취소 확인) | B-02 | `WAITING` | — |
| [ ] | `B-04` | IDE `assistant` 슬롯, 제안 적용(`replaceRange` + stale 가드)·미리보기·적용 후 백테스트 | B-03 | `WAITING` | — |
| [ ] | `B-05` | e2e(MSW 공급자, 재개·취소), 매뉴얼·README·SoT·features README, B-01 이관 접근성 2건(R2-3·R2-4) | B-04 | `WAITING` | — |

Phase exit:

- [ ] 완료 정의 1~5 기록.
- [ ] SoT·책임분리 점검 blocking 0.

## C — Phase A 감사 후속

Phase A 종료 감사(`audit_ai_phase_a.md`)는 **blocking 0**으로 PASS했고, 비차단 11건 중
NB-1~NB-10을 C-01이 닫는다. NB-11은 브랜치 상태(스택 base 격차)라 머지 직전 rebase에서
처리한다 — `PLAN.md` 충돌은 base 쪽 값으로 해소한다.

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `C-01` | 감사 비차단 10건: stale 표기·이월 체크박스 정정, `MIN_CALL_OUTPUT_TOKENS` owner 단일화, CI lint·type 범위와 no-extras 대상 확대, 비밀 누락 422·anthropic env 전수 테스트 | A-07 | `SELF_CHECK` | 구현자 `impl-ai-a07`, 워크트리 `wt-ai-c01`, 브랜치 `feat/ai-c-01-audit-followup` |

Phase exit:

- [ ] NB-1~NB-10 닫힘, 각 항목이 코드·문서·테스트 중 어디서 닫혔는지 PR 본문에 기록.
- [ ] no-extras job 확대판이 SDK 없는 환경에서 green.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|
| P0-01 | `review_ai_p0_01` | 1 | REQUEST_CHANGES | P1 3(적용 경로·턴 owner·PR 분할), P2 10, P3 8 → 전부 반영 |
| P0-01 | `review_ai_p0_01` | 2 | REQUEST_CHANGES | 1차 전부 해소 확인. 신규 P1 2(EventSource 재개 회귀·stale 가드 진행 경로), P2 3, P3 5 → 반영 |
| P0-01 | `review_ai_p0_01` | 3 | REQUEST_CHANGES | 2차 전부 해소 확인. 신규 P1 1(토큰 예산 축소가 포트로 구현 불가), P2 2, P3 4 → 반영 |
| P0-01 | `review_ai_p0_01` | 4 | APPROVE | 3차 전부 해소. 잔여 P2 1(`max_tool_rounds` 집행 주체) 반영, P3 3(D9 분리 반영, OpenAI 통지 문구·기본값은 A-06·A-07) |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| P0-01 | `update-plan-progress.ps1 -Check` | 통과 | 2026-09-20 |

## 변경 기록

- 2026-09-21 — A-05 2차 리뷰 반영. **검색 예산 집행 방식을 유지하기로 결정**: 호출마다
  `max_uses`를 잔량으로 줄이므로 검색이 한 번 일어나면 이후 호출의 캐시 접두가 깨진다(시스템
  프롬프트를 캐시 쓰기로 과금). 대안("`max_uses` 고정 + 소진 시에만 도구 제거")은 접두를 턴당
  한 번만 깨지만 초과를 `2 × max_search_uses − 1`까지 허용한다. 두 손해가 같은 자릿수라
  추정만으로 고르지 않고, **A-07이 `Usage`의 캐시 두 칸으로 실측해 다시 본다**.
  같은 리뷰에서 A-07로 넘긴 항목 5건을 WORKFLOW A-07 Acceptance에 체크 항목으로 적었다
  (선언되지 않은 서버 도구 결과 블록이 든 history를 공급자가 받는지가 최우선).

- 2026-09-21 — A-06 OpenAI 기본 모델을 `gpt-6-astra`로 확정. 근거는 설치된 `openai` 3.16.2의
  `ChatModel` 목록 첫 항목이자 `Response.model` docstring 예시이고, 공식 모델 문서가 "가장 유능한
  모델, 어디서 시작할지 모르겠으면 GPT-6 Astra"로 소개한다는 것. 화면 이름이 "Codex"지만 spec D4가
  "최신 GPT 모델"을 요구했고 이 기능의 일이 코딩이 아니라 리서치라 codex 계열 대신 범용 플래그십을
  골랐다. 실호출 확인은 A-07 live smoke 항목(모델 이름 수용, probe의 `max_output_tokens` 하한,
  `developer` 통지 반응, 추론 항목 재전송 수용).
- 2026-09-21 — A-06 probe의 `max_output_tokens`를 API 최솟값 16이 아니라 64로 둔다. 추론 모델은
  본문 전에 추론 토큰을 먼저 쓰므로 상한이 추론분보다 작으면 400이 날 수 있고, 그러면 연결
  테스트가 "키가 틀렸다"와 "상한이 낮다"를 구분하지 못한다. 모델별 실제 최솟값은 A-07 live
  smoke에서 확인한다.
- 2026-09-20 — P0-01 4차 APPROVE. 잔여 P2(`max_tool_rounds` 집행을 adapter로)·D9 값/집행 분리 반영. CI 통과 후 머지 대상.
- 2026-09-20 — P0-01 3차 리뷰 반영: 토큰 예산 집행을 adapter로(서비스는 Usage 기록만), Failure 우선순위(첫
  Failure만 턴 상태), 409는 backstop·프론트는 이력 복구·`sseMaxRetryAttempts`, OpenAI 검색 상한은 도구 목록 제거,
  기본값은 A-07 실측 후 확정, A-05 체크 항목화.
- 2026-09-20 — P0-01 2차 리뷰 반영: SSE 리더는 생성 SDK 클라이언트(`Last-Event-ID`), RUNNING 턴 없으면
  409·keepalive, 적용 전 확인(미리보기·덮어쓰기), 턴 토큰 예산·`OUTPUT_TRUNCATED`, `accepted_sequence` 정의.
- 2026-09-20 — P0-01 1차 리뷰 반영: spec D2·D3·D4·D5·D6·D7·D9 개정, WORKFLOW 13 PR로 분할(A 7, B 5),
  PLAN·README의 lang2 링크 제거(정본은 yaml-ui WORKFLOW 13.7절), SoT `paths:`·행 문구, 경계 규칙 목록.
- 2026-09-20 — P0-01 PR #166 생성, 리뷰 배정. A-01 구현 착수(P1·P2 스택과 독립이라 병렬).
- 2026-09-20 — 패키지 생성. 제품 소유자 요청(설정에서 Claude·Codex 연결, 우측 사이드바 AI 채팅,
  검색 기반 전략 제안, 책임 분리)을 spec과 Phase 0·A·B로 정리.

## 갱신 절차

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다.
집계는 `powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/ai-assistant/tools/update-plan-progress.ps1`
(`-Check`는 검증만).
