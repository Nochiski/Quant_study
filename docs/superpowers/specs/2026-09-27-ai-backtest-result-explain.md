# 설계: AI가 백테스트 결과를 쉬운 말로 풀어 준다 (US-DM-08)

> 작성: 2026-09-27
>
> 상태: Proposed (제품 소유자 지시 2026-09-27: "전략 언어는 진짜 전략만 담고, AI를 통해 사용성까지
> 증대", "전부 다 진행해")
>
> 유저 스토리: [US-DM-08](../../product/user-stories/stories/dm.md#us-dm-08-백테스트-결과를-ai에게-쉬운-말로-풀어-달라고-한다).
> 상위 설계: [AI 어시스턴트 설계](./2026-09-20-ai-assistant-design.md)(이하 "상위 spec"). 이 문서는 상위
> spec의 네 원칙을 그대로 지키고 결과 화면에 붙이는 방식만 정한다.
>
> - 공급자 SDK는 adapter에만 둔다.
> - 도구는 application이 선언하고 실행한다.
> - 제안은 사용자 "적용"으로만 문서에 들어간다.
> - 비밀과 `Failure.message`를 스크럽한다.
>
> PR 진행은 [docs/planning/ai-assistant/PLAN.md](../../planning/ai-assistant/PLAN.md) D 절이 추적한다.

## 1. 맥락

정동민은 퀀트를 잘 모르는 국내 주식 개인 투자자다. 백테스트가 끝나면 결과 화면에 핵심 지표 여섯
개(Total return·Sharpe ratio·Maximum drawdown·Calmar ratio·Turnover·Closed trades)와 자산 곡선이
뜬다(US-DM-04). 이 사람은 샤프 비율이나 최대 낙폭이 무슨 뜻인지 모르고, 이 숫자가 좋은지도 판단하지
못한다. 지금 AI 어시스턴트는 전략 화면에만 붙어 있고, 모델에게 가는 맥락은 문서 원문·compile 진단·
실행 설정뿐이다.

풀어야 할 것은 다섯 가지다.

1. 어시스턴트를 결과 화면 어디에 어떻게 붙이는가.
2. 모델은 결과에서 무엇을 얼마나 보는가.
3. 결과에 대한 대화는 어느 세션에 쌓이는가.
4. 지표의 한글 이름과 쉬운 뜻은 누가 소유하는가.
5. 결과 화면에서 모델이 문서를 바꾸는 제안을 내도 되는가.

## 2. 결정

### R1. 결과 페이지에 기존 사이드바 feature를 그대로 붙인다

결과 페이지(`pages/research-backtest`)가 우측 패널을 조합한다. 실행이 완료되면 머리에 "AI에게 결과
묻기" 토글 버튼이 생기고, 누르면 `<aside>` 패널(접근성 이름 "AI 어시스턴트", 보이는 제목 `h2`)이
열린다. 패널 안은 전략 화면과 같은 `features/assist-strategy`의 `AssistStrategySidebar`다.

사이드바에 바뀌는 것은 둘뿐이다.

- `readContext`를 선택 prop으로 바꾼다. 주지 않으면 턴 요청에 문서 컨텍스트를 싣지 않는다.
- 빈 대화 안내와 입력칸 안내 문구를 바깥이 고를 수 있게 한다(`copy="result"`). 결과 화면에서
  "무엇을 만들고 싶은지 적으세요"는 틀린 안내다.

제안 카드 콜백(`onApplyProposal` 등)은 결과 페이지가 넘기지 않는다. R5 때문에 제안 이벤트가 오지
않지만, 만에 하나 와도 버튼이 그려지지 않는다.

| 대안 | 기각 이유 |
|---|---|
| IDE의 `assistant` 슬롯 재사용 | 결과 페이지는 IDE가 아니다. 슬롯 배치(`use-panel-layout`)를 끌어오면 편집기 최소 폭 규칙까지 따라온다 |
| 새 feature `explain-backtest-result` | 메시지 목록·스트리밍·취소·세션 전환·재연결을 복제해야 한다. feature끼리 import할 수 없어 공유도 못 한다 |
| `assist-strategy`를 `assist-chat`으로 개명 | 이름은 더 맞지만 AI 후속 C-03이 같은 feature의 transcript를 고치는 중이라 충돌이 커진다. 개명은 순수 리팩터링이라 나중에 따로 한다 |

열림 상태는 페이지의 local UI state다(상위 spec D9). 새로고침에서 복원하지 않는다. 전략 화면과 달리
결과 화면은 편집 작업대가 아니라 읽는 화면이라 기본 접힘으로 충분하다.

### R2. 모델은 서버가 run_id로 읽은 결과 요약을 도구로 받는다

**frontend는 결과를 모델에게 보내지 않는다.** 턴 요청에는 `run_id`를 담은 세션만 있고, backend가
실행 레지스트리에서 결과를 읽어 요약한다. 결과는 서버가 가진 사실이고, frontend가 요약을 만들면 지표
선택·반올림·단위 해석이 화면 쪽에 한 벌 더 생긴다(SoT 규칙: 지표 공식·방향·단위는 backend Metric
Registry).

- **포트**: `application/assistant_chat/ports/outgoing/backtest_results.py`의 `BacktestResultPort`
  (`completed_result(run_id) -> BacktestRunResult | None`). 완료되지 않았거나 없는 실행은 `None`이다.
  bootstrap이 `BacktestRunService.result`를 감싸 구현한다. `StrategyCompilerPort`와 같은 방식이라
  application → application 화살표가 늘지 않는다. `assistant_chat`의 `DEPENDS_ON`에는 domain 노드
  `domain.backtest`·`domain.analytics`만 더한다.
- **도구**: `read_backtest_result`(입력 없음). 결과 세션에서 모델이 받는 도구는 이것 하나다. 요약을
  시스템 프롬프트에 박지 않고 도구로 주는 이유는 셋이다.
  1. 상위 spec D1의 "도구는 application이 선언·실행"과 같은 경로라 모든 공급자에서 동작한다.
  2. 시스템 프롬프트 골든이 실행마다 바뀌지 않는다. 프롬프트 캐시도 날짜 말고는 고정이다.
  3. 대본 공급자가 전략 대본처럼 `execute_tool`로 요약을 받아, e2e가 "서버가 읽은 숫자가 모델까지
     갔다"를 확인할 수 있다.
- **요약 내용**(`application/assistant_chat/_result_context.py`가 소유):

  | 항목 | 출처 |
  |---|---|
  | 기간(시작·끝)·시장·빈도·유니버스·체결 시점·수수료·슬리피지·참여율·결측 처리 | `manifest.environment` |
  | 초기 자본·연환산 거래일·벤치마크 종목 | manifest |
  | 전략 제목·설명·팩터(id·이름·방향·가중치)·보유 종목 수·리밸런싱 주기, 출처(저장 리비전/인라인) | `manifest.run_spec.strategy`, `strategy_provenance` |
  | 지표: id·registry 이름·범주·단위·높을수록 좋은가·값·구간·표본 수·산출 불가 사유 | `metrics` + `metric_definitions` |
  | 자산 곡선 요약: 거래일 수, 시작·끝 자산, 벤치마크 시작·끝, 최대 낙폭이 바닥을 찍은 날 | `series.equity`·`series.drawdown`의 **양 끝과 최솟값 위치**만 |
  | 월별 수익률: 최근 36개월 | `series.monthly_returns` |
  | 데이터 경고 코드·문장 | `manifest.warnings` |
  | 거래 수: 주문·체결·청산 거래 | `artifacts` 목록의 길이 |

- **원시 시계열은 넣지 않는다.** 일별 자산·낙폭·롤링 샤프 전체, 주문·체결·포지션 행은 수천 행이라
  토큰을 먹고 설명에 쓸 정보는 늘지 않는다. 요약에 새 계산을 넣지 않는다. 연 수익률처럼 공식이 필요한
  값은 만들지 않고 서버가 이미 낸 지표와 월별 값만 옮긴다. 양 끝 값과 최솟값 위치는 공식이 아니라
  선택이다.
- **토큰 상한**: 직렬화한 요약 JSON은 `MAX_RESULT_CONTEXT_CHARS = 12_000`자를 넘지 않는다(한글을
  섞은 JSON에서 약 4천 토큰). 넘으면 정해진 순서로 줄이고, 줄인 항목을 요약의 `omitted`에 적어 모델이
  "없다"와 "잘렸다"를 구분하게 한다. 순서는 월별 수익률(오래된 달부터) → 데이터 경고(뒤에서부터) →
  full 구간이 아닌 지표(뒤에서부터)다. full 구간 지표는 자르지 않는다. 상한은 지표 수(현재 21개)와
  구간 수에 비례해 늘 수 있는 부분만 잘라 내는 장치다. 테스트가 큰 합성 결과에서 상한과 `omitted`를
  고정한다.
- 퍼센트 단위 값은 비율(0.12 = 12%)로 온다. 이 사실은 요약 머리의 단위 설명이 모델에게 알린다.
  문장의 owner는 프롬프트 owner(`_prompt.py`)다.

| 대안 | 기각 이유 |
|---|---|
| frontend가 `TurnContextPayload`에 결과 요약을 실어 보낸다 | 화면이 지표 선택·단위 해석을 소유하게 된다. 받은 요약이 서버 결과와 같은지 backend가 확인할 수단도 없다 |
| 요약을 시스템 프롬프트 끝에 붙인다 | 도구 한 번의 왕복은 아끼지만 프롬프트 골든이 실행마다 달라지고, 대본 공급자가 요약을 읽을 통로가 없다 |
| 결과 전체(`BacktestRunResult` JSON)를 그대로 준다 | 실제 결과가 수십만 자다. 상한을 두면 결국 자르는 규칙이 필요하고, 그 규칙이 이 문서의 요약이다 |

### R3. 결과 대화는 실행 하나에 붙는 별도 세션이다

`DocumentRef`에 `run_id`를 더한다. 세션은 저장 전략(`strategy_id`+`revision`), 초안(`draft_id`),
실행(`run_id`) 중 **정확히 하나**에 붙는다. 결과 화면의 사이드바는 그 실행의 세션만 본다.

- `assistant_sqlite` 스키마를 v3으로 올린다. v2는 AI 후속 C-03(#204, `chat_messages.turn_id`)이
  먼저 가져갔다(리드 결정 2026-09-27). `chat_sessions`에 `run_id` 열을 더하고 CHECK를 셋 중 하나로
  바꾼다. SQLite는 CHECK를 바꾸는 `ALTER`가 없어 테이블을 다시 만든다. `chat_sessions`는 다른 표가
  외래 키로 참조하므로 외래 키를 끄고, `legacy_alter_table`을 켜 옛 표를 비켜 둔 뒤 원래 이름으로 새로
  만들고 옮긴다(C-03과 같은 "비켜 두고 새로 만들기" — 새 이름으로 만든 뒤 바꾸면 SQLite가 이름을
  따옴표로 감싸 manifest 대조가 깨진다). 끝나면 `foreign_key_check`로 고아 행을 보고 외래 키를
  되돌린다. v1 파일은 v1 → v2 → v3 순서로, v2 파일은 v2 → v3으로 이력을 잃지 않고 올라간다.
  마이그레이션 테스트가 세션·턴·메시지·이벤트가 그대로 남는지 본다.
- 세션 목록 조회(`GET /sessions`)에 `run_id` 쿼리를 더한다.

| 대안 | 기각 이유 |
|---|---|
| 그 실행을 만든 전략의 세션에 잇는다 | 새 전략 화면에서 저장하지 않고 돌린 실행(US-DM-03 흐름)은 전략 id가 없어 세션을 붙일 곳이 없다. 전략 화면 사이드바에 결과 해설이 섞여 들어가고, 한 세션 안에서 턴마다 도구 집합이 달라진다 |
| `draft_id` 열에 `run:<id>`처럼 접두사를 붙여 저장한다 | 스키마는 그대로지만 두 개념이 한 열을 나눠 쓴다. 초안 id 형식이 바뀌는 날 조용히 겹친다 |

실행 레지스트리는 지금 프로세스 안에만 있다(`BacktestRunService`). backend를 다시 시작하면 결과는
사라지고 결과 세션의 이력만 남는다. 그 세션에 새 턴을 보내면 422 `assistant.result_unavailable`이다.
결과 영속화는 이 문서의 범위가 아니다.

### R4. 지표의 한글 이름과 쉬운 뜻은 frontend i18n이 소유한다

화면 어휘는 키와 문장을 나눠 소유한다는 P1-03 결정(연산자·필드 설명 키)을 따른다.

- **키**: backend Metric Registry의 `metric_id`. 키 stem은 `backtest.metric.<metric_id>`이고
  (i18n 키 규칙 `<domain>.<area>.<phrase>`), stem이 쉬운 이름, `<stem>.description`이 한 줄 뜻이다.
  기존 `tName`·`tDescription`으로 읽는다.
- **문장**: `shared/config/messages.ts`의 ko·en. 결과 화면 "핵심 성과 지표"는 registry의 영어
  `label` 옆에 쉬운 이름과 한 줄 뜻을 함께 보인다.
- **짝 강제**: `backend/tests/fixtures/analytics/metric_ids.json`에 registry의 id 목록을 둔다. backend
  테스트가 registry와 이 파일이 같은지, frontend 테스트가 이 파일의 id마다 ko·en 이름·뜻이 있는지
  본다. 지표를 더하면 두 테스트가 차례로 깨져 문구를 쓰라고 알린다.
- **모델이 쓰는 문장은 owner 대상이 아니다.** 모델에게는 id와 registry `label`만 가고, 모델은 자기
  말로 풀어 쓴다. 모델 답은 저장된 사실이 아니라 산출물이다. 프롬프트는 "영어 지표 이름과 쉬운
  한국어 풀이를 같이 쓴다"만 지시한다.
- SoT 대장(`.claude/rules/strategy-workbench-sot.md`)에 "지표 화면 이름·쉬운 뜻" 행을 더한다.

| 대안 | 기각 이유 |
|---|---|
| Metric Registry에 `label_ko`·`description_ko`를 둔다 | 로케일 문장이 backend로 가면 en 문구도 backend가 가져야 한다. P1-03이 연산자·필드에서 이미 거절한 모양이다 |
| registry가 별도 `description_key`를 발행한다 | `metric_id`가 이미 안정된 키다. 같은 값을 다른 이름으로 한 번 더 싣는 셈이다 |

### R5. 결과 세션은 설명 전용 모드다

**모드는 세션이 정한다.** 요청 플래그가 아니라 세션의 `document_ref` 종류가 모드를 고른다. 같은 세션의
턴이 서로 다른 모드로 돌 수 없다.

| | 전략 세션 | 결과 세션 |
|---|---|---|
| 시스템 프롬프트 | `SYSTEM_PROMPT_TEMPLATE` | `RESULT_EXPLAIN_PROMPT_TEMPLATE` |
| 도구 | 전략 도구 다섯 | `read_backtest_result` 하나 |
| 웹 검색 | 켬 | 끔 |
| 턴 요청의 `context` | 필수 | 없어야 한다 |
| 제안 | `propose_strategy` | 없음 |

- 서비스는 **이번 턴에 준 도구 목록 밖의 호출을 도구 오류로 돌려준다.** 결과 세션에서 모델이
  `propose_strategy`를 불러도 `Proposal` 이벤트가 생기지 않는다. 지금은 `propose_strategy`를 도구
  목록과 무관하게 가로채므로 이 검사가 없으면 모드 경계가 프롬프트 한 줄에만 걸린다.
- 결과를 보고 전략을 고치고 싶으면 전략 화면 사이드바로 간다. 결과 세션 답은 그 길을 문장으로
  안내할 수 있지만 문서를 바꾸는 제안 카드는 만들지 않는다. 결과 화면에는 편집기가 없어 적용할
  곳이 없다(상위 spec D7의 적용 경로는 편집기 전체 범위 교체다).
- 웹 검색을 끄는 이유는 둘이다. 설명의 근거는 이 실행의 사실이면 충분하다. 그리고 백테스트 기간
  이후의 시장 소식으로 결과를 설명하면 사용자가 "전략이 그걸 알고 있었다"로 오해한다(R6).
- `context`가 모드와 맞지 않으면 422 `assistant.turn_context_mismatch`다. 전략 세션에 `context`가
  없거나 결과 세션에 `context`가 있을 때다. 조용히 무시하면 frontend 배선 실수가 "모델이 문서를
  못 본다"로만 드러난다.

### R6. look-ahead 없음

결과 설명은 이미 끝난 백테스트를 읽어 말로 옮길 뿐이다.

- 읽기 경로만 있다. 결과 세션에는 문서·실행 설정·실행 요청을 바꾸는 도구가 없고, 설명이 다음
  실행의 입력으로 되돌아가지 않는다. 실행 결과는 run manifest와 지표로 이미 고정돼 있다.
- 요약은 완료된 실행만 담는다. 진행 중이거나 실패한 실행은 `BacktestResultPort`가 `None`을 주고
  세션 생성·턴 시작이 422로 거절된다.
- 프롬프트는 모델에게 두 가지를 요구한다. 기간 밖에서 일어난 일로 결과를 정당화하지 않는다. 과거
  결과가 미래 수익을 보장하지 않는다고 말한다.

### R7. HTTP 계약

| 변경 | 내용 |
|---|---|
| `DocumentRefView.run_id` | 선택 필드. 셋 중 하나 규칙은 application이 판정하고 위반은 기존 422 `assistant.document_ref_invalid` |
| `GET /sessions?run_id=` | 실행 하나의 세션 목록 |
| `StartTurnRequest.context` | 필수에서 선택으로. 모드와의 짝은 R5 |
| 422 `assistant.result_unavailable` | 결과 세션 생성·턴 시작 때 결과가 없거나 완료되지 않았다 |
| 422 `assistant.turn_context_mismatch` | R5 |

OpenAPI와 frontend 생성 SDK는 같은 PR에서 갱신한다. 새 422 코드 둘은 `entities/assistant`의 거부 문구
표(`Record<AssistantRejectionCode, MessageKey>`)에 문구를 더해야 컴파일된다.

## 3. Non-goals

- 결과 영속화(backend 재시작 뒤에도 결과 조회). 실행 레지스트리의 범위다.
- 결과 화면에서 전략 제안·적용·재실행. 전략 화면 사이드바의 일이다.
- 여러 실행 비교 설명. 세션이 실행 하나에 붙는다.
- 결과 화면 사이드바 열림 상태의 새로고침 복원.
- 원시 시계열·거래 행을 모델에게 주는 도구.
- 실제 모델 설명의 품질 평가. e2e는 대본 공급자로 경로만 지킨다.

## 4. PR

| PR | 내용 |
|---|---|
| D-01 | 이 문서, PLAN D 절, 집계 도구의 D phase |
| D-02 | backend: `DocumentRef.run_id`·sqlite v3, `BacktestResultPort`·요약·도구·프롬프트·모드 분기, HTTP·OpenAPI·SDK, 대본 시나리오, 프롬프트 골든, 지표 id 골든 |
| D-03 | frontend: 결과 페이지 사이드바, 지표 한글 이름·뜻, 거부 문구, 스토리 e2e, US-DM-08 갱신, traceability, SoT 행, features README |

## 5. 완료 정의

1. 완료된 결과 화면에서 "AI에게 결과 묻기"를 눌러 "이 결과 좋은 거야?"라고 물으면, 답에 이 실행의
   총수익률 값과 샤프 비율·최대 낙폭의 쉬운 뜻이 나오고 벤치마크 비교(없으면 비교할 수 없다는 말)가
   나온다. 제안 카드는 나오지 않는다.
2. 핵심 성과 지표 여섯 개마다 영어 이름 옆에 쉬운 한글 이름과 한 줄 뜻이 보인다.
3. 결과 세션에서 모델이 `propose_strategy`를 불러도 제안이 생기지 않는다(테스트).
4. 요약 JSON이 상한을 넘지 않고, 잘린 항목이 `omitted`에 적힌다(테스트).
5. v1·v2 어시스턴트 DB가 이력을 잃지 않고 v3으로 올라간다(테스트).
6. registry 지표마다 ko·en 이름·뜻이 있다(테스트).

## 6. 롤백

D-03은 결과 페이지의 패널과 문구 추가라 revert하면 결과 화면이 이전으로 돌아간다. D-02의 sqlite v3은
v2로 내려가는 경로가 없다. D-02를 revert하면 v3 파일을 v2 코드가 "지원하지 않는 버전"으로 거부하므로
`.local/assistant.sqlite3`를 지우거나 옮겨야 한다. 로컬 단일 사용자 도구라 이력 손실은 대화 기록뿐이다.
