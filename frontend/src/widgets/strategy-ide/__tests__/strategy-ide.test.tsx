import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemePreferenceProvider } from "../../../shared/lib/theme";
import { StrategyIde } from "..";
import {
  PANEL_LAYOUT_STORAGE_KEY,
  readPanelSizes,
} from "../model/use-panel-layout";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

const matchMedia = (matches: boolean) =>
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );

const controlledMatchMedia = (initial: boolean) => {
  let matches = initial;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      get matches() {
        return matches;
      },
      media: query,
      addEventListener: (
        _type: "change",
        listener: (event: MediaQueryListEvent) => void,
      ) => listeners.add(listener),
      removeEventListener: (
        _type: "change",
        listener: (event: MediaQueryListEvent) => void,
      ) => listeners.delete(listener),
    })),
  );
  return {
    setMatches(next: boolean) {
      matches = next;
      const event = { matches: next } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
};

const mount = (props: Partial<Parameters<typeof StrategyIde>[0]> = {}) => {
  const { versionLabel = "v12", ...rest } = props;
  return render(
    <ThemePreferenceProvider>
      <StrategyIde
        title="새 전략"
        versionLabel={versionLabel}
        editor={<textarea aria-label="source" />}
        {...rest}
      />
    </ThemePreferenceProvider>,
  );
};

describe("StrategyIde", () => {
  it("renders the outline slot and a single set of projection tabs", () => {
    matchMedia(false);
    mount({
      outline: (
        <ul role="tree" aria-label="StrategySpec document structure">
          <li role="treeitem">parameters</li>
          <li role="treeitem">risk</li>
        </ul>
      ),
    });
    const outline = screen.getByRole("navigation", { name: "전략 구조" });
    expect(within(outline).getByRole("tree")).toHaveTextContent(
      "parametersrisk",
    );
    // Projection views + results tabs; the caller owns inspector content and there is no
    // Data→…→Execution stepper.
    expect(
      new Set(
        screen.getAllByRole("tablist").map((l) => l.getAttribute("aria-label")),
      ),
    ).toEqual(new Set(["표현 전환", "중간 결과"]));
    expect(screen.getByRole("region", { name: "편집기" })).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "중간 결과" }),
    ).toBeInTheDocument();
  });

  it("resizes every panel linearly by keyboard and pointer, inverting the right/bottom panes", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    const outlineHandle = screen.getByRole("separator", {
      name: "전략 구조 크기 조절",
    });
    outlineHandle.focus();
    await user.keyboard("{ArrowRight}");
    expect(outlineHandle).toHaveAttribute("aria-valuenow", "256");

    const inspectorHandle = screen.getByRole("separator", {
      name: "계약 크기 조절",
    });
    inspectorHandle.focus();
    await user.keyboard("{ArrowRight}");
    expect(inspectorHandle).toHaveAttribute("aria-valuenow", "336");
    await user.keyboard("{Home}");
    expect(inspectorHandle).toHaveAttribute("aria-valuenow", "240");
    await user.keyboard("{ArrowRight}");
    expect(inspectorHandle).toHaveAttribute("aria-valuenow", "256"); // never locked at the minimum
    await user.keyboard("{End}");
    expect(inspectorHandle).toHaveAttribute("aria-valuenow", "520");

    const debuggerHandle = screen.getByRole("separator", {
      name: "중간 결과 크기 조절",
    });
    debuggerHandle.setPointerCapture = vi.fn();
    debuggerHandle.releasePointerCapture = vi.fn();
    debuggerHandle.hasPointerCapture = vi.fn(() => true);
    fireEvent.pointerDown(debuggerHandle, { pointerId: 1, clientY: 500 });
    for (const clientY of [510, 520, 530]) {
      fireEvent.pointerMove(debuggerHandle, { pointerId: 1, clientY });
    }
    expect(debuggerHandle).toHaveAttribute("aria-valuenow", "190"); // 220 - 30, linear
  });

  it("collapses and restores panels with resolvable controls and distinct names", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    const collapse = screen
      .getAllByRole("button", { name: /접기/ })
      .map((b) => b.textContent);
    expect(new Set(collapse).size).toBe(collapse.length);

    await user.click(screen.getByRole("button", { name: "계약 접기" }));
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
    const restore = screen.getByRole("button", {
      name: "계약",
      expanded: false,
    });
    expect(
      document.getElementById(restore.getAttribute("aria-controls") ?? ""),
    ).not.toBeNull();
    await user.click(restore);
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "전략 구조 접기" }));
    expect(
      screen.queryByRole("navigation", { name: "전략 구조" }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "전략 구조", expanded: false }),
    );
    expect(
      screen.getByRole("navigation", { name: "전략 구조" }),
    ).toBeInTheDocument();
  });

  it("keeps drawers closed by default below 1280px, opens them from the header, closes on Escape", async () => {
    matchMedia(true);
    const user = userEvent.setup();
    mount();
    expect(
      screen.queryByRole("separator", { name: "계약 크기 조절" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", {
      name: "계약",
      expanded: false,
    });
    await user.click(toggle);
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "계약", expanded: true }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
  });

  it("closes wide panels when the viewport becomes narrow", () => {
    const media = controlledMatchMedia(false);
    mount();
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "중간 결과" }),
    ).toBeInTheDocument();

    act(() => media.setMatches(true));

    expect(
      screen.getByRole("button", { name: "계약", expanded: false }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "중간 결과", expanded: false }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "중간 결과" }),
    ).not.toBeInTheDocument();
  });

  it("renders the concept frame: breadcrumb, run action, meta line, outline and snippets", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const onRunBacktest = vi.fn();
    mount({
      onRunBacktest,
      saveStatus: "방금 저장됨",
      meta: { author: "김연구" },
    });
    expect(
      screen.getByRole("navigation", { name: "현재 위치" }),
    ).toHaveTextContent(/새 전략 v12/);
    expect(screen.getByText("방금 저장됨")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /백테스트 실행/ }));
    expect(onRunBacktest).toHaveBeenCalledTimes(1);
    expect(screen.getByText("김연구")).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "전략 구조" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "스니펫" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("renders caller-owned inspector content without sample contract values", () => {
    matchMedia(false);
    mount({ inspector: <div>runtime contract projection</div> });
    const inspector = screen.getByRole("complementary", { name: "계약" });
    expect(inspector).toHaveTextContent("runtime contract projection");
    expect(inspector).not.toHaveTextContent("/risk/max_name_weight");
  });

  it("keeps a document notice visible outside every representation panel", () => {
    matchMedia(false);
    mount({
      view: "diff",
      availableViews: ["yaml", "diff"],
      projections: { diff: <div>diff projection</div> },
      notice: <div role="alert">document recovery</div>,
    });

    expect(screen.getByRole("alert")).toHaveTextContent("document recovery");
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("connects every tab to a labelled panel", () => {
    matchMedia(false);
    mount({ availableViews: ["yaml", "json"] });

    for (const tab of screen.getAllByRole("tab")) {
      const panel = document.getElementById(
        tab.getAttribute("aria-controls") ?? "",
      );
      expect(panel).not.toBeNull();
      expect(panel).toHaveAttribute("role", "tabpanel");
      expect(panel).toHaveAttribute("aria-labelledby", tab.id);
    }
  });

  it("runs only enabled document shortcuts and ignores IME composition", () => {
    matchMedia(false);
    const onValidate = vi.fn();
    const onSave = vi.fn();
    const onRunBacktest = vi.fn();
    const onViewChange = vi.fn();
    mount({
      onValidate,
      validateDisabled: false,
      onSave,
      saveDisabled: false,
      onRunBacktest,
      runDisabled: false,
      onViewChange,
      availableViews: ["yaml", "diff"],
    });

    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "5", altKey: true });
    fireEvent.keyDown(window, {
      key: "s",
      ctrlKey: true,
      isComposing: true,
      keyCode: 229,
    });

    expect(onValidate).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onRunBacktest).toHaveBeenCalledTimes(1);
    expect(onViewChange).toHaveBeenCalledWith("diff");
  });

  it("keeps disabled document shortcuts fail-closed", () => {
    matchMedia(false);
    const onValidate = vi.fn();
    const onSave = vi.fn();
    const onRunBacktest = vi.fn();
    mount({
      onValidate,
      validateDisabled: true,
      onSave,
      saveDisabled: true,
      onRunBacktest,
      runDisabled: true,
    });

    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true, shiftKey: true });

    expect(onValidate).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
    expect(onRunBacktest).not.toHaveBeenCalled();
  });

  it("searches document symbols and controls panels and theme from the palette", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const onSelectSymbol = vi.fn();
    mount({
      symbols: [
        {
          id: "/factors/0/graph/nodes/2",
          pointer: "/factors/0/graph/nodes/2",
          label: "factors › momentum › rank_1",
          description: "/factors/0/graph/nodes/2",
          keywords: ["node", "rank_1"],
        },
      ],
      onSelectSymbol,
    });

    await user.click(screen.getByRole("button", { name: /명령/ }));
    await user.type(screen.getByRole("combobox"), "rank_1");
    await user.keyboard("{Enter}");
    expect(onSelectSymbol).toHaveBeenCalledWith("/factors/0/graph/nodes/2");

    await user.click(screen.getByRole("button", { name: /명령/ }));
    await user.type(screen.getByRole("combobox"), "닫기 계약");
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /명령/ }));
    await user.type(screen.getByRole("combobox"), "테마: 다크");
    await user.keyboard("{Enter}");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("quant-workbench.theme.v1")).toContain("dark");
  });

  it("persists bounded panel sizes and rejects hostile stored layouts", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const first = mount();
    const outline = screen.getByRole("separator", {
      name: "전략 구조 크기 조절",
    });
    outline.focus();
    await user.keyboard("{ArrowRight}");
    expect(outline).toHaveAttribute("aria-valuenow", "256");
    first.unmount();

    mount();
    expect(
      screen.getByRole("separator", { name: "전략 구조 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "256");

    const hostile = {
      getItem: () =>
        JSON.stringify({
          version: 1,
          sizes: {
            outlineWidth: 421,
            inspectorWidth: 320,
            debuggerHeight: 220,
          },
        }),
      setItem: vi.fn(),
    };
    expect(readPanelSizes(hostile)).toBeNull();
    expect(
      JSON.parse(localStorage.getItem(PANEL_LAYOUT_STORAGE_KEY) ?? "null"),
    ).toEqual({
      version: 1,
      sizes: { outlineWidth: 256, inspectorWidth: 320, debuggerHeight: 220 },
    });
  });
});
