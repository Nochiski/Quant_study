# StrategySpec schema 1.1 · Form/Graph 편집 기획 패키지

전략 YAML 규칙 간소화(schema 1.1)와 Form/Graph GUI 편집을 stacked PR로 구현하는 initiative의
기획 자료를 한곳에 보관한다.

## 문서 구성

- [설계 spec](../../superpowers/specs/2026-09-17-strategy-schema-1-1-and-gui-editing-design.md):
  결정(D1~D9), non-goal, 대안. 이 initiative의 계약 SoT
- [WORKFLOW.md](./WORKFLOW.md): PR별 scope packet(intent, acceptance, 파일, 테스트, non-goal)과
  Phase 종료 gate
- [PLAN.md](./PLAN.md): PR 상태, 리뷰·검증 결과를 계속 갱신하는 단일 진행 추적 파일
- [tools/update-plan-progress.ps1](./tools/update-plan-progress.ps1): PLAN.md frontmatter와
  집계 표 자동 갱신

## 기준

- 실행 절차(scope packet → self-check → diff freeze → reviewer 1명 → 재검토 → APPROVE → merge)는
  [YAML Strategy Workbench WORKFLOW 12~14절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 그대로
  따른다. 이 패키지는 절차를 복제하지 않는다.
- reviewer 서브에이전트는 Opus로만 배정한다.
- 각 PR은 직전 PR 브랜치를 base로 하는 stacked GitHub PR로 올린다. 스택 바닥은 `main`이다.
- `PLAN.md`가 진행 상태의 단일 기준이다. 상위 제품 milestone은 기존
  [Strategy Workbench 로드맵](../../superpowers/specs/2026-09-03-strategy-workbench-roadmap.md)이
  소유한다.
- WORKFLOW의 PR 범위를 바꾸면 먼저 변경 이유를 `PLAN.md` 변경 기록에 남긴다.

## 진행 상태 갱신 규칙

1. 작업 시작 전에 대상 PR을 `IN_PROGRESS`로 바꾸고 `현재 작업 Packet`을 갱신한다.
2. 구현자 검증이 끝나면 `SELF_CHECK`로 바꾸고 검증 명령과 결과를 기록한다.
3. diff를 고정한 뒤 새 리뷰 서브에이전트 한 명을 배정하고 `IN_REVIEW`로 바꾼다.
4. 수정이 생기면 같은 리뷰어가 재검토한다.
5. `APPROVED` 후 로컬 gate를 통과하면 merge하고 체크박스와 상태를 `MERGED`로 바꾼다.
6. PR row와 변경 기록을 수정한 뒤 `tools/update-plan-progress.ps1`을 실행한다.
7. Phase가 끝날 때마다 별도 서브에이전트로 SoT(`.claude/rules/strategy-workbench-sot.md`)와
   책임 분리(`backend-package-boundary.md`, `frontend-fsd.md`)를 점검하고 결과를 Phase exit에
   기록한다.
