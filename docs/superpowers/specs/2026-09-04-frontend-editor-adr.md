# ADR: Frontend source editor 선택 (P0-02)

> 작성: 2026-09-04
>
> 상태: Accepted
>
> 상위 ADR: [Strategy Authoring Contract](./2026-09-04-strategy-authoring-contract-adr.md)
> · Initiative tracker: [PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)

## 1. 맥락

YAML/JSON source editor는 전문 사용자 IDE의 중심 컴포넌트다. 후보는 Monaco(VS Code 에디터)와
CodeMirror 6이다. WORKFLOW P0-02는 한글 IME 안정성과 lazy bundle 비용을 우선 기준으로 두고 schema
completion 품질을 함께 보라고 한다.

## 2. 실측 (Vite 8.2.2 / rolldown, production build, 2026-09-04)

재현 스크립트: [`docs/superpowers/spikes/2026-09-04-editor-bundle/`](../spikes/2026-09-04-editor-bundle/README.md)
(direct 의존성 7개 pin + `package-lock.json` 커밋).

| 구성 | eager JS raw | eager JS gzip | 전체 JS gzip (lazy chunk 포함) | 비고 |
|---|---|---|---|---|
| CodeMirror 6 `basicSetup` + `@codemirror/lang-yaml` 6.1.3 + `yaml` 2.9.0 | 517 KB | 164 KB | 164 KB | worker 없음, 단일 chunk |
| 위 + `codemirror-json-schema` 0.8.1 | 897 KB | 293 KB | 316 KB | shiki, markdown-it, json-schema-library를 끌고 옴 |
| `monaco-editor` 0.52.2 + `monaco-yaml` 5.5.1 | 3,288 KB (main) + worker 2개 1,260 KB | 약 1,270 KB | 약 1,340 KB | 언어 모드 lazy chunk 약 90개 포함. CSS 133 KB, codicon 80 KB 별도 |
| `monaco-editor` 0.55.1 + `monaco-yaml` 5.5.1 | 3,749 KB (main) | 958 KB | — | 빌드 성공. 0.52.2보다 큼 |
| `monaco-editor` 0.56.0 + `monaco-yaml` 5.5.1 | 빌드 실패 | — | — | 아래 원인 |

- Monaco 0.56.0 실패 원인은 bundler가 아니라 패키지 계약이다. 0.56.0의 `exports` map이
  `"./*": "./esm/vs/*.js"`로 바뀌어 `monaco-worker-manager`(2.0.1, `monaco-yaml`의 의존성)의
  `monaco-editor/esm/vs/editor/editor.worker.js` deep import가 exports map을 존중하는 어떤 bundler에서도
  resolve되지 않는다. 0.55.1까지는 `"./*": "./*"`라 빌드된다. 표의 기준 버전을 0.52.2로 둔 이유는
  monaco-yaml 5.5.1이 공식 검증한 조합이고 textarea 입력 경로(아래 IME 항목)를 대표하기 때문이다.
