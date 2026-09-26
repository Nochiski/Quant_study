# 한상목 유저 스토리

소프트웨어 개발자 출신이고 투자는 처음이다([페르소나](../personas.md#한상목-sm)). 전략을 코드처럼
다루며 퀀트 검증을 배우는 것이 이 사람의 여정이다. YAML과 키보드가 편하고, 금융 개념은 설명이
필요하다.

### US-SM-01 YAML을 붙여 넣고 오타를 필드 경로로 찾아 고친다

> 한상목으로서 YAML을 붙여 넣자마자 검증 결과를 보고, 틀린 곳을 정확한 경로로 알고 싶다. 그래야
> 문법을 외우지 않고도 빠르게 고칠 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: YAML 편집기 · compile 진단 · 문제 목록
- e2e:
  - `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests

수용 기준

- Given 새 전략 화면, When 올바른 YAML을 붙여 넣으면, Then 문서 상태가 "검증 통과"가 된다.
- Given 필드 이름을 틀린 YAML(`max_name_wieght`), Then 문서 상태가 "구조 오류"가 되고 "리비전
  저장"과 "백테스트"가 막히며, 문제 목록에 `/risk/max_name_wieght` 경로가 보인다.
- Given 오타를 고친 문서, When "검증"을 누르면, Then 다시 "검증 통과"가 된다.
- Given 저장하지 않은 편집, When 새로고침하면, Then 복구본과 서버 초안을 불러와 편집하던 글을 되찾는다.

### US-SM-02 필드의 단위·범위·기본값을 계약 패널에서 확인한다

> 한상목으로서 필드를 고르면 그 필드의 타입·단위·허용 범위·화면 표시 값을 보고 싶다. 그래야
> `0.05`가 5%라는 것을 추측하지 않고 안다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: 전략 구조(outline) · 계약 패널(Contract Inspector)
- e2e:
  - `frontend/e2e/stories/sm.field-contract.spec.ts` :: US-SM-02 전략 구조에서 필드를 고르면 계약 패널이 단위·범위·표시 값을 알려 준다

수용 기준

- Given 검증을 통과한 문서, When 전략 구조 필터에 `max_name_weight`를 넣고 그 항목을 고르면,
  Then 계약 패널에 경로 `/risk/max_name_weight`, 저장 값 `0.05`, 표시 값 `5%`, 기본값 `0.1`,
  범위 `> 0 · ≤ 1`, 단위 `ratio → %`, 한글 설명 "종목별 최대 목표 비중 한도"가 보인다.

### US-SM-03 저장할 때마다 새 버전이 쌓이고 버전끼리의 차이와 충돌을 본다

> 한상목으로서 전략을 git처럼 다루고 싶다. 저장마다 새 버전이 생기고, 버전 사이 의미 차이를 보고,
> 다른 탭에서 먼저 저장한 버전을 덮어쓰지 않아야 한다. 그래야 변경 이력을 믿을 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: 리비전 저장 · Diff · 전략 이력 · 저장 충돌
- e2e:
  - `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests

수용 기준

- Given 저장한 v1, When 제목을 바꿔 다시 저장하면, Then v2가 생기고 Diff 탭에서 기준 v1·대상 v2와
  바뀐 경로 `/title`이 보인다.
- Given 같은 전략을 연 두 탭, When 한 탭이 먼저 v3를 저장하고 다른 탭이 저장하면, Then 뒤의 탭에
  "리비전 충돌"과 "서버 최신 v3 · 현재 기준 v2"가 보이고, 편집하던 글은 그대로 남는다. "현재 전체
  문서로 v4 생성"을 누르면 v4가 된다.
- Given 전략 이력 화면, When "Revision 펼치기"를 누르면, Then 모든 버전이 보이고 각 줄의 Diff
  링크가 그 버전과 직전 버전의 차이를 연다.

### US-SM-04 키보드만으로 경로를 찾고 검증·저장·백테스트한다

> 한상목으로서 마우스 없이 명령 팔레트와 단축키로 일하고 싶다. 그래야 편집기에서 손을 떼지 않는다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: 명령 팔레트 · 전역 단축키
- e2e:
  - `frontend/e2e/stories/sm.keyboard.spec.ts` :: US-SM-04 키보드만으로 문서 경로를 찾고 검증·저장·백테스트까지 간다

수용 기준

- Given 검증을 통과한 새 문서, When `Ctrl+K`로 명령 팔레트를 열고 `/risk/max_name_weight`를
  찾아 Enter를 누르면, Then 주소에 그 경로가 남고 계약 패널이 그 필드를 보인다.
- Given 같은 문서, When `Ctrl+Enter`를 누르면 서버 검증이 다시 돌고, `Ctrl+S`를 누르면 v1이
  저장되고 문서 상태가 "저장됨"이다.
- Given 저장된 v1, When `Ctrl+Shift+Enter`를 누르면, Then 백테스트 실행 화면으로 가고 결과가 나온다.

### US-SM-05 실행 기록으로 같은 백테스트를 그대로 다시 돌린다

> 한상목으로서 백테스트가 무엇으로 어떻게 돌았는지 기록을 보고 같은 조건으로 다시 돌리고 싶다.
> 그래야 결과를 재현할 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: 실행 설정 · run manifest · 동일 설정 재실행 · 실행 취소
- e2e:
  - `frontend/e2e/workbench.workflow.spec.ts` :: creates, recovers, validates, versions, traces and backtests
  - `frontend/e2e/workbench.workflow.spec.ts` :: cancels a nonterminal run and replays the server-owned request byte-for-byte

수용 기준

- Given 실행 설정에서 초기 자본을 0으로 둔 상태, When 백테스트를 누르면, Then "백테스트 시작 실패"와
  422 사유가 보인다.
- Given 엔진·초기 자본·벤치마크·연환산 거래일·OOS 시작일을 정한 실행, Then 결과의 "Manifest ·
  데이터 경고 · 재현성 정보"에 같은 값과 전략 버전, 실행 지문이 보인다.
- Given 실행 중인 백테스트, When "실행 취소"를 누르면 상태가 cancelled가 되고, "동일 설정
  재실행"을 누르면 서버가 기억한 요청 그대로 새 실행이 시작된다.

### US-SM-06 같은 전략을 다른 실행 설정으로 돌려도 전략 해시는 같다

> 한상목으로서 기간이나 비용만 바꿔 돌렸을 때 전략 자체는 같다는 것을 해시로 확인하고 싶다.
> 그래야 결과 차이가 전략이 아니라 실행 조건에서 왔다고 말할 수 있다.

- 상태: `예정`
- 담당 PR: P3-03
- 기능 영역: 실행 설정(`RunEnvironment`) · run manifest · `spec_hash`
- e2e: 없음

수용 기준

- Given 저장한 전략 하나, When 기간만 다른 실행 설정으로 두 번 백테스트하면, Then 두 결과의 실행
  기록에서 전략 해시는 같고 실행 설정 해시(`environment_hash`)는 다르다.
- 비고: lang2 완료 정의 5번과 Phase 3 exit "같은 전략·다른 기간 → 같은 spec_hash e2e"가 이 스토리다.

### US-SM-07 예전 형식으로 저장한 전략을 새 형식으로 올린다

> 한상목으로서 예전 schema로 저장한 전략을 열었을 때 의미를 바꾸지 않고 새 형식으로 올리고 싶다.
> 그래야 옛 전략도 계속 돌릴 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: schema 업그레이드 · 동결 리비전
- e2e:
  - `frontend/e2e/workbench.workflow.spec.ts` :: upgrades a frozen 1.0 revision, saves it as 1.1 and backtests it
  - `frontend/e2e/workbench.workflow.spec.ts` :: migrates a source-less legacy revision without changing meaning

수용 기준

- Given schema 1.0 리비전, Then "이 문서는 schema 1.0입니다" 안내가 뜨고 백테스트가 막힌다.
- Given 그 안내, When "1.1로 업그레이드"를 누르고 저장하면, Then 새 버전이 생기고 안내가 사라지며
  백테스트가 끝까지 돈다. 전략 이력에는 옛 버전에만 "1.0 동결" 표시가 남는다.
- Given 원문 없이 JSON으로만 저장된 옛 전략, Then "legacy JSON에서 생성된 문서"라는 안내와 함께
  열리고, 저장하면 의미 해시가 같은 새 버전이 된다.
- 비고: 1.1 → 1.2 업그레이드는 P2-09·P3-02가 이 스토리의 수용 기준을 넓힌다.

### US-SM-08 Form·Graph로 고쳐도 YAML 원문은 그 줄만 바뀐다

> 한상목으로서 GUI로 값을 바꿔도 YAML의 주석과 순서가 그대로이길 바란다. 그래야 리비전 diff를
> 믿고 리뷰할 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- 기능 영역: Form 편집 · Graph 편집 · source 트랜잭션
- e2e:
  - `frontend/e2e/workbench.workflow.spec.ts` :: edits through the Form with the same hash as a YAML edit and adds a catalog factor that reaches the plan
  - `frontend/e2e/workbench.workflow.spec.ts` :: adds a node in the Graph editor, rewires an input, refreshes the plan and saves

수용 기준

- Given 저장한 전략, When Form에서 `max_name_weight`를 0.1로 바꾸면, Then YAML 원문은 그 값만
  바뀌고 주석과 순서는 그대로이며, 같은 값을 YAML로 직접 고친 문서와 의미 해시가 같다.
- Given 저장한 전략, When Graph에서 노드를 더하고 입력을 다시 이으면, Then YAML에는 그 노드와 입력
  줄만 생기고 나머지 앞부분은 그대로다.
- 비고: P4-04가 Form 탭을 은퇴시키면 첫 기준은 그래프 카드의 같은 동작으로 바뀐다.
