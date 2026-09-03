---
paths:
  - "**/*.py"
---

# Code Generation & Edits

- **기존 SoT / 헬퍼 먼저 찾기 (필수)**: 파일명, 경로 빌더, 네이밍 규칙, 파일 IO, 디렉토리 구조, 식별자 포맷, 지표 계산식 같은 cross-cutting concern 은 새로 작성하기 전에 **반드시 grep 으로 기존 헬퍼/SoT 를 먼저 탐색**한다. 있으면 그것을 import 해서 사용한다. 없는 경우에만 신규 작성하되, 여러 곳에서 쓰이면 공용 모듈에 둔다. 같은 리터럴/규칙(수수료율, 거래일 캘린더, 리샘플링 규칙 등)을 두 파일 이상에 산발하면 한쪽 변경 시 다른쪽이 stale 되는 silent breakage. 의심나면 검색 후 결과를 PR description 에 명시.
- 기존 패턴/구조/의존성 최우선
- **기능/cleanup 분리 원칙** (구 "최소 diff"): 코드를 깨끗하게 만드는 정리(리팩토링·리네이밍·죽은 코드 삭제)는 환영 — 단 **동작 변경과 같은 커밋에 섞지 않는다**
  - 기능 PR 내 소규모 정리 → 별도 `refactor`/`chore` 커밋으로 분리, 동작 불변을 테스트로 확인
  - 대규모 정리(파일 다수, legacy lint 부채 대상화) → 별도 PR
  - 포매터 전체 적용(`ruff format .`)은 금지 — 리뷰 가능한 diff를 파묻는다. 정리는 의도적·국소적으로
- 새 라이브러리 추가 금지 → 기존 의존성 대안 먼저 제시
- 아키텍처 패턴 제안 전: **근본 원인 해결 방법 우선** 제시
- 커밋 전 테스트 통과 필수
  - **기존 실패(baseline) 처리**: main에서 이미 실패하던 테스트는 게이트에서 제외. (1) 내 변경과 관련된 테스트는 통과 필수, (2) 기존 실패 여부는 **격리 재실행 우선**(해당 테스트 파일만 단독 실행 — 내 변경과 무관 영역이면 충분), main 직접 대조가 필요하면 작업 트리를 건드리는 stash/checkout 대신 `git worktree add`로 별도 트리 생성 후 확인(worktree는 가상환경 미공유 — 의존성 설치 먼저), 확인 결과를 PR body에 명시, (3) **새로 깨진 테스트만 blocker**. 기존 실패를 고치겠다고 범위 밖 파일을 같은 커밋에서 수정하지 말 것 — 고치려면 별도 커밋/PR로 분리(기능/cleanup 분리 원칙)
- 커밋 전 변경된 `.py` 파일 lint / type check 통과 필수 (CI와 동일 범위 — PR diff 기준):
  ```bash
  files=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.py')
  [ -n "$files" ] && ruff check $files
  [ -n "$files" ] && pyright $files
  ```
- lint gate는 PR diff만 검사 (legacy 부채 grandfathered):
  - 신규 파일/변경된 파일은 ruff + pyright 모두 통과 필요
  - legacy 파일을 수정하면 그 파일이 lint 대상이 됨 — 동일 PR 내 같이 fix 또는 라인별 ignore
  - legacy cleanup만 하는 PR은 별도로 진행
- lint 룰 무시 시 reason 코멘트 의무:
  - ruff: `# noqa: F401  # reason: ...`
  - pyright: `# pyright: ignore[reportOptionalMemberAccess]  # reason: ...`
  - reason 없는 ignore 금지 — 다음 사람이 왜 껐는지 알아야 함
- `[tool.pyright]` / `[tool.ruff]` 설정 변경(룰 켜기/끄기, 모드 변경, exclude 추가)은 **사용자 확인 필수**
- 신규/수정 함수에는 타입 힌트 필수 (args + return)
- 프로덕션 코드에서 `assert` 금지 — 명시적 예외(`ValueError`/`RuntimeError`/`TypeError`) 사용. `tests/`는 pytest 표준이므로 예외
- 공용 API 함수/클래스에 docstring 권장 (Args/Returns/Raises)
- 산출물(백테스트 결과, 리포트 파일 등)의 존재/내용을 단언하는 테스트 작성 금지 — 상세: `.claude/rules/testing.md`

## 협업 가독성 룰 (Clean Code)

