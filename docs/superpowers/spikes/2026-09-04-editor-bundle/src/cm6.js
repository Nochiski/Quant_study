import { EditorView, basicSetup } from "codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { yamlSchema } from "codemirror-json-schema/yaml";
const schema = { type: "object", properties: { title: { type: "string" } } };
new EditorView({ doc: "title: x\n", extensions: [basicSetup, yaml(), yamlSchema(schema)], parent: document.body });
