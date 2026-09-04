# Editor bundle spike (P0-02)

[Frontend editor ADR](../../specs/2026-09-04-frontend-editor-adr.md)의 번들 크기 표를 재현한다.
repo 의존성이 아니며 `frontend/`와 분리된 throwaway 프로젝트다.

```powershell
cd docs/superpowers/spikes/2026-09-04-editor-bundle
npm install --no-audit --no-fund
npm run build:cm6lite   # CodeMirror 6 + lang-yaml + yaml
npm run build:cm6       # 위 + codemirror-json-schema
npm run build:monaco    # monaco-editor 0.52.2 + monaco-yaml
```

각 `dist-*/assets/*.js`의 raw/gzip 합계를 비교한다. `monaco-editor@0.56.0`은 `monaco-worker-manager`가
`monaco-editor/esm/vs/editor/editor.worker.js`를 resolve하지 못해 Vite 8(rolldown) 빌드가 실패한다.
`node_modules/`와 `dist-*/`는 커밋하지 않는다.
