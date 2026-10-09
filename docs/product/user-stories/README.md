# 유저 스토리 하네스

이 폴더는 Strategy Workbench를 누가, 왜, 어떻게 쓰는지를 적어 두는 곳이다. 여기 적힌 스토리가
앞으로의 개발 기준선이다. 새 기능은 어떤 스토리를 움직이는지 먼저 밝히고, 스토리가 약속한 화면
결과는 브라우저 e2e가 지킨다.

제품 방향은 네 가지로 정해져 있다(2026-09-20 결정). 스토리도 이 방향을 따른다.

- 전략 언어에는 전략만 담는다. 유니버스·기간·데이터·수수료·슬리피지 같은 실행 설정은 화면의
  실행 설정에서 정한다.
- AI로 사용성을 높인다.
- 템플릿이 이끄는 흐름은 만들지 않는다. 빈 문서에서 범용으로 조립하는 흐름을 목표로 한다.
- 전략의 표현은 YAML과 그래프 두 가지다.

## 구성

| 파일 | 내용 |
|---|---|
| [personas.md](./personas.md) | 페르소나 세 명: 정동민, 한상목, 김철수 |
| [stories/dm.md](./stories/dm.md) | 정동민의 스토리 (`US-DM-*`) |
| [stories/sm.md](./stories/sm.md) | 한상목의 스토리 (`US-SM-*`) |
| [stories/cs.md](./stories/cs.md) | 김철수의 스토리 (`US-CS-*`) |
| [traceability.md](./traceability.md) | 스토리 → e2e → 상태 → 담당 PR 색인 |

## 스토리 ID

- 형식은 `US-<페르소나>-<두 자리 번호>`다. 페르소나 약자는 `DM`(정동민), `SM`(한상목),
  `CS`(김철수)다.
- 스토리는 그 페르소나의 파일에만 둔다. `US-DM-*`는 `stories/dm.md`에만 있다.
- 번호는 다시 쓰지 않는다. 기능이 바뀌면 스토리를 지우지 말고 수용 기준을 고쳐 쓴다. 정말 필요
  없어진 스토리를 지울 때는 아래 "은퇴한 ID"에 번호와 사유를 남긴다.
- 새 페르소나를 들일 때는 `personas.md`에 먼저 적고, `stories/<약자 소문자>.md`를 만든다.
  검사 도구는 파일 이름에서 약자를 읽는다.

### 은퇴한 ID

아직 없다.

## 스토리 형식

스토리 하나는 아래 모양이다. 검사 도구가 이 모양을 읽으므로 항목 이름과 순서를 바꾸지 않는다.

```markdown
### US-DM-01 한 줄 제목

> 정동민으로서 …하고 싶다. 그래야 …할 수 있다.

- 상태: `구현됨-e2e`
- 담당 PR: 없음
- e2e 담당: 없음
- 기능 영역: AI 어시스턴트 · 설정
- e2e:
  - `frontend/e2e/assistant.workflow.spec.ts` :: 테스트 제목 그대로

수용 기준

- Given …, When …, Then … (화면에서 보이는 결과로 쓴다)
```

- `담당 PR`에는 이 스토리를 움직이는 PR ID를 쉼표로 적는다. lang2 WORKFLOW의 `P3-02` 같은 ID나
  GitHub 번호 `#123`을 쓴다. 없으면 `없음`이다.
- `e2e 담당`에는 그중 이 스토리의 e2e를 쓸 PR을 적는다. `예정` 스토리는 반드시 있어야 하고 `담당 PR`
  목록 안의 ID여야 한다. `구현됨-e2e`와 `미계획`은 `없음`이다.
- `e2e`가 없으면 한 줄로 `- e2e: 없음`이라고 적는다.
- 수용 기준은 사용자가 화면에서 확인할 수 있는 결과로 쓴다. 내부 상태나 API 필드 이름으로 쓰지
  않는다.

## 상태 값

| 상태 | 뜻 | 반드시 있어야 하는 것 |
|---|---|---|
| `구현됨-e2e` | main에 있고 릴리스 게이트 e2e가 지킨다 | 스토리 태그가 붙은 e2e 1개 이상, 담당 PR `없음` |
| `구현됨-e2e없음` | main에 있지만 아직 e2e가 없다 | 태그 붙은 e2e가 없어야 한다. 빨리 e2e를 붙여 `구현됨-e2e`로 올린다 |
| `예정` | 계획된 PR이 만든다 | `담당 PR`과 `e2e 담당`에 PR ID 1개 이상 |
| `미계획` | 어느 계획에도 없다 | 본문에 "제품 결정 필요"와 그 사유 |

## 스토리와 e2e 잇기

e2e는 Playwright의 `tag` 옵션으로 스토리에 잇는다.

```ts
test(
  "US-DM-04 저장한 전략을 백테스트하면 핵심 성과 지표 여섯 개가 보인다",
  { tag: ["@story", "@US-DM-04"] },
  async ({ page }) => { /* … */ },
);
```

- 스토리 태그가 붙은 테스트에는 `@story` 태그도 함께 붙인다.
- 스토리 하나를 위해 새로 쓰는 e2e는 `frontend/e2e/stories/<약자 소문자>.<주제>.spec.ts`에 두고,
  제목을 스토리 ID로 시작한다. 이 폴더는 `chromium-stories` project가 모은다.
