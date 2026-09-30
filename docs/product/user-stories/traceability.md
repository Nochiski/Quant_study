# 유저 스토리 추적 표

스토리 → e2e 테스트 → 상태 → 담당 PR·e2e 담당 PR을 한눈에 보는 색인이다. 스토리의 문구·상태·담당 PR은
[stories/](./stories/)가, 어느 테스트가 어느 스토리를 지키는지는 e2e의 `@US-*` 태그가 소유한다.

아래 두 표는 그 둘에서 만든다. **손으로 고치지 않는다.** 스토리나 태그를 바꾼 뒤 저장소 루트에서
다시 쓴다.

```text
uv run python -m quant_study_dev.user_story_trace --write
```

CI의 `user-story-harness` job이 이 표와 스토리 파일·e2e 태그가 글자 하나까지 같은지 검사한다.
담당 PR ID는 계획마다 접두사가 다르다. `P1-03` 같은 `P*`는 [strategy-language-2-0 WORKFLOW](../../planning/strategy-language-2-0/WORKFLOW.md),
`V1-01` 같은 `V*`는 [validation-lab WORKFLOW](../../planning/validation-lab/WORKFLOW.md)의 PR ID다. 계획 문서에 없는
PR은 GitHub 번호(`#123`)로 적는다.

<!-- USER-STORY-TRACE:START -->
### 상태 요약

| 페르소나 | `구현됨-e2e` | `구현됨-e2e없음` | `예정` | `미계획` | 합계 |
|---|---:|---:|---:|---:|---:|
| 김철수 | 4 | 2 | 6 | 1 | 13 |
| 정동민 | 8 | 0 | 3 | 0 | 11 |
| 한상목 | 12 | 1 | 2 | 1 | 16 |
| 합계 | 24 | 3 | 11 | 2 | 40 |

### 스토리별 추적

