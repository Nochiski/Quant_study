# P6-06 YAML-first migration E2E 보고서

- 실행 시각: 2026-09-06 07:04 KST
- 대상 branch: `feat/p6-06-workflow-migration`
- 기준 base: `dd9a6ff` (P6-05 approval-doc HEAD)
- 환경: Windows 10 Home, Python 3.11.0, uv 0.11.16, Rust 1.93.0,
  Node 22.13.0, npm 10.9.2, Vite 8.2.2, Playwright 1.63.0
- CI 기준 환경: `windows-2025`, Node 22.18.0, Chromium exact Playwright version

## 결론

P6-06의 YAML-first migration gate를 로컬 실제 FastAPI, SQLite, production Vite preview,
Chromium, Rust backtest extension 조합에서 통과했다. 신규 작성부터 오류 수정, 두 종류의 초안
복구, 리비전 저장·충돌·diff, 실제 factor trace/risk 결과, 백테스트 완료와 이력 조회까지 하나의
전문 사용자 흐름으로 검증했다. Quick/Advanced 화면은 제거해도 YAML route가 핵심 업무를 모두
수행하며, backend의 legacy JSON 및 `source=None` wire 호환은 보존된다.

최종 merge는 이 결과만으로 우회하지 않는다. GitHub Actions의 billing/spending-limit 차단이
해제된 뒤 동일 browser E2E와 전체 CI가 latest main에서 green이어야 한다.

## 실제 브라우저 시나리오

`frontend/e2e/workbench.workflow.spec.ts`는 backend가 소유한
`quality_momentum.yaml` golden fixture를 입력으로 사용한다.

1. `/?step=portfolio&run=bt-old`와 `/legacy/builder?...`가 query를 폐기하고 깨끗한
   `/research/strategies/new?draft=...`로 replace redirect되는지 확인했다. 구형 Quick Builder와
   기존 편집기 navigation은 mount되지 않는다.
2. 새 문서에 완전한 verbose YAML을 입력하고 backend compile 성공, local autosave와 server
   draft 저장을 기다렸다. reload 뒤 local recovery와 server recovery를 각각 적용해 source가
   byte-for-byte 복원되는지 확인했다.
3. `/risk/max_name_weight`를 `/risk/max_name_wieght`로 훼손해 structural diagnostic의 exact JSON
   Pointer를 확인했다. invalid source에서는 Save와 Backtest가 모두 비활성화됐다.
4. 오류를 수정하고 명시적 Validate가 실제 `/api/v1/strategy-documents/compile` 200 응답을
   받는지 확인했다. v1 저장·reload source 복원, v2 저장, `/title` semantic diff를 확인했다.
5. 같은 v2를 연 두 browser context 중 하나가 v3를 저장한 뒤 다른 context의 저장이 409를
   반환하는지 확인했다. 사용자 source를 잃지 않고 명시적 충돌 해결로 v4를 생성했다.
6. 두 security를 한 번에 제출해 실제 trace를 실행했다. linked trace의 제약 전/위험 제약 후
   목표, TargetTape 후보·제외 사유, raw `price.close`와 공개일/상태, `mom_252` node 계산,
   execution plan 및 재현 fingerprint를 확인했다.
7. 저장된 v4에서 Backtest를 시작했다. Python fallback 없이 설치된 Rust extension이 계산한 run이
   `completed`가 되고 결과 화면이 열리는지 확인했다. Backtest history에서 동일 run ID와
   `strategy_id · v4` provenance를 확인했다.
8. Strategy history에서 저장된 title과 v4를 찾고, revision 목록 v1~v4 및 각 immutable Diff
   link를 확인했다.
9. legacy JSON endpoint로 source-less 전략을 생성했다. document API가 `origin=legacy_json`,
   `generated=true` YAML을 제공하는지 확인하고 whitespace만 바꿔 v2로 저장했다. 저장 뒤
   `origin=document`, `generated=false`가 되면서 server canonical `spec_hash`는 동일했다.

## 시각·레이아웃 검증

다음 네 committed baseline을 새 UI로 갱신한 뒤 직접 확인했고, 이어서 update option 없이
pixel-exact strict run을 다시 통과했다.

| Viewport  | Theme     | 결과 |
| --------- | --------- | ---- |
| 1440×900  | light     | PASS |
| 1440×900  | soft dark | PASS |
| 1920×1080 | light     | PASS |
| 1920×1080 | soft dark | PASS |

특히 1440px 중앙 panel에서 debugger controls가 이웃 panel을 침범하지 않고, TargetTape 등
결과 tab이 클릭 가능하며, 좁은 container에서 controls가 3열/1열로 축소되는 것을 확인했다.

## 검증 결과

| Gate                                        | 결과                                                         |
| ------------------------------------------- | ------------------------------------------------------------ |
| `npm run test:e2e:update`                   | 11 passed, 36.0s                                             |
| `npm run test:e2e`                          | 11 passed, 33.4s; snapshot 변경 0                            |
| Frontend Vitest                             | 36 files, 437 passed                                         |
| Frontend typecheck / E2E typecheck / ESLint | PASS                                                         |
| Frontend production build                   | PASS; editor gzip 131.94 KiB                                 |
| Backend pytest                              | 1,118 passed, 2 dependency deprecation warnings              |
| Backend/root Ruff and Pyright               | PASS, type errors 0                                          |
| Root contract unittest                      | 6 passed                                                     |
| Rust fmt / clippy `-D warnings` / test      | PASS; 13 tests                                               |
| OpenAPI export + generated SDK              | tracked semantic diff 0                                      |
| Root `npm run dev`                          | `http://localhost:5173/` 200, React root present             |
| Root `uv run server`                        | `http://127.0.0.1:8000/api/v1/health` 200, `status=ok`       |
| 종료 정리                                   | ports 5173/8000 listener 0, isolated E2E runtime directory 0 |

## SoT와 책임 경계 확인

- 실행 의미와 canonical hash는 backend `StrategySpec` compile 결과만 신뢰한다. E2E도 frontend에
  수기 DTO나 validation formula를 추가하지 않고 backend golden YAML과 generated SDK 경계를
  사용한다.
- route adapter는 과거 URL의 YAML route 이관만 담당한다. backend legacy JSON 저장과
  `source=None` document materialization은 wire migration 책임으로 유지한다.
- Playwright config는 서버 조립과 isolated SQLite 경로만 소유하고, runner wrapper는 소유권을
  검증한 임시 runtime 생성·종료 정리만 담당한다.
- 루트 개발 명령은 실행 위치만 위임한다. Vite 설정은 frontend가, ASGI target·host·port·reload는
  backend bootstrap이 계속 단독 소유한다.
- Rust extension이 없으면 default Backtest는 `CoreUnavailable`로 명시적으로 실패한다. 의미가
  다른 Python fallback으로 결과를 가장하지 않으며 README가 설치 절차를 안내한다.

## 잔여 위험과 merge gate

- 로컬 Node는 22.13.0이고 CI는 22.18.0으로 고정돼 있다. 브라우저 기준선의 최종 권위는
  `windows-2025` CI 결과다.
- Safari/WebKit은 이번 committed visual matrix 범위가 아니다.
- GitHub Actions가 repository billing/spending-limit 때문에 job step 실행 전에 차단돼 있다.
  차단 해제 후 stacked PR을 순서대로 latest main에 올리고 전체 backend/frontend/browser E2E가
  green인 경우에만 merge한다.
