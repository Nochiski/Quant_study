import { EditorView } from "@codemirror/view";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { SourceDiagnostic } from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { toDocumentDiagnostics } from "../model/use-compile-document";
import { useStrategyDocument } from "../model/use-strategy-document";
import { useCompileDocument } from "../model/use-compile-document";
import { SourceEditor } from "../ui/source-editor";

const API = "http://localhost:8000";
const requests: { source: string; aborted: boolean }[] = [];

const compiled = (
  source: string,
  diagnostics: SourceDiagnostic[] = [],
  spec: unknown = { title: "ok" },
) => ({
  format: "yaml",
  source_hash: "s".repeat(64),
  schema_version: "1.0",
  spec: diagnostics.some((d) => d.severity === "error") ? null : spec,
  canonical_json: null,
  spec_hash: diagnostics.some((d) => d.severity === "error")
    ? null
    : "h".repeat(64),
  diagnostics,
  echo: source,
});

const server = setupServer(
  http.post(`${API}/api/v1/strategy-documents/compile`, async ({ request }) => {
    const body = (await request.json()) as { source: string };
    const entry = { source: body.source, aborted: false };
    requests.push(entry);
    request.signal.addEventListener("abort", () => {
      entry.aborted = true;
    });
    // The first text answers slowly so a later text can overtake it.
    await delay(body.source.includes("slow") ? 400 : 20);
    if (body.source.includes("warn")) {
      return HttpResponse.json(
        compiled(body.source, [
          {
            code: "strategy.risk.max_name_weight.high",
            kind: "semantic",
            severity: "warning",
            pointer: "/risk/max_name_weight",
            message: "over 50%",
          },
        ]),
      );
    }
    if (body.source.includes("bad")) {
      return HttpResponse.json(
        compiled(body.source, [
          {
            code: "strategy.title.empty",
            kind: "structural",
            severity: "error",
            pointer: "/title",
            message: "title must not be empty",
          },
        ]),
      );
    }
    if (body.source.includes("boom")) return HttpResponse.error();
    return HttpResponse.json(compiled(body.source));
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  requests.length = 0;
});
afterAll(() => server.close());

const Harness = ({ initial }: { initial: string }) => {
  const [state, dispatch] = useStrategyDocument({
    kind: "new",
    format: "yaml",
    source: initial,
  });
  useCompileDocument(state, dispatch);
  return (
    <>
      <output data-testid="phase">{state.phase}</output>
      <output data-testid="version">
        {state.sourceVersion}/{state.compiledVersion}
      </output>
      <SourceEditor state={state} dispatch={dispatch} />
    </>
  );
};

const mount = async (initial: string) => {
  render(<Harness initial={initial} />);
  await screen.findByRole("textbox", { name: "편집기" });
  let view: EditorView | null = null;
  await waitFor(() => {
    const content = document.querySelector(".cm-content");
    view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
    expect(view).not.toBeNull();
  });
  return view as unknown as EditorView;
};

const type = (view: EditorView, text: string) =>
  act(() => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: text },
    });
  });

