# pages

라우트 단위 화면 조립. widgets/features/entities의 public API만 조합하고 자기 로직을 두지 않는다.
전략 authoring route는 YAML/JSON source 하나만 소유한다.

| slice | route |
| --- | --- |
| `research-strategies` | 전략 목록 |
| `research-strategy-new` | 새 전략 초안 |
| `research-strategy-revision` | 저장된 리비전 편집 |
| `research-backtests` · `research-backtest` | 실행 이력과 실행 하나 |
| `operations-placeholder` · `route-states` | 아직 기능이 없는 메뉴, 로딩·오류 화면 |
| `settings` | `/settings` — 지금은 "AI 어시스턴트 공급자" 섹션 하나 |

전략 route 두 곳이 채팅과 편집기를 잇는 유일한 지점이다: 사이드바가 넘긴 제안을 `edit-strategy`의
적용 훅으로 옮기고, 턴을 시작할 때마다 지금 편집기 텍스트·진단·실행 설정을 실어 보낸다.
