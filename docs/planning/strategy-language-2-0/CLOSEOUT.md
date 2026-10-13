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
| CS-05 | 다른 단위·다른 가중치의 두 팩터; none/rank/zscore 선택; 독립 계산 oracle과 trace 기여도·합 비교; 새 문서 rank, 1.1 업그레이드 none; none 단위 경고, 그래프 내부 정규화에는 경고 없음 | 기본 3종목 fixture를 쓰는 실제 HTTP 신규 story E2E. 독립 계산 oracle 리뷰 완료; 최종 HEAD 전체 CI에서 재검증 |
| CS-06 | top_percent=0.2 후보100→eligibility20/rank_cut80와 거절 UI 사유; risk 전후 alpha 기여도·분모·점수·순위 동일; raw 위험 역수 비중(동일 cap 회피); no-alpha compile 오류 | 신규 story E2E로 HTTP 20/80 및 대표 탈락 종목 UI 사유, alpha-only 대조 검증 |
| #328 | 별도 repository 인스턴스·동일 인스턴스의 저장/조회 동시성; source 검증과 읽기 snapshot 경계; 기존 competing create/append·동시 init·손상·foreign-file·rollback 보존 | 수정 전6실패→수정후100통과, 결정적 동시성120회통과; 독립79통과. 과거 Windows 실패 잠금 소유자는 미확정 |
| #324 | 이전 WIP 비교; 문서 응답/lazy editor/첫 parser update 제어; 준비 전 reveal·같은 pointer 반복·pending 중 이동; exact 선택 텍스트·focus·unhandled MSW·cleanup; 원래 suite 부하 반복 | 별도 handle 준비순서 red→green. Linux 병렬 focused30/30 및 전체 suite2회 통과; 과거 Windows 원부하 미검증 |
| #419 | A→B→C, 같은 strategy 다른 revision의 render/commit 시퀀스에서 새 제목+옛 값 혼합 없음; back/forward·빠른 전환·저장 URL 반영 시 편집/undo 보존 | 실제 Graph commit 관측 red→green. 전체 route92통과, 저장 URL/undo·다른revision·visible YAML 검증 |
| #420 | 첫 invalid에서 loading 종료·구문 안내·가짜 기본값 없음; 원문 보존·덮어쓰기 차단; 수정 후 실제 0.05; 같은 문서 valid→invalid는 명시적 stale 유지 | 첫 invalid 기본값 red→green. loading 종료·저장 차단·원문 보존·수정후0.05·같은 문서 STALE 검증 |
| #373 | 일반 field 설명 축약; 개별 의미·한계는 backend 계약 한 곳; declaration↔field_map 역방향 coverage와 정확한 unsupported reason; inspector/autocomplete/AI 소비 확인 | 기존 WIP 대조 반영, backend205/FE46/독립85통과. #421 배선 제외 |
| A11Y | node/port/connection의 이름·상태·live 안내, inspector 진입/복귀, 반복 오류 reveal, 연결 취소; 좁은 화면·시각 캡처 | unit25·브라우저2·1440/640/360 캡처 및 독립 리뷰 통과. 실제 음성은 미검증; [수동 절차](../../manual/lang2-accessibility-validation.md) |
| 기록 | tracker 머지 SHA→공식 PowerShell 생성/-Check; Packet/Phase exit/ADR·CS01/03·BACKLOG005/006/016/007; #374/#366 현재 적용 범위 매핑 | tracker 실제 머지SHA 갱신. 공식 CI PowerShell 생성/-Check 후 집계 적용. 머지100%와 수용 완료를 구분 |

## 테스트 계획

- 수정 전 결정적 red와 수정 후 green을 보존한다. timeout 증가·재시도·assertion 약화로 통과시키지 않는다.
- SQLite 경쟁은 event/barrier로 연결 간 순서를 고정하고 반복한다. 실제 사용자 DB를 열지 않는다.
- frontend 단위·route 검사는 MSW wire 경계와 React commit 관측을 사용한다. parser·projection이
  모르는 값을 UI에서 합성하지 않고, 이전 유효 문서와 다른 identity를 구분한다.
