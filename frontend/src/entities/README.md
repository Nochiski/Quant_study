# entities

도메인 명사 단위의 조회·기본 표현. entity끼리 직접 import하지 않는다.

| slice | 소유한 명사 |
| --- | --- |
| `strategy` | 전략 문서·리비전·초안 query와 mutation |
| `factor` | 팩터 카탈로그와 그래프 설명 |
| `dataset` | 데이터 필드·유니버스 조회 |
| `backtest` | 실행 시작·상태·결과 query |
| `assistant` | AI 공급자 프로파일, 채팅 세션·턴, SSE 리더와 이벤트 리듀서. 생성 SDK 타입과 `shared/api`의 어시스턴트 상수·오류 타입을 여기서 다시 내보내 상위 레이어의 입구를 하나로 둔다 |

`assistant`의 SSE 리더는 생성 SDK의 SSE 클라이언트를 쓰고 `EventSource`를 쓰지 않는다. 리듀서는
이미 반영한 sequence 이하를 무시해, 재연결로 같은 이벤트를 다시 받아도 화면이 두 번 바뀌지 않는다.
