# 검증 랩 기획 패키지

백테스트 결과가 운·과최적화·비용 착시인지를 앱이 먼저 묻고 기록하게 하는 initiative의 기획 자료를 한곳에
보관한다. 연구 구간 잠금(봉인), 시도 원장과 전략 계열, 실험(그리드·워크포워드)과 비동기 대기열, 비용
현실화(매도 거래세·√ 충격·평균 거래대금 한도), 검증 통계(PSR·DSR·고원·팩터 회귀·용량), 결과 화면 검증
카드, 홀드아웃 1회 개봉을 다룬다.

## 문서 구성

- [설계 spec](../../superpowers/specs/2026-09-29-validation-lab-design.md): 결정(D1~D11), non-goal, 완료 정의.
  이 initiative의 계약 SoT
- [WORKFLOW.md](./WORKFLOW.md): Phase별 PR scope packet과 공통 gate
- [PLAN.md](./PLAN.md): PR 상태, 리뷰·검증 결과를 계속 갱신하는 단일 진행 추적 파일
- [tools/update-plan-progress.ps1](./tools/update-plan-progress.ps1): PLAN.md 집계 갱신
- 디자인보드: [전략 검증 랩 UI/UX 기획](https://claude.ai/artifact/5jYGBTPKcWFHhjB4fvEtup)
- 수학 노트: [검증 랩 수학 노트](https://claude.ai/artifact/CieccrnXuEpin4DWmvuXvu)

## 기준

- 실행 절차는 [YAML Strategy Workbench WORKFLOW 12~14절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다
  (scope packet → self-check → diff freeze → Opus reviewer 1명 → 재검토 → APPROVE → merge).
- 리뷰 블로커가 없고 CI 전체가 green이면 main에 머지한다.
- 다른 작업 흐름이 소유한 #161(실행 접수 대기열)과 #274(지표 공식) 브랜치는 건드리지 않는다. 그 위에
  서는 PR은 머지를 기다린다.
- 화면 PR(V5)은 각 화면의 backend PR 뒤에 시작한다. IDE에 붙는 V5-07만 lang2 P4-04·P5-02 뒤다.
- `PLAN.md`가 진행 상태의 단일 기준이다.

## 진행 상태 갱신 규칙

[YAML Strategy Workbench WORKFLOW 13.7절](../strategy-workbench-yaml-ui/WORKFLOW.md)의 tracker 규칙을 따른다
(IN_PROGRESS → SELF_CHECK → IN_REVIEW → APPROVED → MERGED). Phase 종료마다 별도 서브에이전트로
SoT(`.claude/rules/strategy-workbench-sot.md`)와 책임 분리(`backend-package-boundary.md`, `frontend-fsd.md`)를
점검하고 결과를 PLAN의 Phase exit에 기록한다.