- E2E는 실제 격리 backend와 기존 fixture(CS-05 기본3종목, CS-06 100종목)를 쓴다. 계산 결과를 mock하지 않는다.
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


## 구현 결과와 PR 의존 관계

| PR | base | 책임 |
|---|---|---|
| [#439](https://github.com/Nochiski/Quant_study/pull/439) | main 4289429b | SQLite 읽기 snapshot 밖 원문 검증; timeout·WAL·재시도 무변경 |
| [#440](https://github.com/Nochiski/Quant_study/pull/440) | #439 | 필드 설명 계약과 정확한 unsupported 사유; 조회 배선 무변경 |
| [#441](https://github.com/Nochiski/Quant_study/pull/441) | #440 | 문서 identity/첫 parse/CodeMirror 준비 순서; source·undo 보존 |
| [#442](https://github.com/Nochiski/Quant_study/pull/442) | #441 | 연결 live status·인스펙터 복귀·자동 접근성 및 수동 음성 절차 |
| [#443](https://github.com/Nochiski/Quant_study/pull/443) | #442 | CS-05/06 E2E·기록 정합·공식 PLAN 집계 검사 |

### 독립 리뷰

변경 owner와 다른 검토자가 SQLite snapshot/source 검증, 필드 계약·미지원 배선 경계, 문서 reducer·
parse/compile epoch·저장 URL/undo, 접근성 상태의 기존 owner 재사용을 검토했다. 차단 사항 없음.
CS-05는 서버 응답 가중치를 기대값에 재사용하는 oracle 공백을 발견해 입력 고정 가중치0.25/0.75의
독립 계산과 별도 전달 단언으로 보강한 뒤 재검토했다. CS-06의80개 탈락 사유는 실제 응답에서 모두,
대표 탈락 종목의 한국어 사유는 UI에서 확인한다. 전체80행을 화면에서 각각 검증했다는 뜻이 아니다.

### 로컬 전체 게이트와 한계

- frontend 전체 default suite2회: 각각100파일·1,287개 통과. 기존 exact 선택 텍스트·focus·timeout 유지.
- frontend lint/type/build, backend Ruff/Pyright(오류0) 통과. 로컬 Rust 확장 source 경고는 공식 CI의
  Rust 빌드와 전체 suite로 검증한다.
- 실제 스크린리더 음성(Orca/NVDA/VoiceOver 등)은 이 환경에서 실행하지 않았다. 자동 검사는 이를 대체하지 않는다.
- #324 역사적 Windows CPU94~100% 환경은 재현하지 않았다. 확인된 handle 결함과 Linux 부하 결과를
  제공하며 원래 flake의 모든 원인 종결로 표기하지 않는다.
- 공급자 호출·운영 데이터·배포·발송·merge 없음. main과 별도 운영 PR #438은 보존한다.
- 공식 CI 최종 exact HEAD 링크와 숫자는 마감 PR 본문에 기록한다. 생성 집계는 GitHub의 기존 pwsh로
  공식 스크립트를 실행해 받은 결과만 적용하며, `lang2-plan` workflow가 매번 `-Check`로 드리프트를 막는다.

### P3~P6 현재 책임 경계 재감사 (2026-10-13)

독립 검토 범위에서 blocking0이다. P3의 원문·identity·epoch는 document reducer, 의미와 진단은
backend compile이 소유한다. P4는 backend schema를 Form/Pipeline으로 읽기 전용 투영한다.
P5의 recipe/graph는 SourceOperation을 만들고 공통 source transaction과 CodeMirror undo로
전달한다. P6 좌표는 UI 상태이며 배선도 같은 원문 transaction을 쓴다. 정규화·eligibility·risk는
backend compiler 계산이며 linked trace는 응답 ID를 결합한다. 이 감사는 실제 음성 및 역사적
Windows 부하 재현을 포함하지 않는다.