- **변수명에 단위/기준 포함**: 수익률·기간·금액은 단위 접미사(`ret_pct`, `window_days`, `fee_bps`, `cash_krw`), 시계열은 기준 시점·주기 명시(`close_daily`, `ts_utc`, `ret_1d`). 단위/시간대 혼동은 백테스트 결과를 조용히 왜곡한다
- **놀람 최소화 (POLA)**: 함수는 이름이 약속한 것만 한다 — 숨은 부수효과 금지 (예: `get_prices()`가 내부에서 캐시 파일까지 덮어씀)
- **명령-조회 분리 (CQS)**: 상태를 바꾸는 메서드와 값을 읽는 메서드를 분리. 조회는 몇 번을 불러도 결과가 같아야 한다
- **pass-through 래퍼 금지**: 단순 위임만 하는 함수/클래스는 신규 작성 금지, 발견 시 제거 대상
- **죽은 코드 금지**: 주석 처리된 코드는 삭제한다 (이력은 git이 보존). TODO는 이슈번호 필수 — `# TODO(#123): ...`, 번호 없는 TODO 금지

## Pyright 사용 룰 (정공법 우선)

pyright 단독 type checker 기준 (mypy 미사용). 아래 우선순위로 타입 정공법을 지킨다.

### 1. `# type: ignore` 금지, `# pyright: ignore[<rule>]` 만 허용

- `# type: ignore`는 PEP 484 표준이라 mypy/pyright 둘 다 무시 — **어떤 룰을 끄는지 명시 안 됨**
- 항상 `# pyright: ignore[<룰명>]` 형태로 명시
- `# pyright: ignore` (룰명 없음)도 금지 — 적용 범위가 너무 넓어짐
- 반드시 `# reason: ...` 코멘트 동반

### 2. `Any` 강력 지양 — 대안 우선

`Any`는 type checker를 완전 무력화 → 타입 안전성 잃음. 다음 순서로 대안 시도.

| 의도 | 대안 |
|---|---|
| "어떤 값이든 받지만 내부에선 거의 안 씀" | `object` (가장 안전, 거의 모든 연산 차단) |
| "입력 타입 = 출력 타입" 보존이 필요한 제네릭 함수 | `TypeVar` |
| duck-typing 인터페이스 (메서드 시그니처만 맞으면 OK) | `Protocol` |
| 외부 dict-like JSON 응답 (데이터 API 등) | `dict[str, object]` 또는 `TypedDict` |
| `*args`/`**kwargs` 패스스루 wrapper | `*args: Any, **kwargs: Any` 허용 (단, reason 코멘트) |

`Any`를 정말 써야 하는 경우 `# reason:` 코멘트로 정당화 필수.

### 3. 외부 untyped 라이브러리 처리 우선순위

stub 없는 lib(데이터 벤더 SDK, 브로커 API 클라이언트, 일부 지표 라이브러리 등)은 pyright가 `reportUnknownMemberType` / `reportAttributeAccessIssue` / `reportCallIssue` 등을 쏟아낸다. 다음 순으로 대응:

1. **PyPI stub 패키지 우선** — `types-*` / `*-stubs` 가 있으면 dev 의존성으로 추가 (단, 사용자 확인 후)
2. **로컬 stub 생성** — `pyright --createstub <module>` → `backend/typings/<module>/` 에 보관, 실 사용 부분만 수동 보정
3. **Typed wrapper helper 한 곳에 집중** — 호출처마다 ignore 흩뿌리지 말고 helper 함수/팩토리 1개에서만 처리
4. **Protocol 래핑** — lib 일부 인터페이스만 추상화하면 충분할 때
5. **최후의 수단**: 호출 사이트에 `# pyright: ignore[<rule>]  # reason: <lib명> stub 부재` — 그리고 후속 cleanup 백로그 추가

내부(우리 작성) 코드에서 발생한 pyright 에러는 ignore 금지 — 근본 fix 우선.

### 4. 점진적 strictness

- `basic` 모드에서 시작 — `strict` 승격은 별도 마일스톤
- 파일 단위 strict opt-in (`# pyright: strict` 파일 상단) 도 사용자 확인 필수
- 신규 모듈은 `Any` 0개를 목표로 작성 권장

## 관련 규칙

- 결과/실패 값 설계: `.claude/rules/python.md`
- 예외/로그 메시지 진단 디테일: `.claude/rules/error-messages.md`
- 테스트 범위: `.claude/rules/testing.md`
