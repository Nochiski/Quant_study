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

## 2. 실측 (Vite 8.2 / rolldown, production build, 2026-09-04)

재현 스크립트: [`docs/superpowers/spikes/2026-09-04-editor-bundle/`](../spikes/2026-09-04-editor-bundle/README.md)

| 구성 | JS raw | JS gzip | 비고 |
|---|---|---|---|
| CodeMirror 6 `basicSetup` + `@codemirror/lang-yaml` 6.1.3 + `yaml` 2.9.0 | 517 KB | 164 KB | worker 없음, 단일 chunk |
| 위 + `codemirror-json-schema` 0.8.1 | 1,122 KB | 316 KB | shiki, markdown-it, json-schema-library를 끌고 옴 |
| `monaco-editor` 0.52.2 + `monaco-yaml` 5.5.1 | 5,063 KB | 1,344 KB | main 3.3 MB + yaml.worker 1.0 MB + editor.worker 260 KB + CSS 133 KB + codicon 80 KB |
| `monaco-editor` 0.56.0 + `monaco-yaml` 5.5.1 | 빌드 실패 | — | `monaco-worker-manager`가 `monaco-editor/esm/vs/editor/editor.worker.js`를 resolve 못 함 (0.56 `exports` map) |

IME: CodeMirror 6은 contenteditable 위에서 브라우저 native composition을 그대로 쓰며 한글 조합 중
transaction을 막는 `compositionstart/end` 처리가 core에 있다. Monaco는 hidden textarea 기반이라
조합 중 커서 이동·자동완성 팝업에서 한글이 분리되는 이슈가 반복 보고되어 있다. P3-02에서 실제
브라우저 수동 검증을 acceptance로 남긴다.

## 3. 결정

### D1. CodeMirror 6를 채택한다

- `codemirror`(basicSetup), `@codemirror/lang-yaml`, `@codemirror/lang-json`, `@codemirror/state`,
  `@codemirror/view`, `@codemirror/language`, `@codemirror/autocomplete`, `@codemirror/lint`를
  `frontend/package.json` dependencies에 추가한다 (P3-02).
- YAML parse/CST/source map은 `yaml` 2.x를 쓴다 (P0-03 cross-runtime 계약과 같은 라이브러리).
- editor wrapper는 `shared/ui/code-editor`에 도메인 중립으로 두고, StrategySpec·schema 연결은
  `features/edit-strategy`가 소유한다 (WORKFLOW P3-02).
- editor chunk는 route lazy import로 분리한다. 예산: editor chunk gzip ≤ 200 KB.

### D2. Schema completion/hover는 backend runtime schema를 직접 읽는 얇은 provider로 구현한다

- `codemirror-json-schema`는 shiki/markdown-it 때문에 chunk가 두 배가 되고, hover markdown
  렌더링과 자체 validation 엔진이 backend diagnostic과 중복된다. 채택하지 않는다.
- P3-03은 `yaml` CST로 cursor path(JSON Pointer)를 구하고, P1-05 runtime schema(`kind`
  discriminator 포함)에서 그 path의 properties/enum/required를 읽어 `@codemirror/autocomplete`
  source와 hover tooltip을 만든다. 구조 검증은 `@codemirror/lint`에 붙인다.
- frontend는 허용 목록이나 range를 하드코딩하지 않는다. schema 응답만 읽는다.

### D3. 500-node 성능·접근성 기준

- 500 factor node(약 3,000줄) 문서에서 입력 지연 < 16 ms/keystroke, folding·search 정상. P6-04에서
  측정한다.
- keyboard: CM6 기본 keymap + `Escape`로 editor 포커스 탈출, `Tab` 들여쓰기는 `indentWithTab`을
  명시적으로 켜고 포커스 트랩 경고를 표시한다.
- undo history는 `@codemirror/commands` history를 쓰고 view 전환(YAML/JSON/Form/Graph) 시
  `EditorState`를 보존한다.

## 4. 대안

- **Monaco + monaco-yaml**: schema completion이 가장 완성도 높지만 gzip 1.3 MB, worker 3개, Vite
  8/rolldown과 최신 monaco 버전의 resolve 문제, IME 이슈. 기각.
- **CodeMirror 6 + codemirror-json-schema**: 빠르게 붙지만 chunk 2배와 validation 엔진 중복. 기각.
- **textarea + 별도 문법 강조**: folding, bracket matching, 마커가 없어 전문 사용자 기준 미달.

## 5. Rollback

editor wrapper는 `shared/ui/code-editor` 한 곳이다. 문제 시 wrapper 구현만 Monaco로 교체하고
`features/edit-strategy`의 document state machine은 유지한다.
