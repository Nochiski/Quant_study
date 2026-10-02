# 고급 노드 캔버스와 ELK 자동 배치 ADR

- 상태: P6-01 결정. 제품 적용과 접근성 완료 판정은 P6-02·03 검증 후 갱신한다.
- 선행: PR433 `b009e394a406087e04ae3e86970e6bc769b876df`, 전체 CI36957762428 성공.
- 계약: [WORKFLOW §9](../../planning/strategy-language-2-0/WORKFLOW.md#9-phase-6--그래프-3수준-고급-노드-캔버스).

## 결정

사용자가 선택한 **ELK(elkjs 0.12.0)**를 자동 배치 엔진으로 쓴다. 캔버스는 기존
`shared/ui` 버튼·폼으로 만든 HTML 노드와 SVG 연결선을 사용한다. 별도 React Flow 의존성을
추가하지 않는다. ELK는 렌더러가 아니며, 노드·포트의 좌표만 계산한다. 자체 SVG 선택은 배치
알고리즘을 새로 작성한다는 뜻이 아니다.

## 비교

| 후보 | 번들 | 키보드·스크린리더 | 좌표·편집 책임 | 라이선스 |
|---|---|---|---|---|
| React Flow + Dagre | 설치·실측하지 않음. 사용자 ELK 선택과 맞지 않아 채택하지 않음 | React Flow는 노드·선 포커스와 키보드 기능을 제공. 업무별 포트 연결·진단·undo는 별도 연결 필요 | controlled 좌표 사용 가능하나 문서 편집을 기존 source 연산으로 연결해야 함 | 두 프로젝트 MIT |
| React Flow + ELK | ELK 아래 실측에 렌더러가 추가됨. 추가분은 미측정 | 제공 기능을 활용할 수 있으나 기존 폼/진단/배선 흐름 연결은 필요 | ELK 좌표와 source transaction을 분리할 수 있음 | React Flow MIT + ELK 아래 표기 |
| **기존 HTML + SVG, ELK** | **API gzip 1,771B, worker gzip 466,809B**. 스파이크 진입 1,263B·URL 모듈84B 별도 | 실제 버튼과 폼을 재사용. 방향키·포트 선택/확정·취소·진단 이동은 P6-02/03에서 구현·검증 | **좌표만 local state**, 기존 편집 owner 직접 재사용 | ELK `EPL-2.0 OR GPL-3.0-or-later` |
| 자체 SVG + 자체 배치 | 새 배치 구현 비용·크기 미측정 | 위와 같은 키보드 구현 필요 | 불필요한 배치 알고리즘 소유권 추가 | 새 외부 의존성 없음 |

선택 근거는 기존 버튼·폼과 단일 source transaction의 책임을 유지하면서 승인된 ELK만 도입하는
것이다. React Flow 대비 더 작거나 더 접근성이 좋다는 실측 주장은 하지 않는다. 키보드 배선과
스크린리더 이름·관계 설명을 직접 책임져야 하는 비용을 수용한다.

공식 근거: [ELK API/worker](https://github.com/kieler/elkjs),
[ELK 라이선스](https://github.com/kieler/elkjs/blob/master/LICENSE.md),
[React Flow 접근성](https://reactflow.dev/learn/advanced-use/accessibility),
[React Flow MIT](https://github.com/xyflow/xyflow/blob/main/LICENSE),
[Dagre MIT](https://github.com/dagrejs/dagre/blob/master/LICENSE).
설치 버전·무결성·라이선스 표기의 정본은 `frontend/package-lock.json`이다. 배포 자산에는
설치 패키지의 라이선스 고지를 보존한다. 제3자 라이선스를 변경하지 않는다.

## 책임과 생명주기

- 원문: parse tree가 편집 대상이다. `nodeSlotsByKind`의 runtime schema가 포트 정본이며
  compile plan이 없는 빈/오류 그래프도 편집한다. backend plan은 순서·타입·단위·history·hash와
  승격 표식만 투영한다. 실행 규칙이나 타입 검증을 ELK/프론트에 복제하지 않는다.
- 편집: 배선은 `setNodeField`, 추가는 `addNode`, 삭제는 `removeNodeAt`의 참조 가드,
  rename은 `renameNode`, 복제는 새 ID와 `insertItem`이다. 모두 기존 source transaction과
  CodeMirror undo를 사용한다. `nodes`를 별도 캔버스 상태로 저장하거나 serializer를 만들지 않는다.
- 좌표: 팩터 범위의 정상 고유 ID로 local state를 유지한다. malformed/중복 ID의 편집 대상은
  pointer이며 구조 변경 시 임시 좌표 신원을 버린다. 좌표를 YAML·spec·cache에 쓰지 않는다.
- 실행: 비어 있지 않은 고급 그래프가 활성화됐을 때만 API와 worker URL을 동적 import한다.
  숨겨진 Graph projection도 마운트되는 기존 IDE를 고려하여 `active`를 명시적으로 전달한다.
  실제 Web Worker를 사용하고 owner 전환·취소·unmount에서 종료한다. Node bundled의 가짜
  worker는 제품 경로로 사용하지 않는다.
- 늦은 응답: 문서 epoch/source version/팩터/parse tree와 배치 세대를 확인한다. 수동 이동은
  이전 자동 배치 응답을 무효화한다. 실패하면 임시 좌표에서 계속 편집하고 재배치 안내를 제공한다.

## 완료 조건과 비용

P6-01은 [스파이크](../spikes/2026-10-02-elk-layout.md)의 알고리즘 시간·번들 분리만 증명한다.
현재 제품은 아직 ELK를 import하지 않는다. 기존 편집기와 DAG 스트립 교체는 P6-02·03 범위다.

P6-02에서는 방향키 포커스, Enter 인스펙터, Delete 가드, 출력→입력 키보드 연결, Escape 취소를
포인터 배선과 같은 commit 경로에 연결한다. 입력/IME에는 캔버스 단축키를 적용하지 않는다.
식별자는 접힌 영역에 두고, 노드·포트·연결 대상에는 한글 이름과 문서 순번을 제공한다.
SVG는 장식이며 연결 관계는 버튼의 접근성 설명에도 제공한다. 실제 스크린리더 프로그램의
음성 출력 검증과 접근성 트리/키보드 E2E 검증은 구분해 보고한다.

브라우저에서 최초/반복 배치, 취소, 문서 전환 중 stale 응답, 수동 이동, 배선→YAML→한 번 undo,
진단 재선택 reveal과 1440·640·360px를 검증한다. worker 전송량은 약456KiB gzip으로 작지
않으므로 기본 YAML·레시피에서는 로드하지 않는다. 실제 브라우저 시간·전체 번들 증가량은
P6-02에 기록하며 Node 수치를 브라우저 지연 보장으로 사용하지 않는다.
