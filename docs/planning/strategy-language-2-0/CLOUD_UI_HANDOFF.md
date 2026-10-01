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
