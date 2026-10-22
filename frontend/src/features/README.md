# features

사용자 행동 단위의 동사형 slice. 서로 다른 feature를 직접 import하지 않는다 — 조합은 한 단계 위인
widget·page가 한다.

| slice | 소유한 행동 |
| --- | --- |
| `edit-strategy` | 문서 lifecycle: source 트랜잭션, 검증·저장·실행 게이트, 업그레이드 적용, AI 제안의 편집기 적용(전체 범위 교체 한 번 + 확인) |
| `debug-strategy` | 서버 trace 표현 — 종목별 점수·순위·비중을 읽기 전용으로 보인다 |
| `check-capacity` | 원본 실행의 금액별 용량 미리 계산·명시 시작·결과 표/곡선. 비용·한계·N은 기존 backend 응답을 읽는다(US-CS-11) |
| `check-robustness` | 결과 검증 카드의 "튼튼한지 확인하기": 확인 창(새 시도 수·설계는 서버 미리 계산), 실험 시작, 끝난 실험의 표본 밖 곡선·유지율과 추천 후보 표시(US-DM-11·US-CS-09). 설계·판정은 backend가 소유한다 |
| `run-backtest` | 실행 설정 패널(스키마에서 읽은 `RunEnvironment` 칸, 코어·초기 자본·벤치마크·연환산·OOS 시작일), IDE 위쪽 실행 설정 요약 띠, 실행 시작 |
| `configure-ai-providers` | 설정 화면의 "AI 어시스턴트 공급자" 섹션: 카드 목록·활성 전환·삭제 확인·추가 폼·연결 테스트. 키는 폼의 비제어 입력을 지나 요청 본문으로만 가고 state·캐시에 남지 않는다 |
| `configure-kiwoom-mock` | 설정 화면의 "키움 모의투자 연결" 섹션(S06 ②-3, US-SM-18): 저장 여부·계좌 끝 4자리, 저장 폼, 연결 확인, 삭제 확인. 키·시크릿·계좌번호는 비제어 입력을 지나 저장 요청으로만 가고 state·캐시에 남지 않는다. 연결 확인 문장은 서버가 완성한 것을 그대로 보인다 |
| `assist-strategy` | 전략 화면 우측 채팅 사이드바: 메시지·스트리밍 텍스트·검색 활동·도구 활동·제안 카드·취소·세션 전환. 제안 적용은 콜백으로 밖에 넘긴다(feature가 feature를 부르지 않는다). 백테스트 결과 화면도 같은 사이드바를 `copy="result"`·실행 참조로 붙인다(결과 설명 spec R1). 그때는 문서 컨텍스트를 싣지 않고 제안 콜백도 없다 |

AI 어시스턴트 두 slice의 계약 정본은
[설계 spec](../../../docs/superpowers/specs/2026-09-20-ai-assistant-design.md) D7이다.
