import * as monaco from "monaco-editor";
import { configureMonacoYaml } from "monaco-yaml";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import YamlWorker from "monaco-yaml/yaml.worker?worker";
self.MonacoEnvironment = { getWorker(_, label) { return label === "yaml" ? new YamlWorker() : new EditorWorker(); } };
configureMonacoYaml(monaco, { schemas: [{ uri: "inmemory://schema", fileMatch: ["*"], schema: { type: "object" } }] });
monaco.editor.create(document.body, { value: "title: x\n", language: "yaml" });
