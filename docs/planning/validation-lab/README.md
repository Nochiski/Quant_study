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
- [REAL-DATA-CHECKS.md](./REAL-DATA-CHECKS.md): 기능마다 우선하는 실데이터 확인 절차와 열람 기록
- [V5-04-candidate-map.md](./V5-04-candidate-map.md): 파라미터 칸 비교·이유 기록·선택 당시 통계와 결과 카드의 책임 경계
- [KIWOOM-INDEPENDENT.md](./KIWOOM-INDEPENDENT.md): 키움 없이 진행하는 패킷·부분 구현·전체 자동매매 후속 경계
- [공용 Python progress 도구](../../../tools/quant_study_dev/plan_progress.py): PLAN 집계 갱신(`uv run --no-sync python -m quant_study_dev.plan_progress --profile validation-lab`, 검사는 `--check`). 기존 PowerShell wrapper는 호환용
- 디자인보드: [전략 검증 랩 UI/UX 기획](https://claude.ai/artifact/5jYGBTPKcWFHhjB4fvEtup)
- 수학 노트: [검증 랩 수학 노트](https://claude.ai/artifact/CieccrnXuEpin4DWmvuXvu)

## 현재 인계

회귀 접수 상한 #546·V6 모의 core #547·선택 파라미터 배선 #548은 머지됐다. V5-02는 완료됐고
후보 지도 #549(V5-04)도 main `45328d0b`로 머지됐고 12개 검사가 통과했다. 비용/회귀 보고서 #550(V5-06, main `366dc241`)·
IDE 진입점 #551(V5-07, main `c2f46dfb`)도 각 12개 검사 통과 뒤 머지됐다. 검증 랩은 **26/28(93%)**로 V5를 완료했지만
전체 자동매매의 완료율은 아니다. V6는 사전등록·합성 모의 core 부분 구현으로 실제 개봉·UI가 남는다.
상태·완료 수는 PLAN과 실제 머지 기록으로 확인한다. 기능별 실자료 기술 QA와 2026-10-19 주간 예정인
S03 신호/S04 비용 후 수익성 연구를 구분하며, 어느 테스트 통과도 실거래 준비 완료로 해석하지 않는다.

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
