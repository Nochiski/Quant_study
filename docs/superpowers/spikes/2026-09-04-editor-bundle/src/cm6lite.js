import { EditorView, basicSetup } from "codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { parseDocument } from "yaml";
const view = new EditorView({ doc: "title: x\n", extensions: [basicSetup, yaml()], parent: document.body });
console.log(parseDocument(view.state.doc.toString()).toJS());