- Schema completion 품질은 이 spike에서 측정하지 않았다. 비교에 쓸 P1-05 runtime schema(`kind`
  discriminator 포함)가 아직 없기 때문이며, P3-03 acceptance("P1-05 schema의 모든 authoring path가
  completion에서 탐색된다")로 이월한다.
- 500 factor node 입력 지연과 한글 IME 수동 검증은 브라우저가 필요하므로 P3-02(IME)와 P6-04(성능)
  acceptance로 이월한다.

IME 동작 사실(라이브러리 소스 기준):

- CodeMirror 6(`@codemirror/view` 6.43.x)은 데스크톱에서 contenteditable 위의 브라우저 native
  composition을 쓴다. 조합 중에도 DOM 변경을 읽어 transaction을 dispatch하며, 조합 상태는
  `view.composing`으로 노출하고 조합 중인 DOM 노드의 재그리기를 피한다. EditContext는 Android에서만
  켜진다. 따라서 **P3-01 parse trigger와 P3-04 backend validate debounce는 `view.composing`이 false일
  때만 실행**해야 한글 조합 중 diagnostic 깜빡임과 요청 폭주를 막는다.
- Monaco 0.52.2는 hidden textarea(`textAreaHandler`) 입력 경로이며 한글 조합 중 커서 이동·자동완성
  팝업에서 조합이 끊기는 이슈가 반복 보고되어 있다. 0.56.0은 `editContext` 옵션 기본값이 `true`로
  EditContext 경로를 쓰지만 monaco-yaml 호환 때문에 지금 쓸 수 없다. 이 비교는 textarea 경로 기준이다.

## 3. 결정

### D1. CodeMirror 6를 채택한다

- P3-02에서 `frontend/package.json` dependencies에 추가할 패키지(직접 import하는 것은 모두 pin):
  `codemirror`, `@codemirror/state`, `@codemirror/view`, `@codemirror/language`,
  `@codemirror/commands`(history, `indentWithTab`), `@codemirror/search`, `@codemirror/autocomplete`,
  `@codemirror/lint`, `@codemirror/lang-yaml`, `@codemirror/lang-json`.
- YAML parse/CST/source map은 `yaml` 2.x를 쓴다. P0-03이 같은 라이브러리를 frontend parser로
  확정했다.
- editor wrapper는 `shared/ui/code-editor`에 도메인 중립으로 두고, StrategySpec·schema 연결은
  `features/edit-strategy`가 소유한다 (WORKFLOW P3-02).
- editor chunk는 route lazy import로 분리한다. 예산: editor chunk gzip ≤ 200 KB (현재 baseline 164 KB.
  `lang-json` 추가분은 P3-02에서 측정).

### D2. Frontend는 schema를 "탐색"만 하고 "검증"하지 않는다

SoT 규칙(`strategy-workbench-sot.md`: validation 규칙 복제 금지, `frontend-api-state.md`: 최종 판정은
backend)과 P0-01 D4(structural blocking error는 typed hydrate 실패)에 맞춰 경계를 다음처럼 고정한다.

- **Frontend가 만드는 marker는 syntax뿐이다.** `yaml` parse error(및 P0-03 정책 거부)를
  `@codemirror/lint` marker로 표시한다. 이 marker는 advisory이며, Save/Run/Debug gate는 P3-04가 받는
  backend compile diagnostic(syntax + structural + semantic 통합 배열)만 쓴다.
- **Structural/semantic marker는 frontend가 계산하지 않는다.** required/type/enum/unknown-key 판정은
  P1-03 compile API가 돌려주고 P3-04가 pointer→range로 붙인다. frontend에 JSON Schema evaluator
  (`ajv`, `codemirror-json-schema` 등)를 두지 않는다. 수기 walker로 `required`/`type`/`enum`을 판정하는
  것도 validation 복제의 우회이므로 금지한다.
- **Completion/hover는 schema 탐색이다.** P3-03은 `yaml` CST로 cursor path(JSON Pointer)를 구하고,
  P1-05 runtime schema에서 그 path의 node를 해소해(`$ref`, `oneOf` + `kind` discriminator 분기 포함)
  property 이름·enum 값·description·default를 completion과 hover에 보여준다. 이 해소기는 "어떤 값이
  허용되는가"를 보여줄 뿐 "지금 값이 틀렸다"를 판정하지 않는다. `$ref`/`oneOf` 해소 규칙은 P3-03이
  P1-05 acceptance("모든 authoring path가 schema에서 탐색된다")와 짝을 이루는 계약 테스트로 고정한다.
- **Factor ID·dataset field 후보는 schema가 아니라 registry 응답에서 온다.** P1-05 contract API가
  링크하는 Factor Registry/Dataset Registry를 query cache로 읽는다.
- JSON Pointer → source range(P3-04 diagnostic 위치, P4-01 outline 선택)는 `yaml` CST 노드의 `range`
  offset으로 만든다. cursor → path의 역방향도 같은 CST를 쓴다.

이 결정으로 P3-03의 "required/type/enum marker" 문구는 "backend diagnostic marker(P3-04) + schema
completion/hover(P3-03)"로 읽는다 (WORKFLOW P3-03 갱신).

### D3. Wrapper handle과 history

- `shared/ui/code-editor`는 editor-agnostic handle만 노출한다: 문서 텍스트, selection(offset), offset ↔
  line/column, diagnostics 입력, completion source 콜백, 그리고 history 보존용 **opaque state**
  (`unknown` 타입의 불투명 값). `features/edit-strategy`는 이 handle만 잡고 CodeMirror 타입을 import하지
  않는다.
- undo history는 `@codemirror/commands` history를 쓰되, view 전환(YAML/JSON/Form/Graph) 시 wrapper가
  opaque state를 돌려주고 feature가 보관했다가 복원한다.
- keyboard: CM6 기본 keymap + `Escape`로 editor 포커스 탈출, `Tab` 들여쓰기는 `indentWithTab`을
  명시적으로 켜고 포커스 트랩 안내를 표시한다.
- 500 factor node(약 3,000줄) 문서에서 입력 지연 < 16 ms/keystroke, folding·search 정상. worker가 없어
  `yaml` parse가 main thread에서 돌므로 P3-01은 parse를 debounce하고 P6-04에서 측정한다.

## 4. 대안

- **Monaco + monaco-yaml**: schema completion이 가장 완성도 높지만 gzip 약 1.3 MB, worker 3개, textarea
  기반 IME 이슈, monaco ≥ 0.56 exports map 미지원. 기각. monaco-yaml이 0.56+를 지원하면 IME 논거는
  약해지고 번들 8배 차이가 주된 근거로 남는다.
- **CodeMirror 6 + codemirror-json-schema**: 빠르게 붙지만 chunk 2배와 validation 엔진 중복(D2 위반).
  기각.
- **frontend에 범용 JSON Schema evaluator(ajv)**: "backend schema를 데이터로 실행"하므로 규칙에는
  맞지만 gzip 예산과 backend diagnostic과의 중복 표시 문제가 있다. D2에서 structural marker 자체를
  backend로 일원화했으므로 도입하지 않는다.
- **textarea + 별도 문법 강조**: folding, bracket matching, 마커가 없어 전문 사용자 기준 미달.

## 5. Rollback

- editor 구현 교체 범위는 `shared/ui/code-editor` wrapper와 `features/edit-strategy`의 opaque history
  state 보관부다. handle 계약(D3)이 유지되면 document state machine(P3-01)과 diagnostic 연결(P3-04)은
  바뀌지 않는다.
- Monaco로 되돌릴 때는 monaco-yaml 대신 D2와 같은 "backend diagnostic + schema 탐색" 구조를 유지한다.
