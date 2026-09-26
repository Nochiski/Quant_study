# 유저 스토리 추적 표

스토리 → e2e 테스트 → 상태 → 담당 PR을 한눈에 보는 색인이다. 스토리의 문구·상태·담당 PR은
[stories/](./stories/)가, 어느 테스트가 어느 스토리를 지키는지는 e2e의 `@US-*` 태그가 소유한다.

아래 두 표는 그 둘에서 만든다. **손으로 고치지 않는다.** 스토리나 태그를 바꾼 뒤 저장소 루트에서
다시 쓴다.

```text
uv run python -m quant_study_dev.user_story_trace --write
```

CI의 `user-story-harness` job이 이 표와 스토리 파일·e2e 태그가 글자 하나까지 같은지 검사한다.
담당 PR ID(`P1-03` 등)는 [strategy-language-2-0 WORKFLOW](../../planning/strategy-language-2-0/WORKFLOW.md)의
PR ID다. 그 계획 패키지는 P0-01이 main에 올리기 전까지 `docs/strategy-language-2-0-plan` 브랜치에
있어 위 링크가 잠시 비어 있을 수 있다.

<!-- USER-STORY-TRACE:START -->
### 상태 요약

| 페르소나 | `구현됨-e2e` | `구현됨-e2e없음` | `예정` | `미계획` | 합계 |
|---|---:|---:|---:|---:|---:|
| 김철수 | 4 | 0 | 3 | 1 | 8 |
| 정동민 | 4 | 0 | 3 | 1 | 8 |
| 한상목 | 7 | 0 | 1 | 0 | 8 |
| 합계 | 15 | 0 | 7 | 2 | 24 |

### 스토리별 추적

