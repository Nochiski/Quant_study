# LANG2 마감 구현·수용 검증 계획

기준: main `4289429b075cac6abf42c3fa825af637760df2e8` (2026-10-13). 작업 브랜치
`fix/lang2-acceptance-closeout`. 이 문서는 합병된 기능의 수용 검증과 후속 결함을 추적한다.
PLAN의 머지 진척도와 수용 검증 완료율을 구분한다. 운영 `track/cutover`, PR #438, 운영 DB·배포·발송,
validation-lab 신규 기능, M8 확장은 범위 밖이다. 이슈 상태·외부 댓글도 변경하지 않는다.

## 구현 순서와 책임

1. 기존 명세·이슈·WIP와 실제 main을 비교하고 실패 증거부터 확보한다.
2. 독립적인 SQLite 조회 잠금과 필드 설명 계약을 각각 수정한다.
3. 문서 identity/parse lifecycle과 편집기 준비·reveal 경계를 고친다. #324의 역사적 부하 실패와
   별도로 재현한 결함은 구분한다.
4. 실제 HTTP→trace UI로 CS-05/06 수용 기준을 채우고 접근성·좁은 화면을 검증한다.
5. 최종 코드의 독립 SoT/책임 분리 리뷰, 전체 게이트, 공식 생성기로 완료 기록을 정리한다.
6. 논리적 변경 단위의 draft PR 스택으로 normal commit/push한다. 실제 의존 base와 exact HEAD
   CI 결과를 각 PR에 기록한다. 병합·배포·강제 push는 하지 않는다.

## Acceptance matrix

| ID | 구현·검증 기준 | 판정 근거/상태 |
|---|---|---|
| CS-05 | 다른 단위·다른 가중치의 두 팩터; none/rank/zscore 선택; 독립 계산 oracle과 trace 기여도·합 비교; 새 문서 rank, 1.1 업그레이드 none; none 단위 경고, 그래프 내부 정규화에는 경고 없음 | 실제 100종목 HTTP fixture를 재사용한 story E2E 작성 중 |
| CS-06 | top_percent=0.2 후보100→eligibility20/rank_cut80와 거절 UI 사유; risk 전후 alpha 기여도·분모·점수·순위 동일; raw 위험 역수 비중(동일 cap 회피); no-alpha compile 오류 | story E2E 작성 중 |
| #328 | 별도 repository 인스턴스·동일 인스턴스의 저장/조회 동시성; source 검증과 읽기 snapshot 경계; 기존 competing create/append·동시 init·손상·foreign-file·rollback 보존 | 결정적 재현 및 수정 후 회귀 검증 중; 역사적 Windows 원인 단정 금지 |
| #324 | 이전 WIP 비교; 문서 응답/lazy editor/첫 parser update 제어; 준비 전 reveal·같은 pointer 반복·pending 중 이동; exact 선택 텍스트·focus·unhandled MSW·cleanup; 원래 suite 부하 반복 | #240/#323과 별개; editor-ready 수정만으로 완료 판정 금지 |
| #419 | A→B→C, 같은 strategy 다른 revision의 render/commit 시퀀스에서 새 제목+옛 값 혼합 없음; back/forward·빠른 전환·저장 URL 반영 시 편집/undo 보존 | route 회귀 작성 중 |
| #420 | 첫 invalid에서 loading 종료·구문 안내·가짜 기본값 없음; 원문 보존·덮어쓰기 차단; 수정 후 실제 0.05; 같은 문서 valid→invalid는 명시적 stale 유지 | route 회귀 작성 중 |
| #373 | 일반 field 설명 축약; 개별 의미·한계는 backend 계약 한 곳; declaration↔field_map 역방향 coverage와 정확한 unsupported reason; inspector/autocomplete/AI 소비 확인 | 기존 WIP 필요한 부분만 대조 적용; #421 배선 제외 |
| A11Y | node/port/connection의 이름·상태·live 안내, inspector 진입/복귀, 반복 오류 reveal, 연결 취소; 좁은 화면·시각 캡처 | 자동 검증과 실제 음성 검증 분리. 실제 스크린리더/음성 장치 없는 환경은 미검증으로 명시 |
| 기록 | tracker 머지 SHA→공식 PowerShell 생성/-Check; Packet/Phase exit/ADR·CS01/03·BACKLOG005/006/016/007; #374/#366 현재 적용 범위 매핑 | 머지100%를 수용100%로 표기하지 않음 |

## 테스트 계획

- 수정 전 결정적 red와 수정 후 green을 보존한다. timeout 증가·재시도·assertion 약화로 통과시키지 않는다.
- SQLite 경쟁은 event/barrier로 연결 간 순서를 고정하고 반복한다. 실제 사용자 DB를 열지 않는다.
- frontend 단위·route 검사는 MSW wire 경계와 React commit 관측을 사용한다. parser·projection이
  모르는 값을 UI에서 합성하지 않고, 이전 유효 문서와 다른 identity를 구분한다.
- E2E는 실제 격리 backend와 existing 100종목 fixture를 쓴다. 계산 결과를 mock하지 않는다.
  전체 release gate에 story 태그가 수집되는지 검사한다. 저장·전환·반복·취소 및 좁은 화면을 포함한다.
- 최종 backend lint/type/unit/contract/integration, frontend lint/type/unit/build, 전체 browser E2E,
  root/story/generated 계약 및 exact HEAD CI 전체를 확인한다.
- 리뷰는 변경 owner가 아닌 독립 검토자가 SoT/책임 분리·회귀 근거를 확인한다. 현재 환경에
  WORKFLOW가 언급한 superpowers skill/Opus reviewer가 없으므로 사용 가능한 독립 검토로 수행하고
  특정 도구·모델의 검토를 했다고 주장하지 않는다.

## 환경과 검증 기록

- 기존 frontend 의존성이 lockfile과 달라 이 worktree에서 `npm ci`로 재구성한다.
- backend 공유 venv의 editable 경로와 혼동하지 않도록 `PYTHONPATH`를 작업 트리의 `backend/src`로 지정한다.
- PowerShell 실행 파일 부재를 확인했다. 생성 집계는 손으로 바꾸지 않으며 공식 생성기 실행 방법을
  확보한 후 생성/-Check 증거를 추가한다.
- 실제 음성용 스크린리더·음성 서비스·오디오 장치가 없어 자동 접근성 통과를 실제 음성 완료로 세지 않는다.

최종 PR·테스트 수치·시각 캡처·독립 리뷰와 미검증 항목은 결과가 나온 뒤 이 문서에 연결한다.
