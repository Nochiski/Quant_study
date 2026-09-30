# 클라우드 UI 검증 인계 (2026-09-30)

## 확인한 범위

기본 PIT mock으로 FastAPI health와 Vite HTML 응답을 확인했다. 실데이터·키를 사용하지 않았다.
현재 클라우드 도구에는 외부 브라우저에 전달할 지원 preview/포트 전달 URL이 없다.
`127.0.0.1:18000`, `127.0.0.1:15173`은 이 실행 환경 안에서만 확인한 주소이며 외부 URL이 아니다.
실제 화면 렌더·시각 검증과 pinned Playwright 자동 E2E는 미실행이다. 단위 테스트는 이를 대체하지 않는다.

## 차단과 필요한 환경 조치

공식 `npx playwright install chromium`이 아래 pinned Chromium 153 다운로드에서
`403 Domain forbidden`을 받았다.

```text
https://cdn.playwright.dev/builds/cft/153.0.8010.12/linux64/chrome-linux64.zip
```

이 요청의 허용이 필요한 도메인은 `cdn.playwright.dev`다. 리다이렉트 뒤 다른 도메인의
필요 여부는 확인하지 못했다. 허용 정책 변경은 수행하지 않았고, 다른 다운로드 도메인·시스템
브라우저 대체·공개 터널·Library 소스 업로드로 우회하지 않았다.

사용자는 다음 중 지원되는 환경 조치를 선택할 수 있다: 이 공식 다운로드를 허용하는 검증 환경,
또는 동일 private 저장소 브랜치를 승인된 GitHub 접근으로 체크아웃할 수 있는 별도 UI 실행 환경.
별도 환경의 수동 브라우저 확인도 pinned 자동 E2E 통과와 별도로 기록해야 한다.

## 재현 조건과 공식 명령

Python >=3.11, Node >=22.18, uv가 필요하다. 해당 PR head를 체크아웃하고 루트 README를 따른다.
`npm ci --prefix frontend`, `uv run server`, `npm run dev`가 기본 mock 실행 경로다.
Rust는 시작에 필수가 아니며 Python 참조 실행을 사용할 수 있다.
개발자 기본 `backend/.local`을 재사용하지 않는다. DB·research DB·assistant DB·assistant secrets 경로는
별도 임시 디렉터리로 설정하고 artifact 기본 저장소도 격리한다. 이 클라우드에서는 별도 worktree의
`backend/.local`을 임시 런타임 디렉터리로 연결했다. `E2E_REAL_EQUITY_ROOT`는 설정하지 않는다.

자동 게이트는 `frontend/e2e/README.md`가 정본이다. 설치가 허용된 환경에서 frontend 디렉터리에서:

```sh
npx playwright install chromium
PW_BACKEND_PORT=18000 PW_PREVIEW_PORT=15173 npm run test:e2e
```

해당 포트를 쓰는 수동 서버를 먼저 종료한다. 저장소 runner가 임시 DB·서버·기계 잠금을 소유하므로
`playwright test`를 직접 실행하지 않는다. Windows Chromium의 1440×900 / 1920×1080,
light/dark 기준선과 좁은 폭 동작을 확인한다. Linux 수동 시각 확인으로 Windows 기준선을 갱신하지 않는다.

## 구현 검토 경계

- 편집: runtime schema/catalog → 기존 navigator/form projection → 파이프라인/공유 컨트롤 →
  source-operation planner → CodeMirror 트랜잭션 한 번 → parse/compile → 진단 경로를 유지한다.
  Form/JSON 탭과 feature public 진입점을 제거했고, 공유 컨트롤의 내부 검증용 Form wrapper는 모듈에 남아 있다.
- 미리보기: 현재 compile/plan/실행 설정 → 기존 trace 요청 owner/cache → backend summary 및
  securities 디렉터리 → 생성 SDK → 표시만 수행한다. frontend의 선정·합산·이름 추론은 없다.
- 라우트: 표현은 graph/yaml, 비교 펼침은 compare 검색 상태가 소유한다. 저장된 JSON은 재직렬화 없이
  같은 바이트를 YAML 1.2로 읽는다. 문제 이동 보류는 documentEpoch/sourceVersion에 묶어 다른 문서에 적용하지 않는다.
- 재입력: 카드 편집 → undo → 같은 값 재입력에서 이전 제출 기록이 새 입력을 막던 회귀를 검출하고
  새 입력 시 기록을 초기화한다. Enter 후 blur의 동일 이벤트 중복 방지는 유지한다.

## 연결 코드 SoT·책임 분리 검토 결과

P4-04 draft 전 변경 파일뿐 아니라 schema navigator·form projection·source-operation planner,
문서 reducer·parse/compile, trace request/cache와 backend summary, 라우트 문서 identity 경로를 함께 읽었다.
표현 유니온은 `strategy-views.ts`에서 가져오며 widget에서 복제하지 않는다. 공유 컨트롤은 기존 planner를
거쳐 CodeMirror 트랜잭션 하나를 만들고, 문서 원문 이외의 편집 모델이나 새 직렬화 경로를 만들지 않는다.
선정·점수·이름·진단·hash 의미는 backend, 응답은 query cache, 펼침·표현은 route/UI가 소유한다.
API DTO/OpenAPI 변경이 없어 SDK 재생성은 필요하지 않았다. 검토한 경로에서 새 SoT 중복이나
역방향 레이어 의존은 발견하지 않았다. 이 결과는 실제 렌더·키보드·브라우저 경합 검증을 대신하지 않는다.

## 남은 검증

P4-04의 실제 빈 문서 → 팩터 → 기존 편집기 세 노드 → 명시적 미리보기 → 백테스트 흐름과
파이프라인 식별자 노출, 360/640px 탭 클릭은 pinned browser에서 실행해야 한다.
기존 Windows 스크린샷은 이전 탭 구성이라 새 렌더를 검토한 뒤 해당 환경에서만 갱신해야 한다.
P5/P6와 보고된 다른 웹 이슈 전체를 해결했다는 의미가 아니다.

## 현재 클라우드 검증 기록

P4-04 frontend 전체 Vitest는 93개 파일 / 1,216개 테스트 통과(2026-09-30 UTC)했다.
첫 parse 회귀 테스트는 로딩 문구 관찰 후 별도 await 틈에서 parse가 완료되는 경쟁을 제거하고,
로딩 문구·컨트롤 부재·고급 편집기 부재를 같은 DOM 시점에서 단언한다. 제품의 150ms 설정은 바꾸지 않았다.
frontend 타입·ESLint·production build·E2E 타입 검사가 통과했고 editor chunk는 gzip 132.07KiB로
200KiB 예산 안이다. 루트 story harness는 40개 story / 35개 tagged E2E를 확인했고
conflict-marker·diff 검사는 통과했다. story 목록 검사는 브라우저 실행이 아니다.
P4-03c의 관련 frontend 69개 및 backend trace 통합 75개도 이 환경에서 통과했다.
이후 P4-04에는 backend/API 변경이 없어 backend 전체 suite를 다시 실행하지 않았다.