| 스토리 | 페르소나 | 제목 | 상태 | 담당 PR | e2e 담당 | e2e (파일 :: 테스트) |
|---|---|---|---|---|---|---|
| US-CS-01 | 김철수 | 그래프 편집기에서 노드를 더하고 다시 이어 팩터 계산을 바꾼다 | `구현됨-e2e` | — | — | `frontend/e2e/workbench.workflow.spec.ts` :: adds a node in the Graph editor, rewires an input, refreshes the plan and saves |
| US-CS-02 | 김철수 | 두 원천 필드를 가공한 파생 팩터를 정의하고 기존 팩터와 결합한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/cs.derived-factor.spec.ts` :: US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다 |
| US-CS-03 | 김철수 | 중간값 추적과 실행 계획으로 계산과 공개 시점을 검증한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/cs.derived-factor.spec.ts` :: US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다<br>`frontend/e2e/stories/cs.masked-trace.spec.ts` :: US-CS-03 원장이 가린 신용잔고 칸을 건넌 노드 값과 그 원시 셀을 원장이 가림으로 본다<br>`frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-CS-04 | 김철수 | AI에게 팩터 자료 조사와 파라미터 조정안을 맡긴다 | `구현됨-e2e` | — | — | `frontend/e2e/assistant.workflow.spec.ts` :: 검색 상한에 닿은 턴은 검색 칩 대신 안내 문구를 보이고 새로고침해도 같다<br>`frontend/e2e/assistant.workflow.spec.ts` :: 검색 출처를 링크로 보이고 검증에 실패한 턴은 실패 문구로 끝난다<br>`frontend/e2e/assistant.workflow.spec.ts` :: 팩터 그래프를 바꾸는 제안도 적용 후 백테스트가 팩터 계획 조회를 기다려 실행한다 |
| US-CS-05 | 김철수 | 단위가 다른 팩터를 결합 전에 정규화한다 | `구현됨-e2e없음` | P5-03 | P5-03 | — |
| US-CS-06 | 김철수 | 거래대금 상위 20% 같은 횡단면 필터와 변동성 역가중을 쓴다 | `구현됨-e2e없음` | P5-03 | P5-03 | — |
| US-CS-07 | 김철수 | 노드 캔버스에서 끌어서 잇고 되돌린다 | `예정` | P6-02, P6-03 | P6-02 | — |
| US-CS-08 | 김철수 | 파생 팩터를 저장해 두고 다른 전략에서 다시 쓴다 | `미계획` | — | — | — |
| US-CS-09 | 김철수 | 워크포워드 실험으로 표본 밖 성과를 본다 | `예정` | V3-01, V3-05, V5-01, V5-02 | V5-02 | — |
| US-CS-10 | 김철수 | 파라미터 지도에서 고원을 확인하고 후보를 고른다 | `예정` | V4-03, V5-04 | V5-04 | — |
| US-CS-11 | 김철수 | 비용을 현실적으로 잡고 운용 금액별 용량을 본다 | `예정` | V2-01, V2-02, V2-03, V4-04, V5-06 | V5-06 | — |
| US-CS-12 | 김철수 | 팩터 회귀로 진짜 알파가 남는지 본다 | `예정` | V4-05, V5-06 | V5-06 | — |
| US-CS-13 | 김철수 | 기준을 먼저 적고 홀드아웃을 한 번만 연다 | `예정` | V6-01, V6-02 | V6-02 | — |
| US-DM-01 | 정동민 | AI 공급자를 한 번 연결해 둔다 | `구현됨-e2e` | — | — | `frontend/e2e/assistant.workflow.spec.ts` :: 설정에서 공급자를 등록하면 활성이 되고 키는 꼬리 4자리만 남는다 |
| US-DM-02 | 정동민 | 모르는 말을 전략 화면에서 바로 묻는다 | `구현됨-e2e` | — | — | `frontend/e2e/assistant.workflow.spec.ts` :: 사이드바 질문에 답이 스트리밍되고 새로고침해도 이력과 진행 중 턴이 이어진다 |
| US-DM-03 | 정동민 | 말로 한 아이디어를 AI가 전략으로 바꿔 주고 바로 백테스트한다 | `구현됨-e2e` | — | — | `frontend/e2e/assistant.workflow.spec.ts` :: 제안 카드를 미리 보고 적용하고 실행 취소·다시 실행한 뒤 적용 후 백테스트가 실행 화면까지 간다<br>`frontend/e2e/stories/dm.ai-new-strategy.spec.ts` :: US-DM-03 빈 새 전략에서 AI에게 아이디어를 말해 받은 전략을 적용하고 백테스트한다<br>`frontend/e2e/stories/dm.ai-new-strategy.spec.ts` :: US-DM-03 사이드바와 계약 서랍을 어느 입구로 펼쳐도 머리 줄이 펼친 자리에서 눌린다<br>`frontend/e2e/stories/dm.sidebar-scrollbar.spec.ts` :: US-DM-03 스크롤바가 폭을 차지하는 창에서도 사이드바가 붙었다 떴다 하지 않는다 |
| US-DM-04 | 정동민 | 백테스트 결과에서 핵심 숫자와 자산 곡선을 본다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/dm.backtest-result.spec.ts` :: US-DM-04 저장한 전략을 백테스트하면 핵심 성과 지표 일곱 개와 자산 곡선이 보인다 |
| US-DM-05 | 정동민 | 기간·유니버스·수수료·슬리피지를 전략 밖 실행 설정에서 정한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/dm.run-environment.spec.ts` :: US-DM-05 날짜 칸에 숫자를 이어 치거나 대시를 넣어 쳐도 그 날짜가 들어가고, 덜 친 날짜는 칸이 알려 준다<br>`frontend/e2e/stories/dm.run-environment.spec.ts` :: US-DM-05 실행 설정에서 기간만 바꿔 다시 돌려도 전략은 그대로이고 실행 기록에 바꾼 기간이 남는다 |
| US-DM-06 | 정동민 | 화면의 말과 오류 문장을 쉬운 한글로 읽는다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/dm.readable-korean.spec.ts` :: US-DM-06 결과 파일을 읽을 수 없는 완료 실행은 결과 화면이 다시 실행하라고 말하고 결과를 다시 묻지 않는다<br>`frontend/e2e/stories/dm.readable-korean.spec.ts` :: US-DM-06 그래프 편집 화면은 노드 종류·연산자·필드를 한글 이름과 설명으로 보이고 삭제 거부를 노드 이름으로 말한다<br>`frontend/e2e/stories/dm.readable-korean.spec.ts` :: US-DM-06 실패한 옛 실행은 백테스트 이력과 결과 화면에서 같은 한글 문장으로 보이고 재실행 거절은 고칠 곳을 말한다<br>`frontend/e2e/stories/dm.readable-korean.spec.ts` :: US-DM-06 필드 이름을 틀리거나 1.0 문법을 쓰면 문제 목록이 한글로 고칠 방법을 말한다<br>`frontend/e2e/stories/dm.readable-korean.spec.ts` :: US-DM-06 한 번도 사지 않은 실행과 데이터가 모르는 벤치마크는 실패 문장이 고칠 곳을 말한다 |
| US-DM-07 | 정동민 | 빈 문서에서 그래프 화면만으로 전략을 만들어 백테스트한다 | `예정` | P4-04, P5-03 | P4-04, P5-03 | — |
| US-DM-08 | 정동민 | 백테스트 결과를 AI에게 쉬운 말로 풀어 달라고 한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/dm.result-explain.spec.ts` :: US-DM-08 완료된 백테스트 결과에서 AI에게 좋은 결과인지 물으면 지표 뜻과 벤치마크 비교를 쉬운 말로 답한다 |
| US-DM-09 | 정동민 | AI 제안을 적용한 뒤 버튼 한 번으로 되돌린다 | `구현됨-e2e` | — | — | `frontend/e2e/assistant.workflow.spec.ts` :: 제안 카드를 미리 보고 적용하고 실행 취소·다시 실행한 뒤 적용 후 백테스트가 실행 화면까지 간다 |
| US-DM-10 | 정동민 | 결과가 운으로 설명되는지 쉬운 말로 본다 | `예정` | V5-02 | V5-02 | — |
| US-DM-11 | 정동민 | 버튼 하나로 지금 설정이 튼튼한지 확인한다 | `예정` | V3-05, V5-02 | V5-02 | — |
| US-SM-01 | 한상목 | YAML을 붙여 넣고 오타를 필드 경로로 찾아 고친다 | `구현됨-e2e` | — | — | `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-02 | 한상목 | 필드의 단위·범위·기본값을 계약 패널에서 확인한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.field-contract.spec.ts` :: US-SM-02 전략 구조에서 필드를 고르면 계약 패널이 단위·범위·표시 값을 알려 준다 |
| US-SM-03 | 한상목 | 저장할 때마다 새 버전이 쌓이고 버전끼리의 차이와 충돌을 본다 | `구현됨-e2e` | — | — | `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-04 | 한상목 | 키보드만으로 경로를 찾고 검증·저장·백테스트한다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.keyboard.spec.ts` :: US-SM-04 키보드만으로 문서 경로를 찾고 검증·저장·백테스트까지 간다 |
| US-SM-05 | 한상목 | 실행 기록으로 같은 백테스트를 그대로 다시 돌린다 | `구현됨-e2e` | — | — | `frontend/e2e/workbench.workflow.spec.ts` :: cancels a nonterminal run and replays the server-owned request byte-for-byte<br>`frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-06 | 한상목 | 같은 전략을 다른 실행 설정으로 돌려도 전략 해시는 같다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/dm.run-environment.spec.ts` :: US-DM-05 실행 설정에서 기간만 바꿔 다시 돌려도 전략은 그대로이고 실행 기록에 바꾼 기간이 남는다 |
| US-SM-07 | 한상목 | 예전 형식으로 저장한 전략을 새 형식으로 올린다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.upgrade-new-strategy.spec.ts` :: US-SM-07 새 전략 화면에 옛 schema YAML을 붙여 넣으면 배너로 올리고 실행 설정을 채워 저장·백테스트한다<br>`frontend/e2e/stories/sm.upgrade-new-strategy.spec.ts` :: US-SM-07 업그레이드할 수 없는 옛 문서에는 배너 대신 문제 목록이 고칠 곳을 말한다<br>`frontend/e2e/workbench.workflow.spec.ts` :: migrates a source-less legacy revision without changing meaning<br>`frontend/e2e/workbench.workflow.spec.ts` :: upgrades frozen 1.1 and 1.0 revisions to the current schema, fills their run settings, saves them and backtests them |
| US-SM-08 | 한상목 | Form·Graph로 고쳐도 YAML 원문은 그 줄만 바뀐다 | `구현됨-e2e` | — | — | `frontend/e2e/workbench.workflow.spec.ts` :: adds a node in the Graph editor, rewires an input, refreshes the plan and saves<br>`frontend/e2e/workbench.workflow.spec.ts` :: edits through the Form with the same hash as a YAML edit and adds a catalog factor that reaches the plan |
| US-SM-09 | 한상목 | 모르는 금융 개념을 편집 흐름 안에서 설명받는다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.finance-terms.spec.ts` :: US-SM-09 편집 중에 AI와 계약 패널로 금융 개념의 뜻을 확인한다 |
| US-SM-10 | 한상목 | 실행 설정 항목 옆에서 용어 뜻을 바로 본다 | `미계획` | — | — | — |
| US-SM-11 | 한상목 | 백테스트를 다시 눌러도 같은 계산이 겹쳐 돌지 않는다 | `구현됨-e2e없음` | V3-04 | V5-01 | — |
| US-SM-12 | 한상목 | 시도 수가 자동으로 쌓이고 지울 수 없다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.trial-ledger.spec.ts` :: US-SM-12 시도 원장은 설정을 바꾼 실행만 새 시도로 세고 재확인·결과 없는 요청을 따로 보이며 합치기만 있다 |
| US-SM-13 | 한상목 | 봉인 구간과 겹치는 실행이 이유와 교정 버튼과 함께 막힌다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.research-window.spec.ts` :: US-SM-13 봉인 구간과 겹치는 시작일은 이유와 교정 버튼과 함께 막히고, 실행 전에 시도 수 영향을 알려 준다 |
| US-SM-14 | 한상목 | 실험 설정을 표로 보고 같은 실험을 다시 만든다 | `예정` | V3-03, V5-01 | V5-01 | — |
| US-SM-15 | 한상목 | 여러 실험을 대기열에 넣고 창을 닫아도 계속 돈다 | `예정` | V3-04, V5-01 | V5-01 | — |
| US-SM-16 | 한상목 | 실행 전에 이 실행이 시도 수를 늘리는지 본다 | `구현됨-e2e` | — | — | `frontend/e2e/stories/sm.research-window.spec.ts` :: US-SM-13 봉인 구간과 겹치는 시작일은 이유와 교정 버튼과 함께 막히고, 실행 전에 시도 수 영향을 알려 준다 |
<!-- USER-STORY-TRACE:END -->