| 스토리 | 페르소나 | 제목 | 상태 | 담당 PR | e2e (파일 :: 테스트) |
|---|---|---|---|---|---|
| US-CS-01 | 김철수 | 그래프 편집기에서 노드를 더하고 다시 이어 팩터 계산을 바꾼다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: adds a node in the Graph editor, rewires an input, refreshes the plan and saves |
| US-CS-02 | 김철수 | 두 원천 필드를 가공한 파생 팩터를 정의하고 기존 팩터와 결합한다 | `구현됨-e2e` | — | `frontend/e2e/stories/cs.derived-factor.spec.ts` :: US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다 |
| US-CS-03 | 김철수 | 중간값 추적과 실행 계획으로 계산과 공개 시점을 검증한다 | `구현됨-e2e` | — | `frontend/e2e/stories/cs.derived-factor.spec.ts` :: US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다<br>`frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-CS-04 | 김철수 | AI에게 팩터 자료 조사와 파라미터 조정안을 맡긴다 | `구현됨-e2e` | — | `frontend/e2e/assistant.workflow.spec.ts` :: 검색 출처를 링크로 보이고 검증에 실패한 턴은 실패 문구로 끝난다<br>`frontend/e2e/assistant.workflow.spec.ts` :: 팩터 그래프를 바꾸는 제안도 적용 후 백테스트가 팩터 계획 조회를 기다려 실행한다 |
| US-CS-05 | 김철수 | 단위가 다른 팩터를 결합 전에 정규화한다 | `예정` | P2-04, P3-01 | — |
| US-CS-06 | 김철수 | 거래대금 상위 20% 같은 횡단면 필터와 변동성 역가중을 쓴다 | `예정` | P2-05, P2-06, P3-01 | — |
| US-CS-07 | 김철수 | 노드 캔버스에서 끌어서 잇고 되돌린다 | `예정` | P6-02, P6-03 | — |
| US-CS-08 | 김철수 | 파생 팩터를 저장해 두고 다른 전략에서 다시 쓴다 | `미계획` | — | — |
| US-DM-01 | 정동민 | AI 공급자를 한 번 연결해 둔다 | `구현됨-e2e` | — | `frontend/e2e/assistant.workflow.spec.ts` :: 설정에서 공급자를 등록하면 활성이 되고 키는 꼬리 4자리만 남는다 |
| US-DM-02 | 정동민 | 모르는 말을 전략 화면에서 바로 묻는다 | `구현됨-e2e` | — | `frontend/e2e/assistant.workflow.spec.ts` :: 사이드바 질문에 답이 스트리밍되고 새로고침해도 이력과 진행 중 턴이 이어진다 |
| US-DM-03 | 정동민 | 말로 한 아이디어를 AI가 전략으로 바꿔 주고 바로 백테스트한다 | `구현됨-e2e` | — | `frontend/e2e/assistant.workflow.spec.ts` :: 제안 카드를 미리 보고 적용한 뒤 적용 후 백테스트가 실행 화면까지 간다 |
| US-DM-04 | 정동민 | 백테스트 결과에서 핵심 숫자와 자산 곡선을 본다 | `구현됨-e2e` | — | `frontend/e2e/stories/dm.backtest-result.spec.ts` :: US-DM-04 저장한 전략을 백테스트하면 핵심 성과 지표 여섯 개와 자산 곡선이 보인다 |
| US-DM-05 | 정동민 | 기간·유니버스·수수료·슬리피지를 전략 밖 실행 설정에서 정한다 | `예정` | P2-01, P3-02 | — |
| US-DM-06 | 정동민 | 화면의 말과 오류 문장을 쉬운 한글로 읽는다 | `예정` | P1-03, P1-05 | — |
| US-DM-07 | 정동민 | 빈 문서에서 그래프 화면만으로 전략을 만들어 백테스트한다 | `예정` | P4-04, P5-03 | — |
| US-DM-08 | 정동민 | 백테스트 결과를 AI에게 쉬운 말로 풀어 달라고 한다 | `미계획` | — | — |
| US-SM-01 | 한상목 | YAML을 붙여 넣고 오타를 필드 경로로 찾아 고친다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-02 | 한상목 | 필드의 단위·범위·기본값을 계약 패널에서 확인한다 | `구현됨-e2e` | — | `frontend/e2e/stories/sm.field-contract.spec.ts` :: US-SM-02 전략 구조에서 필드를 고르면 계약 패널이 단위·범위·표시 값을 알려 준다 |
| US-SM-03 | 한상목 | 저장할 때마다 새 버전이 쌓이고 버전끼리의 차이와 충돌을 본다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-04 | 한상목 | 키보드만으로 경로를 찾고 검증·저장·백테스트한다 | `구현됨-e2e` | — | `frontend/e2e/stories/sm.keyboard.spec.ts` :: US-SM-04 키보드만으로 문서 경로를 찾고 검증·저장·백테스트까지 간다 |
| US-SM-05 | 한상목 | 실행 기록으로 같은 백테스트를 그대로 다시 돌린다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: cancels a nonterminal run and replays the server-owned request byte-for-byte<br>`frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests |
| US-SM-06 | 한상목 | 같은 전략을 다른 실행 설정으로 돌려도 전략 해시는 같다 | `예정` | P3-03 | — |
| US-SM-07 | 한상목 | 예전 형식으로 저장한 전략을 새 형식으로 올린다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: migrates a source-less legacy revision without changing meaning<br>`frontend/e2e/workbench.workflow.spec.ts` :: upgrades a frozen 1.0 revision, saves it as 1.1 and backtests it |
| US-SM-08 | 한상목 | Form·Graph로 고쳐도 YAML 원문은 그 줄만 바뀐다 | `구현됨-e2e` | — | `frontend/e2e/workbench.workflow.spec.ts` :: adds a node in the Graph editor, rewires an input, refreshes the plan and saves<br>`frontend/e2e/workbench.workflow.spec.ts` :: edits through the Form with the same hash as a YAML edit and adds a catalog factor that reaches the plan |
<!-- USER-STORY-TRACE:END -->
