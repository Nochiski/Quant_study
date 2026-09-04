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

import { StrategyIde } from "..";

afterEach(() => {
  cleanup();
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
    <StrategyIde
      title="새 전략"
      versionLabel={versionLabel}
      editor={<textarea aria-label="source" />}
      {...rest}
    />,
  );
};

describe("StrategyIde", () => {
  it("shows the outline with every section including parameters and a single tablist", () => {
    matchMedia(false);
    mount();
    const outline = screen.getByRole("navigation", { name: "전략 구조" });
    const sections = within(outline)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(sections.some((text) => text?.includes("parameters"))).toBe(true);
    expect(sections.some((text) => text?.includes("risk"))).toBe(true);
    // view tabs + inspector tabs + results tabs; no Data→…→Execution stepper
    expect(
      new Set(
        screen.getAllByRole("tablist").map((l) => l.getAttribute("aria-label")),
      ),
    ).toEqual(new Set(["표현 전환", "계약", "중간 결과"]));
    expect(screen.getByRole("region", { name: "편집기" })).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "중간 결과" }),
    ).toBeInTheDocument();
  });

  it("marks the current section and reports selection", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const onSelectSection = vi.fn();
    mount({ currentSection: "risk", onSelectSection });
    const outline = screen.getByRole("navigation", { name: "전략 구조" });
    expect(
      within(outline).getByRole("button", { current: "location" }),
    ).toHaveTextContent("risk");
    await user.click(
      within(outline).getByRole("button", { name: /portfolio/ }),
    );
    expect(onSelectSection).toHaveBeenCalledWith("portfolio");
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

  it("renders the concept frame: breadcrumb, run action, meta line, filterable outline, snippets", async () => {
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
    await user.type(
      screen.getByRole("searchbox", { name: "전략 구조 필터" }),
      "risk",
    );
    const outline = screen.getByRole("navigation", { name: "전략 구조" });
    expect(
      within(outline).getAllByRole("button", { name: /risk/ }),
    ).toHaveLength(1);
    await user.clear(screen.getByRole("searchbox", { name: "전략 구조 필터" }));
    await user.type(
      screen.getByRole("searchbox", { name: "전략 구조 필터" }),
      "기본",
    );
    expect(
      within(outline).getByRole("button", { name: /기본 정보/ }),
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

  it("lets the placeholder inspector tabs switch panels", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "오류" }));
    expect(screen.getByRole("tab", { name: "오류" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    for (const tab of screen.getAllByRole("tab", { selected: true })) {
      expect(
        document.getElementById(tab.getAttribute("aria-controls") ?? ""),
      ).not.toBeNull();
    }
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
});
