---
plan_version: 2
project: ai-assistant
project_status: SELF_CHECK
current_phase: C
current_pr: C-02
active_prs: [C-02]
parallel_window: [C-02]
last_updated: 2026-09-26T14:35:27+09:00
planned_prs: 15
merged_prs: 14
approved_prs: 14
progress_percent: 93
---

# AI 어시스턴트 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `SELF_CHECK` |
| Current phase | `C` |
| Current/next PR | `C-02` |
| Active PR | `C-02` |
| Progress | `14 / 15 merged (93%)` |
| Approved | `14 / 15` |
| Aggregated at | `2026-09-26 14:35 KST` |
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

### A-07 backlog: 검색 상한 통지 전용 이벤트 (담당 B-03 → C-03 재배정)

B-03 행·리뷰 기록·변경 기록 어디에도 이 건의 처리나 이관 기록이 없었다(Phase B 감사 NB-4).
2026-09-26 담당을 "AI 후속 C-03(미착수)"으로 옮겼고, 현재 위치의 4요소 기록은 C 절 backlog가
정본이다. 아래는 A-07 시점의 원 기록이다.

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
| P0 | Planning package | 1 | 1 | `MERGED` |
| A | Backend: ports, storage, HTTP, providers | 7 | 7 | `MERGED` |
| B | Frontend: settings, entity, sidebar, e2e | 5 | 5 | `MERGED` |
| C | Phase A audit follow-up | 2 | 1 | `SELF_CHECK` |
| **Total** |  | **15** | **14** | **93%** |
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
| [x] | `P0-01` | 기획 패키지·설계 spec·SoT 행 예약 | 없음 | `MERGED` | [#166](https://github.com/Nochiski/Quant_study/pull/166) · `review_ai_p0_01` 4차 APPROVE(1~3차 REQUEST_CHANGES 전부 해소, 잔여 P2 1건 머지 전 반영) |

## A — backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `A-01` | `domain/assistant` 타입·도구 계약, `application/assistant_chat` 포트 5종·프로파일 서비스, SDK import 게이트, 경계 규칙 목록 | P0-01 | `MERGED` | [#169](https://github.com/Nochiski/Quant_study/pull/169) · `e8c6895` · `review_ai_a_01` 3차 APPROVE(비차단 P3 2건은 A-02 브랜치에 적재) · CI 대기 |
| [x] | `A-02` | 채팅 유스케이스·컨텍스트 빌더·프롬프트·턴 러너, 가짜 공급자 테스트 | A-01 | `MERGED` | [#170](https://github.com/Nochiski/Quant_study/pull/170) · `8324ebc`(84570df + 후속 2: A-01 P3·A-02 P3·CI flake 수정) · `review_ai_a_02` 2차 APPROVE + 후속 확인 APPROVE · CI 대기 |
| [x] | `A-03` | `assistant_sqlite`·`secrets_local` adapter | A-02 | `MERGED` | [#171](https://github.com/Nochiski/Quant_study/pull/171) · `d52436b7`(A-02 `8324ebc` 위 rebase, 내용 동일) · `review_ai_a_03` 2차 APPROVE · POSIX 모드 비트는 Linux CI로 확인 |
| [x] | `A-04` | `/api/v1/assistant/*` + 턴 시작·SSE·취소, bootstrap, OpenAPI·SDK | A-03 | `MERGED` | [#174](https://github.com/Nochiski/Quant_study/pull/174) · `9f39faec`(A-03 `d52436b7` 위, 2차 P3 4건 반영) · `review_ai_a_04` 2차 APPROVE · CI 대기 |
| [x] | `A-05` | `llm_anthropic` adapter | A-04 | `MERGED` | [#175](https://github.com/Nochiski/Quant_study/pull/175) · `c0219894`(A-04 최종 `9f39faec` 위 15커밋) · `review_ai_a_05` 3차 APPROVE(세 라운드 24건 전부 닫힘, 비인증 헤더 통과는 docstring 한 문장 P3 — A-06 rebase 뒤 A-05에 fast-forward) · live smoke 최우선: 선언되지 않은 서버 도구 결과 블록 history 수용 여부 |
| [x] | `A-06` | `llm_openai` adapter | A-05 | `MERGED` | [#178](https://github.com/Nochiski/Quant_study/pull/178) · `6ada372f`(A-05 최종 `c0219894` 위 14커밋; org/project 헤더 `omit`, env 7종 기준선 테스트, spec D6 전수 표, P3 4 + A-05 P3 docstring 적재) · `review_ai_a_06` 2차 APPROVE · 확정 |
| [x] | `A-07` | 프롬프트 최종본, 컨텍스트 품질, 시나리오 fixture 3개 | A-06 | `MERGED` | 구현 완료(로컬 `9250699f`, A-05 위 9커밋, `total_input_tokens` wire 필드·분리형 docstring) → A-06 최종 `6ada372f` 위 rebase·캐시 필드·재생성 뒤 push·PR( 골든 fixture·live smoke·기본값 근거·세션 Usage 집계(`aggregate_usage` 순수 함수, `SessionHistoryView.usage`)·시나리오 fixture 3개(실제 HTTP 응답에서 받아 적음), pytest 1821·ruff·pyright 0) → A-06 tip 위 rebase·캐시 필드 반영 뒤 push·PR · live smoke 미실행(키 없음, 사용자 실행 필요) · **A-06 최종 `6ada372f` 위 replay 완료 `4fcad54c`**(10커밋, 33파일 +2971/−27; `UsageView` 성분 2칸·`TokenTotalsView` 성분+`total_input_tokens`, `MODEL_NOTICES`에 `search_budget_exhausted`, pytest 1957) · [#179](https://github.com/Nochiski/Quant_study/pull/179) · `review_ai_a_07` 1차 APPROVE(P2 1·P3 3) · 마무리 `e57ec183`(리뷰 4건) + `5009a03c`(B-05 발견: 첫 이벤트 전 취소 `Failure(CANCELLED)` 저장, 공급자 알린 취소 중복 억제; pytest 1964) — **최종 `5009a03c`**(`review_ai_a_07` 2차 APPROVE 유지, 돌연변이 3건 포착), B 스택 rebase 대상 |

Phase exit:

- [ ] 가짜 공급자 SSE 시나리오 3개 green(재개 포함), live smoke 2건 로컬 통과 기록.
- [ ] SDK import 게이트·비밀 평문 검사 green.
- [ ] SoT·책임분리 점검 blocking 0.

## B — frontend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `B-01` | `/settings` 페이지, `configure-ai-providers` 섹션, `entities/assistant` 프로파일 query | A-07 | `MERGED` | 구현 완료(로컬 `9f536bb4`, A-04 `645efcb1` 위, 18 파일 +1542/−10, 게이트·e2e 19/19) · `review_ai_b_01` 1차 REQUEST_CHANGES(P1 1·P2 2) 반영 완료(로컬 `7fcc584e`: 키 실은 요청은 plain async, 접힘 시 base_url 미전송, 삭제 확인 포커스·live region, P3 4건, R2-1 `probingIds` Set·R2-2 제목 위계) · 2차 APPROVE_WITH_COMMENTS · R2-3·R2-4는 B-05 · cascade 뒤 A-07 위 rebase·push·PR · **A-07 최종 `4fcad54c` 위 replay `44ce20a4`**(7커밋, 충돌 없음, api:generate diff 0, vitest 659, 잠금 아래 e2e 19/19) · [#180](https://github.com/Nochiski/Quant_study/pull/180) · `review_ai_b_01` 3차 APPROVE(비차단 2: R2-3·R2-4 이관을 WORKFLOW B-05 acceptance에, 주석 과장) · 비차단 2건 커밋 + A-07 최종 `5009a03c` 위 rebase **최종 `5dc43c8b`**(8커밋, e2e 19/19, api:generate diff 0) — B-02 replay 대상 |
| [x] | `B-02` | 세션·턴 query, 생성 SDK SSE 리더(재개·멱등), 이벤트 리듀서, property test | B-01 | `MERGED` | 구현 완료(로컬 `cb852032`, B-01 최종 `7fcc584e` 위 12커밋) · `review_ai_b_02` 3차 APPROVE(StrictMode probe 포함, e2e 19/19) · 어휘 단일 입구는 entity · cascade 뒤 push·PR · B-01 최종 `5dc43c8b` 위 replay + A-07 캐시 성분 반영 + DEFECT-AI-B05-001 정착 수정 **`22a86e39`**(14커밋, vitest 694, e2e 19/19) · [#182](https://github.com/Nochiski/Quant_study/pull/182) · `review_ai_b_02` 4차 APPROVE(P3 2: 훅 docstring 옛 규칙, PR 본문 bullet·결함 id) · docstring 정정 **최종 `0e460b97`** — B-03 replay 대상 |
| [x] | `B-03` | `assist-strategy` 사이드바 feature(렌더 안전·취소 확인) | B-02 | `MERGED` | 구현 완료(로컬 `ecd530a6`, B-02 최종 `cb852032` 위 9커밋, vitest 728, 훅 API 적응: exhausted 재연결·rejected 정착·droppedFrames 경고, vitest 714·e2e 19/19) · `review_ai_b_03` 1차 REQUEST_CHANGES(P1 1: 이력보다 202가 먼저 오면 turns 순서 축이 갈라져 질문↔답 짝 밀림 — 리듀서 정렬을 B-03에서 수정, P2 4, P3 6) 반영 → 2차 REQUEST_CHANGES(1차 10/11 해소; 새 P1: 취소·실패 턴에도 완료 announce, P2: 제안 카드 aria-live 미적용, P3 4) 반영(결말별 status·제안 카드 aria-live·P3 4·이름 없는 section) → 3차 확인 중 · 후속 backlog: `ChatMessageView.turn_id` · `review_ai_b_03` 3차 APPROVE(P2 1: status 라이브 영역 상시 렌더, P3 3) · 마무리 커밋 후 **최종 `1445d982`**(B-02 `cb852032` 위 10커밋, vitest 729, e2e 19; 사이드바 자체 `<h2>` 제거, WORKFLOW B-04 Acceptance에 슬롯 landmark·`onClose` 위임 계약 2줄) · B-02 최종 `0e460b97` 위 replay **`92ce346a`**(`reduceTurn` 충돌 병합, 취소 하네스 `Failure(CANCELLED)` 보강, usage 픽스처; vitest 732, e2e 19/19) · [#185](https://github.com/Nochiski/Quant_study/pull/185) · `review_ai_b_03` 4차 APPROVE(P3 3은 C-01로) — **최종 `92ce346a`** |
| [x] | `B-04` | IDE `assistant` 슬롯, 제안 적용(`replaceRange` + stale 가드)·미리보기·적용 후 백테스트 | B-03 | `MERGED` | 구현 완료(로컬 `c6aaf55e`, B-03 `df7b25f4` 위 8커밋: 슬롯·적용 훅·페이지 배선·적용 후 백테스트·오버레이·spec 정정·사이드바 실장착·App 통합 테스트·`readContext` live 읽기·알림을 문서 notice 슬롯으로, vitest 762·e2e 19/19(어시스턴트 라우트 200 확인)·기준선 4장 재생성) · `review_ai_b_04` 1차 REQUEST_CHANGES(P1 3: 적용 알림이 패널 절반 차지·미소거, 좁은 화면 서랍 2개 동시, 드래그 임계 초과 시 되돌릴 수 없음; P2 2: en 한글, onClose 미배선; P3 7) → 반영 중 · 핵심 적용 설계(전체 범위 교체·undo 1스텝·live 재확인·stale 중단)는 승인 수준 · 2차 6건 반영 + B-03 최종 `1445d982` 위 replay **`c9fd1f3d`**(10커밋, vitest 776, e2e 19; 우측 패널 단일 열림, 오버레이 판정 기본 폭 상수, `onClose` 위임, 슬롯 헤더 보이는 h2) · `review_ai_b_04` 3차 REQUEST_CHANGES(2차 6건 해소 확인; 새 P1 1·P2 2·P3 3) · 후속 **`47041511`**(펼침 포커스·함수 슬롯 기본, 동일 제안 `changed=false` 알림+체인 진행, Acceptance 정정, 포맷 기억 제거; vitest 780) · `review_ai_b_04` 4차 APPROVE(주석 1줄 정정은 B-03 새 tip 위 replay 때) · 주석 정정 `d29cf637` · B-03 최종 `92ce346a` 위 replay **`25fc7edc`**(12커밋, vitest 783, e2e 19/19) · [#186](https://github.com/Nochiski/Quant_study/pull/186) · `review_ai_b_04` 5차 APPROVE — **최종 `25fc7edc`**, B-05 rebase 대상 |
| [x] | `B-05` | e2e(backend 대본 공급자 `llm_scripted`, 재개·취소), 매뉴얼·README·SoT·features README | B-04 | `MERGED` | 구현자 `impl-ai-b05`, 워크트리 `wt-ai-b05`, 브랜치 `feat/ai-b-05-e2e-docs`(임시 base B-04 로컬 `8ee4808a`; 가짜 공급자 env 배선으로 e2e, A-07 시나리오는 읽기 참조) · 구현 완료(로컬 4커밋: e2e 잠금·포트 override(P1-05 `aa57028e` 동일 hunk + CORS), 대본 공급자 adapter `llm_scripted`(`STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1`), e2e 시나리오 4 + B-01 이관 2, 매뉴얼 9절·README; e2e 23/23(포트 18000·15173), vitest 778, pytest 1411) · B-04 `c9fd1f3d` 위 rebase 완료(5커밋, e2e 24/24·vitest 794·pytest 1718; B-04 슬롯 계약 반영, 상주 status로 취소 문구 즉시 확인) · push **`0b982778`** · `review_ai_b_05` 1차 APPROVE 조건부(P1: 결함 id가 `database/` DEFECT-B05와 충돌 → `DEFECT-AI-B05-001`; P2 4: 재개 시나리오 여유 5초, 가짜 공급자 표식 없음, 재연결 중복 미검출, 적용 격리 미단언; P3 3) · B-04 `47041511` 위 rebase **`c5b4ddd0`**(시나리오 4개로 합침: 동일 제안 재적용 "바뀐 내용 없음" 알림 + 백테스트 진행 단언, 펼침 포커스 단언; e2e 23/23, vitest 798, pytest 1760) · 리뷰 후속 8건 **`b1412682`**(id 개명, 대본 지연 배율 20초+, 가짜 공급자 표식은 모델명+warning 로그(probe message는 spec D2로 불가), `toHaveText`, 제목 줄만 변경 단언, P3 3; 취소 시나리오는 A-07 수정 전 base라 "턴이 멈췄다" 단언으로; e2e 23/23, pytest 1762) · `review_ai_b_05` 2차 APPROVE(P2 1: 취소 대체 단언이 서버 취소 무시 회귀를 못 잡음 → reload 뒤 status "답변을 중지했습니다." 단언) · B-04 `25fc7edc` 위 rebase + 취소 사유 단언 조임(reload 없이) **`ea7b41d5`**(6커밋, e2e 23/23, vitest 801, pytest 1838) · [#189](https://github.com/Nochiski/Quant_study/pull/189) · R2-001(취소 증명을 서버 사실 둘로: 재조회 이력의 취소 사유 + 다음 턴 409 없음) + P1-05 `cf56b5b4` e2e 파일 바이트 동일 재복사 **`d84b0eda`**(vitest 809, e2e 23/23) · `review_ai_b_05` 3차 APPROVE(취소 단언 2가 러너 슬롯 계약으로 회귀 포착 확인, 재복사 9파일 blob 동일; P3 2: prettier 1줄, e2e README 잠금 문장) → P1-05 최종 `a263276b`(경합 테스트 신호화) 재복사 **최종 `2d5f2b26`**(e2e 인프라 6파일 blob 동일, README는 B-05 AI 절 때문에 3-way 병합, prettier 4곳; vitest 812) |

Phase exit:

- [ ] 완료 정의 1~5 기록.
- [ ] SoT·책임분리 점검 blocking 0.

## C — Phase A·B 감사 후속

Phase A 종료 감사(`audit_ai_phase_a.md`)는 **blocking 0**으로 PASS했고, 비차단 11건 중
NB-1~NB-10을 C-01이 닫는다. NB-11은 브랜치 상태(스택 base 격차)라 머지 직전 rebase에서
처리한다. `PLAN.md` 충돌은 파일 단위가 아니라 절 단위로 합친다. PR 행·Review 기록·변경 기록은
리드 판(base)을 쓰고, 스택에만 있는 절(A-07 기본값 확정 근거, A-07 backlog, 이 C 절과 그
backlog)은 보존한다. 파일 단위로 한쪽을 고르면 다른 쪽 기록이 조용히 사라진다(Phase B 감사 NB-5).

Phase B 종료 감사(`audit_ai_phase_b.md`)도 **blocking 0**으로 PASS했다. 비차단 8건 중 NB-3은
C-01이 매뉴얼을 고치며 닫았고(C-02가 스크립트 경로와 변수 누락 시 조용히 끝난다는 경고를
보탰다), NB-1·NB-2·NB-5~NB-8은 C-02가 닫는다. NB-4는 코드를 고치지 않고
아래 backlog 2건으로 담당을 정한다.

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `C-01` | 감사 비차단 10건: stale 표기·이월 체크박스 정정, `MIN_CALL_OUTPUT_TOKENS` owner 단일화, CI lint·type 범위와 no-extras 대상 확대, 비밀 누락 422·anthropic env 전수 테스트 | A-07 | `MERGED` | 구현자 `impl-ai-a07`, 워크트리 `wt-ai-c01`, 브랜치 `feat/ai-c-01-audit-followup` · [#190](https://github.com/Nochiski/Quant_study/pull/190) · 최종 `707cd4f9` · main 머지 `a3cc5f8b` |
| [ ] | `C-02` | Phase B 감사 비차단 7건: 4xx 거부 문구 표 entity 단일화(생성 code 합집합), 적용 후 백테스트 blocked 시 요청 폐기·알림, SSE 좁히기 표 타입 가드, CORS 기본 origin 단일 owner, 두 page 어시스턴트 배선 훅, backlog 2건 담당 지정, PLAN 병합 규칙 정정 | C-01 | `SELF_CHECK` | 구현자 `impl-ai-b05-c01`, 워크트리 `wt-ai-c02`, 브랜치 `feat/ai-c-02-phase-b-followup` |

Phase exit:

- [ ] NB-1~NB-10 닫힘, 각 항목이 코드·문서·테스트 중 어디서 닫혔는지 PR 본문에 기록.
- [ ] no-extras job 확대판이 SDK 없는 환경에서 green.
- [ ] Phase B 감사 NB-1~NB-8 처리 결과(C-02 닫힘 6건, C-01 닫힘 1건, backlog 이관 1건)를 C-02 PR
  본문에 기록.

### C 절 backlog (Phase B 감사 NB-4)

**1. 검색 상한 통지 전용 `ChatEvent`와 화면 분기** — 담당 AI 후속 C-03(미착수)

- **상황**: OpenAI 프로파일이 활성인 세션에서 한 턴의 누적 웹 검색이 `max_search_uses`에 닿는다.
- **인풋**:
  1. `POST /api/v1/assistant/sessions/{id}/turns` — 검색을 상한 이상 유도하는 질문.
  2. 사이드바가 그 턴의 SSE 또는 `GET /api/v1/assistant/sessions/{id}` 이력을 그린다.
- **에러 위치**: 생산 쪽 `backend/src/strategy_workbench/adapters/outbound/llm_openai/_turn.py`가
  통지를 `SearchActivity(query=SEARCH_BUDGET_EXHAUSTED_NOTICE, sources=())`로 흘린다. 소비 쪽
  `frontend/src/features/assist-strategy/ui/assist-transcript.tsx`는 그것을 "웹 검색" 배지와
  `query` 본문으로 그리며, 통지를 가르는 분기가 없다. 집계는
  `backend/src/strategy_workbench/application/assistant_chat/_usage.py`의 `_is_search`가 문구
  비교로 막고 있다.
- **위험성**: 사용자는 하지 않은 검색 칩을 보고, 칩 본문은 모델에게 보내려고 쓴 고정 문장이다
  (표시 오차). 통지와 검색을 가르는 근거가 문구 비교라 같은 문구를 쓰는 경로가 생기거나 번역이
  들어오면 집계가 다시 틀린다(silent 집계 오류). 데이터 손실·look-ahead는 아니다.
- **해결 방향**: `ChatEvent` union에 통지 전용 갈래를 더하고 adapter·리듀서·SSE 좁히기 표
  (C-02가 갈래 합집합으로 묶어 누락 시 컴파일 오류)·transcript를 함께 고친다. 그때 `_is_search`
  분기와 아래 재현 테스트를 지운다.
- **재현 test**: `backend/tests/application/test_assistant_usage.py::test_the_search_budget_notice_is_not_counted_as_a_search`
  (현재 동작 고정). frontend 쪽 없음.

**2. `ChatMessageView.turn_id`** — 담당 AI 후속 C-03(미착수)

- **상황**: 한 세션에 턴이 여러 개 쌓인 뒤 사이드바가 세션 이력으로 대화를 다시 그린다.
- **인풋**: `GET /api/v1/assistant/sessions/{id}` 응답의 `messages` 배열(사용자·어시스턴트 메시지).
- **에러 위치**: 전송 계약 `ChatMessageView`에 `turn_id`가 없다. 그래서
  `frontend/src/features/assist-strategy/model/transcript.ts`의 `assistTranscript`는 n번째 사용자
  메시지를 n번째 턴의 질문으로 본다(생성 순서 짝짓기). 이 backlog는 B-03 행 비고와 그 파일
  docstring에 적혀 있지만 담당이 없었다.
- **위험성**: 메시지 수와 턴 수가 어긋나는 경로(사용자 메시지 없이 턴이 생기거나 그 반대)에서는
  질문이 다른 턴의 답에 붙는다(표시 오차, silent). 지금은 리듀서의 턴 순서 불변식이 막고 있을
  뿐이다. 데이터 손실은 아니다. 계약에 `turn_id`를 싣고 그 값으로 짝지으면 추정이 사라진다.
- **재현 test**: 없음. C-03이 backend 계약 테스트와 transcript 단위 테스트를 함께 더한다.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|
| P0-01 | `review_ai_p0_01` | 1 | REQUEST_CHANGES | P1 3(적용 경로·턴 owner·PR 분할), P2 10, P3 8 → 전부 반영 |
| P0-01 | `review_ai_p0_01` | 2 | REQUEST_CHANGES | 1차 전부 해소 확인. 신규 P1 2(EventSource 재개 회귀·stale 가드 진행 경로), P2 3, P3 5 → 반영 |
| P0-01 | `review_ai_p0_01` | 3 | REQUEST_CHANGES | 2차 전부 해소 확인. 신규 P1 1(토큰 예산 축소가 포트로 구현 불가), P2 2, P3 4 → 반영 |
| P0-01 | `review_ai_p0_01` | 4 | APPROVE | 3차 전부 해소. 잔여 P2 1(`max_tool_rounds` 집행 주체) 반영, P3 3(D9 분리 반영, OpenAI 통지 문구·기본값은 A-06·A-07) |
| A-01 | `review_ai_a_01` | 1 | REQUEST_CHANGES | P1 1(`ProbeResult.message` 스크럽 계약 없음), P2 4(활성 승계·create 쓰기 순서·base_url 정수/16진/`localhost.` 우회·`_models` 테스트/`DocumentRef` 불변식), P3 5 → 반영 |
| A-02 | `review_ai_a_02` | 1 | REQUEST_CHANGES | P1 1(취소 직후 두 번째 턴 시작), P2 6(`stream.close` 예외로 `_finish` 누락, 크래시 경로 Failure 없음, 취소 시 이벤트 유실, `logger.exception` 전문, `_DISCRIMINATOR` 손글씨, 타임아웃 유예) → 반영 |
| A-01 | `review_ai_a_01` | 2 | REQUEST_CHANGES | 1차 10건 전부 해소. 새 P2 1(insecure 플래그가 기본 정책의 상위집합이 아님), 비차단 2(거절 문구, 승계 테스트) → 반영 |
| A-03 | `review_ai_a_03` | 1 | REQUEST_CHANGES | P1 2(`ChatEvent` 확장 가드 부재, 비밀 파일 경로가 예외 메시지에), 권고 1(`update_turn`이 `started_at`·`accepted_sequence` 덮음), P2 1(부모 디렉터리 0700), P3 6 → 반영 |
| A-01 | `review_ai_a_01` | 3 | APPROVE | 2차 3건 전부 해소(39 URL 매트릭스 실측). 새 P3 2(스킴 누락 거절 문구, `FailureCode`·`ChatEvent` 집합 대조 테스트) → A-02 브랜치에 적재. 잔여 위험: `Failure.message` 자유 문자열(adapter PR에서 `str(exc)` 유입 게이트) |
| A-02 | `review_ai_a_02` | 2 | APPROVE | 1차 P1 1·P2 6 전부 해소(원인 수정 + 회귀 테스트). `equity_workspace` 화살표는 포트 소비 확인. 새 P3 7(도구 종료 경로 이벤트 보존 등) → 5 반영, 2 사양(판별자 첫 매치, `_finish` 창 `state()`는 A-04 확인) |
| A-03 | `review_ai_a_03` | 2 | APPROVE | 1차 P1 2·권고·P2 전부 해소(`assert_never` 양방향 실증). 새 P3 2(`__cause__` 경로, 종료 상태 역전 미차단) → 후속 커밋 |
| A-02 | `review_ai_a_02` | 3 | APPROVE | 후속 커밋 2개 확인. `Done` 뒤 `Failure` 가능 계약은 spec 문장 추가(A-04)·B-03 리듀서 확인 항목 |
| A-04 | `review_ai_a_04` | 1 | APPROVE WITH CHANGES | P1 1(`assistant.document_ref_invalid`가 OpenAPI·SDK에 없음), P2 3(이력 읽기 순서, SSE payload `unknown`, 레지스트리 즉시 호출·미설치 표현), P3 7 → 반영. `_finish` 순서 변경은 A-02 불변 4개 유지 확인 |
| A-05 | `review_ai_a_05` | 1 | REQUEST_CHANGES | P0 1(`output_format=None`이 SDK 센티널 아님 → live에서 모든 텍스트 블록 `ValidationError`, 대본이 `_client.py`를 안 지나 미검출), P1 1(검색 상한 호출당 집행), P2 3(`llm` extra 없이 수집 깨짐, 로그 키 테스트 부재, 미사용 `DEPENDS_ON`), P3 6 → 반영. SDK 표면은 설치본 1.7.0과 전부 일치 |
| B-01 | `review_ai_b_01` | 1 | REQUEST_CHANGES | P1 1(제출한 API 키가 react-query mutation cache `variables.secret`에 잔류 → 키 실은 요청은 mutation 미사용), P2 2(고급 접힘 시 base_url 전송, 삭제 확인 포커스·announce). FSD·i18n·서버 문구 미노출·스크린샷 OK |
| A-04 | `review_ai_a_04` | 2 | APPROVE | 1차 11건 중 10 해소·1 근거 보류. `PROVIDER_SDK_MODULES` 표·라이브 SSE 테스트가 권고보다 나음. 새 P3 4(하위 모듈 오분류, pyright ignore, 실패 시 스레드 정리, 공개 setter) → 후속 커밋 |
| B-02 | `review_ai_b_02` | 1 | REQUEST_CHANGES | P1 2(재시도 상한 소진 뒤 진행 중 턴 스트림 영구 중단 — `streamKey`·`status`·`retry()`로 재연결 owner를 화면으로; 마지막 시도 본문 끊김이 `ended`로 분류 — `onSseError`로 계수), P2 2(이력 병합이 watermark 아래 앞선 턴 이벤트 폐기 → 적용 sequence 집합; `frontend-api-state.md` 갱신), P3 3 |
| B-01 | `review_ai_b_01` | 2 | APPROVE_WITH_COMMENTS | P1·P2·P3 전부 닫힘(캐시 단언 실효성 되돌리기 실측). 새 P2 1(`probingId` 단일 슬롯 — 동시 probe에서 버튼 조기 해제, 주석 오기), P3 1(font-size 토큰화로 h2>h1 위계) → 후속. R2-3 삭제 후 포커스·R2-4 배지 연결은 B-05 |
| A-07 | `review_ai_a_07` | 1 | APPROVE | P0·P1 없음. P2 1(`aggregate_usage`가 A-06 상한 통지 `SearchActivity(query=SEARCH_BUDGET_EXHAUSTED_NOTICE)`를 검색 1회로 집계 → 화면 검색 횟수 상한+1; 통지 제외 + 전용 이벤트 backlog), P3 3(`_chat.py` 모델 향 문장 2개가 `MODEL_NOTICES` 골든 밖, live smoke가 `DEFAULT_MAX_SEARCH_USES` 상수 읽음, 이력 `events`==SSE 프레임 미고정). 캐시 토큰 domain·집계·wire 일관, 돌연변이 3건 골든이 포착. pytest 1957 |
| A-07 | `review_ai_a_07` | 2 | APPROVE | `e57ec183`·`5009a03c`. 1차 4건 반영 확인, 취소 결함 2건(첫 이벤트 전 취소 사유 저장은 `state.stop is None`일 때만 → PROVIDER 덮지 않음, 중복 억제는 같은 코드만 → 이벤트 0개 턴 없음)이 spec D3·`_finish` 1회와 정합. 새 결함 없음. 돌연변이 3건 포착, 변경 모듈 pytest 206 |
| B-01 | `review_ai_b_01` | 3 | APPROVE | R2-1(`probingIds` 집합, 회귀 테스트가 2차 재현 시나리오와 동일)·R2-2(제목 display 크기) 닫힘 확인, PR #180 본문 코드 대조 정확. 비차단 2: R2-3·R2-4 B-05 이관 기록이 PR 본문에만 있음 → WORKFLOW B-05 acceptance에 추가; `ai-provider-settings.tsx` 주석 "활성 전환·" 과장. vitest 659 |
| A-05 | `review_ai_a_05` | 2 | APPROVE WITH CHANGES | 1차 12건 전부 해소(P0 red 확인, extras 없는 환경 재현, env 폴백은 `ANTHROPIC_AUTH_TOKEN`까지 차단). 블로킹 1(A-04 하위 모듈 fix 되돌림)은 base `439f9411` 불일치 산물 → replay 보존. P2 2(예산 소진 호출의 history 서버 도구 블록 미테스트 → live smoke 최우선, 캐시 접두 파기 비용 미기재), P3 4 |
| B-02 | `review_ai_b_02` | 2 | APPROVE_WITH_NITS | 1차 P1 2·P2 2 해소(재현 probe 재실행). 이탈 2건(5값 status, 내부 attempt+retry) 타당. 새 P2 1(`streamKey` 입력 파생 → 세션 이탈·복귀 시 옛 close 사유가 status로), P3 6 → 후속 |
| A-06 | `review_ai_a_06` | 1 | REQUEST_CHANGES | P1 2(`OPENAI_CUSTOM_HEADERS`→`default_headers`가 Authorization 우선 — A-05도 `ANTHROPIC_CUSTOM_HEADERS` 동일; 턴 예산 잔량<16 호출 400→PROVIDER), P2 3(`store` 기본 true, 통지 경로 spec 문장, 도구 제거 호출에 이전 `web_search_call` 동승 미테스트), P3 7. A-05 결함 부류 15 중 9 부재 확인 |
| B-02 | `review_ai_b_02` | 3 | APPROVE | 2차 잔여 7건 전부 닫힘(단조 `run`·렌더 중 조정, StrictMode 이중 호출 probe 통과, 값 형태 setState 멱등). 메모: `queryClient` 인스턴스 교체는 실제 경로 없음 |
| B-03 | `review_ai_b_03` | 1 | REQUEST_CHANGES | P1 1(`reduceHistory`가 messages는 교체·turns는 append → `asked[index]` 짝 밀림; 리듀서 턴 배열을 accepted_sequence 순 유지), P2 4(409 질문 유실, 출처 호스트 미표시, aria-live 델타 재낭독, 대화상자 초점 복귀), P3 6. 렌더 안전·비밀·FSD·i18n OK |
| A-05 | `review_ai_a_05` | 3 | APPROVE | 5항목 전부 통과(A-04 보존, 양쪽 환경 green, P2·P3 반영, Usage 분리형 계약+파생, `ANTHROPIC_CUSTOM_HEADERS` 독립 재현·차단 확인 — 위험은 키 유출이 아니라 요청이 남의 계정으로 나가는 것). P3 1(비인증 헤더 통과를 docstring에 명시) |
| B-03 | `review_ai_b_03` | 2 | REQUEST_CHANGES | 1차 11건 중 10 해소(P1 되돌리기 실증). 새 P1 1(`finishedTurn`이 status·failure 무시 → 취소·실패에도 "완료" announce·잔존), P2 1(제안 카드 `aria-live="off"` 주석만), P3 4(409 왕복 중 새 질문 덮임, 이벤트마다 정렬, `String.replace` 패턴, 주석) |
| B-03 | `review_ai_b_03` | 3 | APPROVE | 2차 6건 전부 해소(되돌리기 실증 3건). 새 non-blocking 4: P2 1(`role="status"` 조건 렌더라 내용과 함께 삽입돼 첫 질문·실패 뒤 진행 알림 누락 가능 → 상시 렌더 + 텍스트만 교체), P3 3(슬롯 landmark 계약을 B-04 Acceptance로, 사이드바 자체 헤더 중복, 완료 문구 지속은 결함 아님). vitest 728 |
| A-06 | `review_ai_a_06` | 2 | APPROVE | 1차 12건 전부 해소(`http_client` 주입이 `OPENAI_CUSTOM_HEADERS` 파싱 경로를 실제로 지남 확인, 예산 가드 되돌리기 red). P2(Anthropic 동일 구멍)는 A-05 `c0219894`에서 이미 차단. P3 4 → replay 뒤 커밋 |
| B-04 | `review_ai_b_04` | 1 | REQUEST_CHANGES | P1 3(적용 알림 `<p>`가 슬롯 fragment `flex:1`로 패널 절반 + 소유자 `documentEpoch`뿐이라 미소거; 좁은 화면 계약·AI 서랍 동시 표시; 드래그 임계 초과 시 핸들 언마운트·폭 저장으로 복구 불가), P2 2(en 한글 `yaml-only`, spec D7 onClose 취소 확인 미배선), P3 7. 적용 설계·데이터 손실·look-ahead 결함 없음, 기준선 4장 상단 토글 밴드만 |
| B-04 | `review_ai_b_04` | 2 | REQUEST_CHANGES | `c6aaf55e` 재판정. live 읽기 정합 확인(양변 편집기 텍스트, `beginTurn`이 `readContext()` 1회), notice 슬롯으로 레이아웃 절반 해소. 잔존 P1 2(좁은 화면 서랍 겹침, 폭 401px+ 드래그 끊김·`lastOpenedRight` 미저장), P2 4(적용 알림 영구 잔존, en `yaml-only` 한글, `onClose` 미배선, live 읽기 통합 테스트 vacuous), P3 2(포맷 전환 순간 `source_format` 출처 불일치, 알림 줄 충돌 배너 중복). vitest 762 |
| B-04 | `review_ai_b_04` | 3 | REQUEST_CHANGES | `c9fd1f3d`. 2차 6건 되돌리기 실험으로 전부 확인, 기본 폭 상수 판정 수용, B-03 계약 3건 정합. 새 P1 1(함수 슬롯에서 인라인 펼침 시 토글 버튼 언마운트로 포커스 `body` 소실 — 위젯 테스트 `mount()`가 ReactNode 슬롯이라 가려짐), P2 2(제안이 현재 문서와 동일하면 `sourceVersion` 불변 → `applied` 소유자 영원히 불일치 + `chainPhase`가 idle을 취소로 읽어 적용 후 백테스트 취소, 2차 대비 회귀; WORKFLOW Acceptance가 접기·`onClose` 문장에서 코드와 모순), P3 3. vitest 776 |
| B-04 | `review_ai_b_04` | 4 | APPROVE | `47041511`. 3차 6건 반영 확인(되돌리기 실험: 패널 포커스 폴백·동일 텍스트 알림/체인 테스트가 정확히 깨짐), Acceptance 문장이 코드와 일치, P3-1 포맷 기억 제거 이탈 타당. 새 결함 없음. 비차단: `readContext` 호출 시점 주석 1줄 부정확(네트워크 콜백 경로). vitest 780 |
| B-05 | `review_ai_b_05` | 1 | APPROVE(조건부) | `0b982778`. 코드·테스트 blocking 없음. P1 1(DEFECT-B05-001이 코드 주석에만 인용되고 id가 `database/` 동명 결함과 충돌 → 리드 PLAN 기록 확인 + `DEFECT-AI-B05-001`로 개명), P2 4(재개 시나리오 여유 ~5초, 가짜 공급자 켜진 프로세스 표식 없음, 재연결 중복 반영을 `toContainText`가 못 잡음, 적용 격리 브라우저 단언 없음), P3 3. P1-05 `aa57028e`와 blob 동일 확인, `playwright test` 직접 호출 차단 확인. pytest 1718, vitest 798 |
| B-02 | `review_ai_b_02` | 4 | APPROVE | `22a86e39`. A-07 캐시 성분 반영이 backend 불변식과 일치, 정착 플래그(이력 응답만 설정) 설계 건전 — 취소 dispatch 뒤 연결 유지 → 취소 사유 프레임 → 이력 정착 뒤 재연결·추가 조회 0, 반대 오류는 409 1회로 수렴, StrictMode 합성 확인. P3 2(훅 docstring이 옛 개폐 규칙 단언, PR 본문 변경 내용 bullet·결함 id 불일치). vitest 694 |
| B-05 | `review_ai_b_05` | 2 | APPROVE | `b1412682`. 1차 후속 5건 코드 해소·방법 타당(R1-003 지연 배율, R1-004 `ProbeResult`는 `message` 필드 없음 — 도메인이 강제한 로컬 로그 경로가 유일, R1-005·006), P3 3 해소. 결함 기록은 리드 PLAN이 정본. 새 P2 1(취소 대체 단언: 마지막 문장 부정 단언이 즉시 통과라 서버가 취소 무시해도 green → reload 뒤 status 문구 단언). vitest flake 1(probe 경합 기존 테스트) |
| B-03 | `review_ai_b_03` | 4 | APPROVE | `92ce346a`. 3차 P2 1·P3 3 닫힘, `reduceTurn` 병합이 B-02 계약 불변(`settled` 3줄 문자 동일, `!== "running"` 축약 안전), 하네스·usage 픽스처 적절, PR #185 본문 일치·12절 사유 있음. B-01 `configure-ai-providers` probe 경합 테스트 flake 1회(380ms 벽시계 의존 → C-01에서 deferred로), P3 3(죽은 키 `assistant.chat.title`·`.assist__title`, `.assist__head` 정렬, 하네스 커밋 메시지 과장) → C-01 |
| B-04 | `review_ai_b_04` | 5 | APPROVE | `25fc7edc`. replay 드리프트 0(패치 3576줄 동일, hunk 헤더 3줄만), `SessionHistoryView.usage`·`unsettledAssistantTurn` 미참조로 B-02 정착 플래그와 직교, 인계 5건 정합, 주석 정정 확인, PR #186 본문 stat·설계 결정 8건·12절 사유 일치. 부하 민감 테스트 flake 2(`code-editor.performance`, `document-routes` P6-03) 회귀 아님. vitest 783 |
| B-05 | `review_ai_b_05` | 3 | APPROVE | `d84b0eda`. R2-001 대체 단언 타당 — reload 뒤 progress 문구는 `finishedTurn`이 `watchedTurnId`일 때만 채워져 불가, 대신 서버 사실 둘 중 "다음 질문이 409 없이 접수"가 러너 슬롯 계약(`cancel()`은 Event만, pop은 `_finish`뿐)으로 취소 무시 회귀를 결정적으로 잡음. P1-05 `cf56b5b4` 재복사 9파일 blob 동일·CORS 동일, `PW_BACKEND_PORT` 누출 닫힘 확인. P3 2. vitest 809 |

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|
| P0-01 | `update-plan-progress.ps1 -Check` | 통과 | 2026-09-20 |

## 변경 기록

- 2026-09-26 — C-02가 Phase B 감사 비차단 건을 처리했다. NB-1: 4xx 거부 문구 표를
  `entities/assistant`의 `Record<AssistantRejectionCode, MessageKey>` 하나로 모으고
  `TURN_IN_PROGRESS`를 entity로 옮겼다. NB-2: "적용 후 백테스트"가 blocked로 풀리면 요청을
  버리고 알림 줄에 "적용했지만 백테스트는 시작하지 않았다"를 보인다(spec "자동 백테스트
  금지" 쪽 결정, 회귀 테스트 수정 전 red 확인). 첫 구현은 이미 이은 실행이 게이트를 닫는 순간을
  blocked로 읽어 전체 e2e에서 걸렸고, ready 판정 때도 요청을 대기에서 꺼내도록 고쳤다. NB-3: C-01이
  매뉴얼을 고쳤고, C-02는 스크립트 경로와 "변수를 `1`로 주지 않으면 조용히 끝난다" 경고만 보탰다.
  C-01(#190)이 main에 머지돼(`a3cc5f8b`) C-01 행을 `MERGED`로 바꿨다. NB-4: 코드는 두고 C 절 backlog 2건(검색 상한 통지 전용 이벤트,
  `ChatMessageView.turn_id`)의 담당을 "AI 후속 C-03(미착수)"으로 정했다. NB-5: C 절 서문의 PLAN
  충돌 규칙을 절 단위 병합으로 고쳤다. NB-6: SSE 좁히기 표 키를 이벤트 갈래 합집합으로 묶었고,
  정리 중 `constructor` 같은 프로토타입 이름 프레임이 통과하던 결함을 `Object.hasOwn`으로 막았다.
  NB-7: `create_app`의 CORS 기본값을 없애 기본 origin의 owner를 `bootstrap/_http.py` 하나로 했다.
  NB-8: 두 전략 page의 어시스턴트 배선을 `useStrategyAssistant` 하나로 모았다.
- 2026-09-26 — **P0-01~B-05 13 PR main 머지 완료**(#166·#169·#170·#171·#174·#175·#178·#179·#180·#182·#185·#186·#189, 스택 아래부터 `--merge`, 다음 PR base를 main으로 먼저 옮긴 뒤). 중간 PR은 PLAN.md만 리드 판으로 맞춘 main 병합 커밋을 얹었고(PLAN 외 파일은 각 tip과 동일, CI는 tip 결과 인용), 스택 전용 절(현재 결정의 A-07 항목·A-07 backlog·기본값 근거·C 절·A-05/A-06 변경 기록)은 C-01의 main 병합에서 절 단위로 합쳤다(Phase B 감사 NB-5). C-01은 1차 REQUEST_CHANGES(P2 3: PLAN 도구 cp949 깨짐, NB-9 기준선이 SDK gate 미통과, 매뉴얼 `RUN_LLM_LIVE`) → 반영 `afba06f3` → 2차 REQUEST_CHANGES(P2 1: probe 상한 owner 오기, 1차 지적 오판) → `de807566`.
- 2026-09-26 — 세션 한도 중단 뒤 재개. B-05 **최종 `2d5f2b26`**, C-01 B-05 위 rebase **`b7e01b76`**(range-diff 6커밋 전부 동일, pytest 1992·vitest 812·계약 재생성 diff 0) push, 스택 최상단 `b7e01b76`에서 **전체 e2e 23/23**(백테스트 2건·적용 후 백테스트 포함). `review_ai_c_01` 1차 재착수(이전 리뷰는 결과 없이 중단). **Phase B 감사(`audit_ai_phase_b`, `bb51cec9`) PASS, blocking 0**, NON_BLOCKING 8건(NB-1 거부 문구 표 이중 owner, NB-2 적용 후 백테스트가 `blocked` 뒤 트리거 잔존 → armed 폐기로 결정, NB-3 매뉴얼 live smoke 변수, NB-4 backlog 담당 부재, NB-5 PLAN 병합 규칙, NB-6~8 P3)은 스택 최상단 **C-02** 하나로 처리. 완료 정의 1(실제 키 연결 테스트)·live smoke는 키 부재로 사용자 실행 필요.
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
- 2026-09-21 — 보안 결함(A-06 발견, A-05·A-06 수정): 프로파일에 base_url이 없으면 SDK가 `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` 환경 변수를 읽어 spec D6 검사를 지나지 않은 호스트로 키가 나감 → 기본 base_url·api_key를 항상 명시해 SDK 환경 변수 폴백 차단(spec D6 한 줄). `Usage` 캐시 필드는 SDK 값 그대로 매핑.
- 2026-09-21 — A-06(#178) PR 생성·리뷰 배정. `Usage` 캐시 의미 결정(번복 후 확정): domain은 **분리형**(`input_tokens` = 캐시 읽기·쓰기 제외, 세 칸 겹치지 않음) 유지, 총입력은 파생 `total_input_tokens`(A-05), OpenAI adapter가 원시 내역을 빼서 정규화(A-06), A-07 집계는 성분 합산 + wire `total_input_tokens`. 근거: 성분별 단가·저장 이력 의미 보존(A-05 리뷰어).
- 2026-09-21 — C-01 추가 3건(B-01 probe 경합 deferred, B-02 세션 전환 동기 push+`delivered`+닫힘 플래그+afterEach, B-03 죽은 키·CSS·`.assist__head`) 완료 **`cfb704dd`**(B-05 `ea7b41d5` 위 6커밋, 21파일 +365/−78; pytest 1992, no-extras 118, vitest 801 ×3, e2e 23) · [#190](https://github.com/Nochiski/Quant_study/pull/190) · `review_ai_c_01` 1차 착수. B-05 최종 재복사 tip 위 `--onto` 1회 남음.
- 2026-09-21 — C-01 NB-1~NB-10 구현 완료 `33d7f663`(B-02 `22a86e39` 위 5커밋; `MIN_CALL_OUTPUT_TOKENS` owner domain + AST 가드, CI ruff·pyright `tools` 포함, no-extras 110 passed, anthropic env 14종 SDK 상수 기반 기준선, `provider_secret_missing` 422 테스트; pytest 1968). 추가 범위(B-01 probe flake deferred화, B-03 죽은 키·CSS·헤더 정렬, B-02 `assistant-event-stream` 세션 전환 테스트 `Controller is already closed` 격리)는 B-05 최종 tip 위 rebase 뒤 커밋.
- 2026-09-21 — C-01 범위 추가: B-01 probe 경합 vitest flake(벽시계 380ms 의존, B-03·B-05 리뷰어 각 1회 관측)를 deferred promise로 결정화, B-03 P3 2건(죽은 키·CSS, `.assist__head` 정렬).
- 2026-09-21 — 사용자 지시로 우선순위 변경: 진행 중 작업 전부 push, **AI 스택을 먼저 완결**(B-02~B-05 PR → C-01 감사 후속 → 전체 E2E·CI → #166부터 순서 머지). C-01 구현 착수(`impl-ai-a07`, 워크트리 `wt-ai-c01`, base B-05 tip).
- 2026-09-21 — **Phase A 감사(`audit_ai_phase_a`, A-07 최종 `5009a03c`) PASS, blocking 0.** SoT 15행 owner 단일, `DEPENDS_ON`↔import 6노드 일치, 계약 재생성 diff 0, anthropic env 14종 wire 프로브 누출 0, pytest 1964·no-extras 67. 남은 exit 항목 "live smoke 2건 로컬 통과"는 키 부재로 미실행(사용자 실행 필요). NON_BLOCKING 11건은 B 스택 cascade를 피하기 위해 **B-05 뒤 감사 후속 PR(C-01)** 하나로 처리: NB-1 `RUN_LLM_LIVE` 표기 잔존(`llm_openai/_client.py:10`, 테스트), NB-2 WORKFLOW 이월 체크박스 3건·275행 반대 서술, NB-3 PLAN "검색 8 vs `MAX_PAUSE_RESUMES=5`" stale, NB-4 A-05 acceptance 검색 집행 문장 spec D4 불일치, NB-5 `MIN_CALL_OUTPUT_TOKENS` 두 adapter 중복(등가 가드), NB-6 `backend/tools/` CI ruff·pyright 범위 밖, NB-7 `backend-no-extras` job이 A-07 신규 4파일 미실행, NB-8 `assistant.provider_secret_missing` 422 emit 테스트 없음, NB-9 anthropic env 차단 기준선 4종(OpenAI 대비 좁음, SDK 14종), NB-10 영문 docstring 3줄, NB-11 브랜치 base divergence는 머지 전 처리. 전문 `scratchpad/audit_ai_phase_a.md`.
- 2026-09-21 — B-05 e2e가 찾은 DEFECT-AI-B05-001(진행 중 턴 취소 후 취소 사유가 새로고침 전까지 안 보임): 프론트 `assistantStreamTarget`이 턴 status로 파생돼 취소 dispatch 직후 SSE를 닫아 서버의 `Failure(CANCELLED)`·남은 조각을 못 받음 → **B-02**가 target을 정착(`is_settled`) 기준으로 파생하도록 수정; 첫 이벤트 전 취소 시 backend가 `Failure(CANCELLED)`를 저장하지 않는 경로 → **A-07** 마무리 커밋에서 수정. B-05 e2e 2번 시나리오는 두 수정 뒤 "reload 없이 취소 문구"로 조임.
- 2026-09-21 — B-03 구현 완료. A 후속 backlog: `ChatMessageView.turn_id` 추가(질문↔턴 짝짓기가 순서 가정에 의존).
- 2026-09-21 — A-07: 실행 설정 문장은 schema 1.1 기준(lang2 P2-03 머지 뒤 프롬프트·골든 갱신 후속), env `STRATEGY_WORKBENCH_LIVE_SMOKE`로 통일, 기본값 5종 유지(근거 표), 타임아웃 300s 여유 얇음(live smoke 후 bootstrap 주입으로 조정).
- 2026-09-21 — A-02 후속(CI flake 수정)이 스택 위에 없어 A-04·A-05 CI 실패 → A-03부터 cascade rebase(A-03 `d52436b7`, 이어 A-04·A-05·A-06·A-07·B-01).
- 2026-09-21 — A-04(#174) PR 생성·리뷰 배정, A-05 rebase·팩토리 등록 지시, B-01 착수(A-04 위, frontend만).
- 2026-09-21 — A-01(3차)·A-02(2차)·A-03(2차) APPROVE. A-04 구현 완료·rebase 중, A-05 착수.
- 2026-09-20 — A-03(#171) PR 생성·리뷰 배정, A-04 착수(A-02 rewrite 뒤 A-03·A-04 rebase 예정).
- 2026-09-20 — A-01·A-02 1차 리뷰 REQUEST_CHANGES(각 P1 1건) → 같은 구현자가 반영 후 재검토.
- 2026-09-20 — A-01(#169)·A-02(#170) 스택 PR 생성, 리뷰 배정. A-03 착수.
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