describe("useCompileDocument", () => {
  it("compiles a parsed text once and records the semantic verdict for that version", async () => {
    await mount('schema_version: "1.0"\ntitle: ok\n');
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent(
        "semantically-valid",
      ),
    );
    expect(requests.map((r) => r.source)).toEqual([
      'schema_version: "1.0"\ntitle: ok\n',
    ]);
    {
      const [source, compiled] = screen
        .getByTestId("version")
        .textContent!.split("/");
      expect(compiled).toBe(source);
    }
  });

  it("aborts the superseded request and never shows an older verdict", async () => {
    const view = await mount("title: slow\n");
    await waitFor(() => expect(requests).toHaveLength(1));
    type(view, "title: bad\n");
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent(
        "structure-invalid",
      ),
    );
    expect(requests[0]).toMatchObject({
      source: "title: slow\n",
      aborted: true,
    });
    // The slow reply (would be "semantically-valid") can no longer overwrite the verdict.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.getByTestId("phase")).toHaveTextContent("structure-invalid");
    {
      const [source, compiled] = screen
        .getByTestId("version")
        .textContent!.split("/");
      expect(compiled).toBe(source);
    }
  });

  it("sends nothing for unparsable text or during IME composition", async () => {
    const view = await mount("title: [unterminated\n");
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(requests).toHaveLength(0);
    expect(screen.getByTestId("phase")).toHaveTextContent("syntax-invalid");
    act(() => {
      view.contentDOM.dispatchEvent(
        new Event("compositionstart", { bubbles: true }),
      );
    });
    type(view, "title: 한글\n");
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(requests).toHaveLength(0);
    act(() => {
      view.contentDOM.dispatchEvent(
        new Event("compositionend", { bubbles: true }),
      );
    });
    await waitFor(() => expect(requests).toHaveLength(1));
  });

  it("lists problems with their source position and moves the selection on click", async () => {
    const user = userEvent.setup();
    const view = await mount('schema_version: "1.0"\ntitle: bad\n');
    const problems = await screen.findByRole("region", { name: "문제" });
    expect(problems).toHaveTextContent("오류 1 · 경고 0");
    const item = screen.getByRole("button", {
      name: /title must not be empty/,
    });
    expect(item).toHaveTextContent("2:8 /title");
    await user.click(item);
    const selection = view.state.selection.main;
    expect(view.state.doc.sliceString(selection.from, selection.to)).toBe(
      "bad",
    );
  });

  it("keeps warnings separate from errors and marks a stale result while typing", async () => {
    const view = await mount("risk:\n  max_name_weight: warn\n");
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent(
        "semantically-valid",
      ),
    );
    expect(screen.getByRole("region", { name: "문제" })).toHaveTextContent(
      "오류 0 · 경고 1",
    );
    expect(screen.getByRole("button", { name: /over 50%/ })).toBeEnabled();
    type(view, "risk:\n  max_name_weight: [broken\n");
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent("syntax-invalid"),
    );
    // The syntax marker replaces the old list; the old semantic warning is not shown as current.
    expect(screen.getByRole("region", { name: "문제" })).toHaveTextContent(
      "오류 1 · 경고 0",
    );
  });

  it("disables an error list that belongs to an older text", async () => {
    const view = await mount('schema_version: "1.0"\ntitle: bad\n');
    await screen.findByRole("button", { name: /title must not be empty/ });

    type(view, "a: 1\n");

    const stale = screen.getByRole("button", {
      name: /title must not be empty/,
    });
    expect(stale).toBeDisabled();
    expect(stale.closest(".problems")).toHaveClass("problems--stale");
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /title must not be empty/ }),
      ).not.toBeInTheDocument(),
    );
  });

  it("reports a transport failure as a server problem without pretending validity", async () => {
    await mount("title: boom\n");
    await waitFor(() =>
      expect(screen.getByTestId("phase")).toHaveTextContent("semantic-invalid"),
    );
    expect(screen.getByRole("region", { name: "문제" })).toHaveTextContent(
      "서버",
    );
  });
});

describe("toDocumentDiagnostics", () => {
  it("locates markers through the frontend parse map and falls back to the backend range", () => {
    const text = 'schema_version: "1.0"\nrisk:\n  max_name_weight: 2\n';
    const parse = parseSource(text, "yaml");
    const [located, fallback] = toDocumentDiagnostics(
      [
        {
          code: "a",
          kind: "semantic",
          severity: "error",
          pointer: "/risk/max_name_weight",
          message: "m",
          range: null,
        },
        {
          code: "b",
          kind: "structural",
          severity: "error",
          pointer: "/nope",
          message: "m",
          range: {
            start: { line: 0, column: 0, offset: 0 },
            end: { line: 0, column: 3, offset: 3 },
          },
        },
      ],
      parse,
    );
    expect(
      text.slice(located.range!.start.offset, located.range!.end.offset),
    ).toBe("2");
    expect(fallback.range).toEqual({
      start: { line: 0, column: 0, offset: 0 },
      end: { line: 0, column: 3, offset: 3 },
    });
  });

  it("anchors a root diagnostic at the start instead of marking the whole document", () => {
    const text = 'schema_version: "1.0"\ntitle: ok\n';
    const parse = parseSource(text, "yaml");
    const [diagnostic] = toDocumentDiagnostics(
      [
        {
          code: "required",
          kind: "structural",
          severity: "error",
          pointer: "",
          message: "data is required",
          range: null,
        },
      ],
      parse,
    );
    expect(diagnostic.range?.start.offset).toBe(0);
    expect(diagnostic.range?.end.offset).toBe(1);
  });
});