- 이미 같은 흐름을 도는 e2e가 있으면 새로 복제하지 않는다. 기존 테스트에 태그만 더한다. 테스트
  하나가 여러 스토리를 지킬 수 있다.
- 태그는 `test(…)` 호출의 `tag` 옵션에만 둔다. `test.describe`에 스토리 태그를 달면 검사 도구가
  거부한다. 어느 테스트가 어느 스토리를 지키는지 한 줄로 보이게 하려는 것이다.
- 스토리 태그가 있는 spec에서는 `test.skip`·`test.fixme`·`test.fail`·`test.only`를 쓰지 않는다.
  opt-in spec(`workbench.real-equity.spec.ts`)에는 스토리 태그를 달지 않는다. 둘 다 릴리스 게이트에서
  스토리를 지키지 못한다.
- 스토리 e2e는 사용자의 눈으로 쓴다. role과 label로 요소를 찾고, 화면에 보이는 한글 문구로
  단언한다([frontend 테스트 규칙](../../../.claude/rules/frontend-testing.md)).

스토리 몇 개만 골라 돌릴 때는 이렇게 한다. 머신 전역 잠금을 잡는 러너를 쓰고, `playwright test`를
직접 부르지 않는다([e2e README](../../../frontend/e2e/README.md)).

```text
cd frontend
npm run test:e2e -- --grep @US-DM-03
npm run test:e2e -- --grep @story
```

## 검사 도구

`tools/quant_study_dev/user_story_trace.py`가 스토리 파일, e2e 태그, traceability 표가 서로 맞는지
본다. CI의 `user-story-harness` job이 매 push마다 돌린다.

```text
uv run python -m quant_study_dev.user_story_trace          # 검사만
uv run python -m quant_study_dev.user_story_trace --write  # traceability 표를 다시 쓰고 검사
```

검사 항목은 다음과 같다.

1. `구현됨-e2e` 스토리마다 그 스토리 태그가 붙은 e2e가 1개 이상 있고, 담당 PR은 `없음`이다.
2. e2e의 `@US-*` 태그는 전부 스토리 파일에 있는 ID다.
3. 스토리의 `e2e` 목록이 실제로 태그가 붙은 테스트와 같다.
4. `예정` 스토리는 담당 PR과 e2e 담당이 있고, `미계획` 스토리는 "제품 결정 필요"를 적었다.
5. 스토리마다 `수용 기준` 절과 항목이 있다.
6. 스토리 태그가 있는 spec에 skip·fixme·fail·only 호출이 없고, opt-in spec에는 스토리 태그가 없다.
7. `traceability.md`의 표가 스토리 파일과 e2e 태그에서 만든 표와 글자 하나까지 같다.

CI의 browser-e2e job은 여기에 하나를 더 본다. 러너로 Playwright 목록을 만들어 스토리 태그가 붙은
테스트가 릴리스 게이트 project에 수집되고 건너뛰지 않는지 확인한다. 로컬에서는 이렇게 돌린다.

```text
PLAYWRIGHT_JSON_OUTPUT_NAME=story-list.json node frontend/e2e/run-playwright.mjs --list --reporter=json
uv run python -m quant_study_dev.user_story_trace --playwright-list story-list.json
```

traceability 표는 손으로 고치지 않는다. 스토리나 태그를 고친 뒤 `--write`로 다시 쓴다.

## 스토리를 고치는 때

기능을 더하거나 동작을 바꾸는 PR이 할 일은
[user-story-harness 규칙](../../../.claude/rules/user-story-harness.md)이 정본이다. 여기에 다시 적지
않는다.

## 알려진 한계

검사기가 막지 못하는 것들이다. 리뷰어가 본다.

- 담당 PR ID는 형식만 본다. 계획 문서에 실제로 있는 ID인지는 대조하지 않는다. lang2 WORKFLOW는 P0-01
  (#167)로 main에 들어왔지만 검사기는 아직 그 문서를 읽지 않는다.
- 스토리를 지우면 traceability 표만 달라진다. `--write` 한 번이면 통과하므로 "은퇴한 ID" 기록은
  리뷰어가 확인한다.
- 수용 기준이 실제로 화면 결과를 단언하는지는 사람이 판단한다. 태그만 붙고 기준을 확인하지 않는
  e2e는 리뷰어 규칙상 결함이다.
- 정적 검사는 `test(…)` 호출의 제목이 문자열 상수일 때만 읽는다. 옵션 객체 안의 중첩은 한 겹까지
  읽는다. 그보다 깊으면 태그 위치 오류로 거부한다.
- 본문의 조건부 `test.skip(조건)`은 정적 검사가 파일 단위로 거부한다. 그 밖에 테스트가 조용히 일찍
  끝나는 코드(예: 조건부 `return`)는 잡지 못한다.
- 공급자 등록 스토리(US-DM-01) e2e는 활성 공급자가 이미 있으면 등록 단계를 건너뛴다. 지금은 먼저 도는
  project가 공급자를 만들지 않아 매번 등록한다. 공급자를 만드는 e2e가 앞 project에 생기면 이 가정이
  깨진다.
- 계약 패널 값(US-SM-02)은 `dt`·`dd` 쌍의 이웃 관계로 찾는다. 계약 패널이 정의 목록 구조를 바꾸면
  locator도 함께 고친다.
