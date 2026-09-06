# P6-06 YAML-first migration E2E 보고서

- 최종 검증: 2026-09-06 19:08 KST
- 대상 PR: [#79](https://github.com/Nochiski/Quant_study/pull/79)
- 대상 branch: `feat/p6-06-workflow-migration`
- 기준 base: `dd9a6ff` (P6-05 approval-doc HEAD)
- 환경: Windows 10 Home, Python 3.11.0, uv 0.11.16, Rust 1.93.0,
  Node 22.13.0, npm 10.9.2, Vite 8.2.2, Playwright 1.63.0
- CI 기준 환경: `windows-2025`, Node 22.18.0, Chromium exact Playwright version

## 결론

YAML-first 전환의 핵심 업무 흐름을 실제 FastAPI·격리 SQLite·production Vite preview·Chromium·
Rust backtest extension 조합으로 끝까지 검증했다. 신규 전략 작성, 오류 유도와 수정, local/server
draft 복구, revision 저장과 충돌 복구, immutable history/diff, 실제 factor/PIT trace, 비기본 실행
설정을 사용한 Rust 백테스트, 취소와 동일 요청 재실행까지 하나의 사용자 흐름으로 연결된다.

Quick/Advanced 편집기는 더 이상 route에서 mount되지 않는다. 기존 bookmark와 source-less legacy
revision은 YAML route로 이동하거나 서버에서 verbose YAML로 materialize되며 canonical spec 의미와
hash를 보존한다.

로컬 구현·정적 검사·전체 테스트는 통과했다. 최종 병합은 GitHub Actions billing/spending-limit
차단이 해제된 뒤 동일 commit의 Windows CI가 green일 때만 수행한다.

## 실제 브라우저 시나리오

`frontend/e2e/workbench.workflow.spec.ts`는 backend 소유 `quality_momentum.yaml` golden fixture와
generated SDK를 사용한다. 테스트 내부에 수동 wire DTO나 프론트 재검증 공식을 두지 않는다.

1. 과거 `/?step=portfolio&run=bt-old`와 `/legacy/builder?...` bookmark가 query를 제거하고
   `/research/strategies/new?draft=...`로 replace redirect되는지 확인한다. retired editor는 mount되지
   않는다.
2. 완전한 verbose YAML을 입력하고 실제 backend compile 성공, local autosave, server draft 저장을
   확인한다. reload 뒤 local recovery와 server recovery가 각각 source를 byte-for-byte 복원한다.
3. `/risk/max_name_weight`를 의도적으로 오타 내 exact JSON Pointer 진단과 Save/Backtest 차단을
   확인한다. 실행 설정의 시작 현금도 `0`으로 만들어 사용자 오류와 실행 차단을 검증한 뒤 수정한다.
4. v1~v4 revision을 저장하고 두 browser context가 만든 stale save가 정확히 HTTP 409인지 확인한다.
   충돌 전후 editor source가 byte-for-byte 보존되고, 명시적 해소 뒤 저장된 v4 source도 동일하다.
5. 저장된 v4를 대상으로 실제 trace를 두 번 실행한다. 응답 provenance의 spec/source version,
   dataset snapshot `mock-equity-v0.2-20260903`, registry `factor-registry-v1`, plan hash를 검증한다.
6. `2026-07-31` 기준 raw PIT observation 날짜가 as-of 이후가 아닌지 확인하고, 실제 fixture 값
   `sec-000660-1=212570`, `sec-005930-1=116285`, `mom_252=0.02667014412117008`을 wire와 UI에서
   함께 확인한다.
7. 후보 rank·선택 여부·제한 전/후 비중과 조정 사유, factor contribution, TargetTape, raw data,
   selected node, execution plan과 재현 fingerprint를 5개 debugger tab에서 확인한다. factor explain의
   plan hash도 trace plan과 일치해야 한다.
8. Python을 선택했다가 Rust로 되돌리고 시작 현금 `123456789`, benchmark `sec-005930-1`,
   annualization `260`, OOS 시작일 `2025-01-02`를 설정해 백테스트한다. 서버가 수락한
   `BacktestRunSpec`과 manifest의 core·strategy revision/hash·snapshot·모든 실행 옵션을 정확히
   비교하고 결과가 `completed`인지 확인한다.
9. nonterminal run을 deterministic browser fixture로 만들고 Cancel이 `cancelled` 상태로 전이하는지,
   Rerun이 서버 소유 accepted request를 byte-for-byte 재사용해 새 run을 만드는지 확인한다.
   accepted-request endpoint 자체의 저장·조회·replay는 backend integration test가 실제 HTTP로 검증한다.
10. Backtest history에서 같은 run ID와 `strategy_id · v4` provenance를 확인한다. Strategy history의
    v1~v4 각 Diff 링크가 immutable `base`/`target` URL인지 확인하고 실제로 이동해 selector를 검증한다.
11. legacy JSON endpoint로 source-less revision을 만든다. document API가 `origin=legacy_json`,
    `generated=true` YAML을 제공하고 whitespace-only v2 저장 후 `origin=document`, `generated=false`가
    되며 canonical `spec_hash`가 유지되는지 확인한다.

## 비동기 소유권 회귀

Debugger trace 응답은 document route identity·generation owner에 귀속된다. 이전 요청이 늦게
resolve/reject되거나 컴포넌트가 unmount되어도 현재 URL, 선택된 security/node, trace 결과를 덮어쓸 수
없다. AbortSignal 전달과 resolve/reject/abort 세 경로를 단위 테스트로 고정했다.

## 시각·레이아웃 검증

아래 8개 committed screenshot을 직접 확인한 뒤 update option 없이 strict run을 다시 통과시켰다.

| Viewport  | Theme     | Workbench | 실제 debugger trace |
| --------- | --------- | --------- | ------------------- |
| 1440×900  | light     | PASS      | PASS                |
| 1440×900  | soft dark | PASS      | PASS                |
| 1920×1080 | light     | PASS      | PASS                |
| 1920×1080 | soft dark | PASS      | PASS                |

각 테스트는 editor·contract·debugger panel과 form/tablist/tabpanel의 bounding box가 viewport를 넘지
않는지 함께 검사한다. 1920px에서는 전체 pipeline을 한 화면에서 비교할 수 있고, 1440px 중앙 pane은
내부 가로 탐색으로 세부 단계를 보존한다.

## 최종 검증 결과

| Gate                                          | 결과                                                     |
| --------------------------------------------- | -------------------------------------------------------- |
| Playwright strict 전체                        | 16 passed, 4 workflow + 12 viewport/theme infrastructure |
| Frontend Vitest                               | 37 files, 444 passed                                     |
| Frontend typecheck / E2E typecheck / ESLint   | PASS                                                     |
| Frontend production build                     | PASS; editor 131.94 KiB gzip                             |
| Backend pytest                                | 1,118 passed, dependency deprecation warning 2건         |
| Backend CI 범위 Ruff / Pyright                | PASS, type errors 0                                      |
| Root delegate·PLAN·실제 server smoke unittest | 6 passed                                                 |
| Root tools Ruff / Pyright                     | PASS, type errors 0                                      |
| Rust fmt / clippy `-D warnings` / test        | PASS; 13 tests                                           |
| OpenAPI export + generated SDK                | 재생성 후 tracked diff 0                                 |
| Root `npm run dev`                            | `http://localhost:5173/` 200, React root 확인            |
| Root `uv run server`                          | health 200 `status=ok`, template 200                     |
| 종료 정리                                     | 5173/8000 listener 0, 검사용 SQLite 삭제                 |

전체 게이트를 CPU 집약 작업과 동시에 처음 실행했을 때 CodeMirror 16ms performance 1건과 긴 route
테스트 2건이 timing budget을 넘었다. 다른 작업을 모두 종료하고 각각 재실행해 1/1·57/57을 통과했고,
프론트 전체를 단독으로 다시 실행해 444/444 green을 확정했다.

`ruff check .`은 CI/README 범위 밖의 기존 `backend/ops/rebuild_share.py` 9건을 보고한다. 이 PR이
변경하지 않은 운영 스크립트이며 정식 backend gate인 `ruff check src tests examples scripts`와 root
`ruff check tools`는 모두 통과했다.

## SoT와 책임 경계

- 실행 가능 여부와 canonical hash는 backend `StrategySpec` compile 결과만 신뢰한다.
- YAML source, canonical spec, saved revision, accepted run request를 서로 다른 provenance로 보존한다.
- run 설정 UI는 generated `BacktestRunSpec` 타입에서 파생하고 서버가 보관한 accepted request를
  cancel/rerun의 정본으로 사용한다.
- Playwright는 generated SDK와 사용자 접근성 selector를 사용한다. REST wire shape 또는 CSS 구현
  세부를 테스트 안에 복제하지 않는다.
- route adapter는 과거 URL 해석과 YAML route 이동만 담당한다. legacy JSON materialization은 backend
  document adapter가 담당한다.
- E2E runner만 격리 SQLite와 서버·preview process 수명주기를 소유하고 종료 때 정리한다.

## 남은 merge gate

- GitHub Actions가 repository billing/spending-limit 때문에 job step 실행 전에 차단되어 있다.
- 차단 해제 후 stacked PR을 순서대로 latest main에 올리고 전체 backend/frontend/browser CI가 green인
  경우에만 병합한다.
- Chromium/Windows 외 브라우저는 이번 committed visual matrix 범위가 아니다.
