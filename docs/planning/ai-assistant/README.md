# AI 어시스턴트 기획 패키지

설정 화면에서 LLM 공급자(Claude·Codex)를 연결하고, 전략 화면 우측 사이드바 채팅에서 LLM이 현재
전략·데이터 카탈로그·인터넷 검색으로 시장을 조사해 전략을 제안하게 하는 initiative의 기획 자료를
한곳에 보관한다.

## 문서 구성

- [설계 spec](../../superpowers/specs/2026-09-20-ai-assistant-design.md): 결정(D1~D9), non-goal,
  완료 정의. 이 initiative의 계약 SoT (2026-09-20 P0-01 리뷰 반영판)
- [WORKFLOW.md](./WORKFLOW.md): Phase별 PR scope packet과 Phase 종료 gate
- [PLAN.md](./PLAN.md): PR 상태, 리뷰·검증 결과를 계속 갱신하는 단일 진행 추적 파일
- [tools/update-plan-progress.ps1](./tools/update-plan-progress.ps1): PLAN.md 집계 갱신

## 기준

- 실행 절차는 [YAML Strategy Workbench WORKFLOW 12~14절](../strategy-workbench-yaml-ui/WORKFLOW.md)을
  그대로 따른다(scope packet → self-check → diff freeze → Opus reviewer 1명 → 재검토 → APPROVE →
  merge).
- 각 PR은 직전 PR 브랜치를 base로 하는 stacked GitHub PR. 스택 바닥은 `main`.
- 공급자 SDK(`anthropic`, `openai`)는 `adapters/outbound/llm_*` 밖에서 import하지 않는다.
  architecture 테스트가 강제한다.
- 비밀(API 키)은 저장소·DB·로그·응답·OpenAPI 어디에도 평문으로 두지 않는다. 테스트로 고정한다.
- 실제 공급자 호출 테스트는 `RUN_LLM_LIVE=1`일 때만 돈다. CI는 가짜 공급자만 쓴다.
- `PLAN.md`가 진행 상태의 단일 기준이다.
- 이 initiative는 schema 1.2·그래프 표현 initiative(PR #167)와 파일이 겹치지 않는다. 어시스턴트는 현재
  스키마 버전을 runtime schema에서 읽으므로 1.1이든 1.2든 같은 코드로 동작한다. 단 그쪽이 먼저
  머지되면 A-07 fixture와 B-05 e2e를 1.2 문서로 갱신한다.

## 진행 상태 갱신 규칙

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)의 tracker 규칙을
따른다(IN_PROGRESS → SELF_CHECK → IN_REVIEW → APPROVED → MERGED). Phase 종료마다 별도 서브에이전트로
SoT(`.claude/rules/strategy-workbench-sot.md`)와 책임 분리(`backend-package-boundary.md`, `frontend-fsd.md`)를
점검하고 결과를 Phase exit에 기록한다.
