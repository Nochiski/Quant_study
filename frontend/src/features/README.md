# features

사용자 행동 단위의 동사형 slice. 서로 다른 feature를 직접 import하지 않는다 — 조합은 한 단계 위인
widget·page가 한다.

| slice | 소유한 행동 |
| --- | --- |
| `edit-strategy` | 문서 lifecycle: source 트랜잭션, 검증·저장·실행 게이트, 업그레이드 적용, AI 제안의 편집기 적용(전체 범위 교체 한 번 + 확인) |
| `debug-strategy` | 서버 trace 표현 — 종목별 점수·순위·비중을 읽기 전용으로 보인다 |
| `run-backtest` | 실행 설정(코어·초기 자본·벤치마크·지표 구간)과 실행 시작 |
| `configure-ai-providers` | 설정 화면의 "AI 어시스턴트 공급자" 섹션: 카드 목록·활성 전환·삭제 확인·추가 폼·연결 테스트. 키는 폼의 비제어 입력을 지나 요청 본문으로만 가고 state·캐시에 남지 않는다 |
| `assist-strategy` | 전략 화면 우측 채팅 사이드바: 메시지·스트리밍 텍스트·검색 활동·도구 활동·제안 카드·취소·세션 전환. 제안 적용은 콜백으로 밖에 넘긴다(feature가 feature를 부르지 않는다) |

AI 어시스턴트 두 slice의 계약 정본은
[설계 spec](../../../docs/superpowers/specs/2026-09-20-ai-assistant-design.md) D7이다.
