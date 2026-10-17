import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
        outline={null}
        snippets={null}
        inspector={null}
        debugger={null}
        title="새 전략"
        versionLabel={versionLabel}
        editor={<textarea aria-label="source" />}
        {...rest}
      />
    </ThemePreferenceProvider>,
  );
};

const StatefulViewIde = () => {
  const [view, setView] =
    useState<NonNullable<Parameters<typeof StrategyIde>[0]["view"]>>("yaml");
  return (
    <ThemePreferenceProvider>
      <StrategyIde
        outline={null}
        snippets={null}
        inspector={null}
        debugger={null}
        title="새 전략"
        versionLabel="초안"
        editor={<textarea aria-label="source" />}
        sourceView="yaml"
        projections={{ graph: <div>Graph projection</div> }}
        view={view}
        onViewChange={setView}
        availableViews={["graph", "yaml"]}
      />
    </ThemePreferenceProvider>
  );
};

describe("StrategyIde", () => {
  it("주본문을 먼저 열고 구조·계약·중간 결과는 명시해서 연다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount({ outline: <ul role="tree"><li role="treeitem">risk</li></ul> });
    expect(screen.queryByRole("navigation", { name: "전략 구조" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "계약" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "중간 결과" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "편집기" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    expect(screen.getByRole("tree")).toHaveTextContent("risk");
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
    expect(screen.getByRole("complementary", { name: "계약" })).toBeInTheDocument();
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
  });

  it("명시해서 연 보조 패널의 폭을 키보드로 조절한다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
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

  });

  it("과거의 작은 분할 높이를 복원해도 추적 작업공간과 편집 원문 상태를 보존한다", async () => {
    matchMedia(false);
    localStorage.setItem(PANEL_LAYOUT_STORAGE_KEY, JSON.stringify({version: 1, sizes: {outlineWidth: 240, inspectorWidth: 320, debuggerHeight: 120}}));
    const user = userEvent.setup();
    mount({ debugger: <input aria-label="추적 종목" defaultValue="SEC001" /> });
    await user.type(screen.getByRole("textbox", { name: "source" }), "보존할 원문");
    await user.click(screen.getByRole("button", { name: "중간 결과" }));
    expect(screen.queryByRole("region", { name: "편집기" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "중간 결과" })).not.toHaveStyle({height: "120px"});
    expect(screen.queryByRole("separator", { name: "중간 결과 크기 조절" })).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "추적 종목" }), ",SEC002");
    await user.click(screen.getByRole("button", { name: "전략 편집" }));
    expect(screen.getByRole("textbox", { name: "source" })).toHaveValue("보존할 원문");
    await user.click(screen.getByRole("button", { name: "중간 결과" }));
    expect(screen.getByRole("textbox", { name: "추적 종목" })).toHaveValue("SEC001,SEC002");
  });

  it("추적 중 AI와 계약을 명시해서 열어도 추적과 범위 입력을 유지한다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount({ assistant: <p>AI 대화</p>, debugger: <input aria-label="추적 범위" defaultValue="SEC001" /> });
    await user.click(screen.getByRole("button", { name: "중간 결과" }));
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }));
    expect(screen.getByRole("region", { name: "중간 결과" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
    expect(screen.getByRole("region", { name: "중간 결과" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "추적 범위" })).toHaveValue("SEC001");
    expect(screen.queryByRole("complementary", { name: "AI 어시스턴트" })).not.toBeInTheDocument();
  });

  it("추적 중 표현 열기 단축키는 원문을 보존하며 편집으로 복귀한다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    render(<StatefulViewIde />);
    await user.type(screen.getByRole("textbox", { name: "source" }), "같은 원문");
    await user.click(screen.getByRole("button", { name: "중간 결과" }));
    fireEvent.keyDown(window, { key: "1", altKey: true });
    expect(screen.getByRole("region", { name: "편집기" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "중간 결과" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByRole("textbox", { name: "source" })).toHaveValue("같은 원문");
  });

  it("collapses and restores panels with resolvable controls and distinct names", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
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

  it("작은 화면에서는 보조 패널을 하나씩 전환하고 새 패널로 포커스를 옮긴다", async () => {
    matchMedia(true);
    const user = userEvent.setup();
    mount({ assistant: <button>AI 입력</button> });
    expect(screen.queryByRole("navigation", { name: "전략 구조" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    expect(screen.getByRole("navigation", { name: "전략 구조" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
    expect(screen.queryByRole("navigation", { name: "전략 구조" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "계약 접기" })).toHaveFocus();
    await user.keyboard("{Alt>}a{/Alt}");
    expect(screen.queryByRole("complementary", { name: "계약" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    expect(screen.queryByRole("complementary", { name: "AI 어시스턴트" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "전략 구조 접기" })).toHaveFocus();
  });

  it.each([true, false])("좁은 화면 전환은 이미 열린 구조와 현재 포커스를 유지한다: %s", async (insideOutline) => {
    const media = controlledMatchMedia(false);
    mount({ outline: <button>구조 항목</button> });
    fireEvent.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    const focused = insideOutline
      ? screen.getByRole("button", { name: "구조 항목" })
      : screen.getByRole("textbox", { name: "source" });
    focused.focus();
    act(() => media.setMatches(true));
    expect(screen.getByRole("navigation", { name: "전략 구조" })).toBeInTheDocument();
    expect(focused).toHaveFocus();
  });

  it("좁은 화면으로 바뀌어도 현재 추적 작업공간을 유지한다", () => {
    const media = controlledMatchMedia(false);
    mount();
    fireEvent.click(screen.getByRole("button", { name: "중간 결과" }));
    fireEvent.click(screen.getByRole("button", { name: "계약", expanded: false }));
    act(() => media.setMatches(true));
    expect(screen.queryByRole("complementary", { name: "계약" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "중간 결과" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "중간 결과" })).toHaveAttribute("aria-pressed", "true");
  });

  it("lets palette Escape close only the top modal and restore focus inside a narrow drawer", async () => {
    matchMedia(true);
    const user = userEvent.setup();
    mount();
    await user.click(
      screen.getByRole("button", { name: "계약", expanded: false }),
    );
    const drawerControl = screen.getByRole("button", { name: "계약 접기" });
    await waitFor(() => expect(drawerControl).toHaveFocus());

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveFocus());
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(
      screen.getByRole("button", { name: "명령 팔레트 닫기" }),
    ).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(drawerControl).toHaveFocus());
  });

  it("renders the concept frame: breadcrumb, run action, meta line, outline and snippets", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const onRunBacktest = vi.fn();
    mount({
      onRunBacktest,
      editorActions: <button onClick={onRunBacktest}>백테스트 실행</button>,
      saveStatus: "방금 저장됨",
      meta: { author: "김연구" },
    });
    expect(
      screen.getByRole("navigation", { name: "현재 위치" }),
    ).toHaveTextContent(/새 전략.*v12/);
    expect(screen.getByText("방금 저장됨")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /백테스트 실행/ }));
    expect(onRunBacktest).toHaveBeenCalledTimes(1);
    await user.click(screen.getByText("문서 정보"));
    expect(screen.getByText("김연구")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    expect(
      screen.getByRole("navigation", { name: "전략 구조" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "스니펫" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("저장 실패는 간결하게 드러내고 문서 정보에서 전체 원인을 읽는다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount({ saveTone: "error", saveStatus: "서버에 초안을 저장할 수 없습니다. 원문은 로컬에 보존했습니다." });
    expect(screen.getByText("저장 오류")).toBeVisible();
    await user.click(screen.getByText("문서 정보"));
    expect(screen.getByText("서버에 초안을 저장할 수 없습니다. 원문은 로컬에 보존했습니다.")).toBeVisible();
  });

  it("renders caller-owned inspector content without sample contract values", () => {
    matchMedia(false);
    mount({ inspector: <div>runtime contract projection</div> });
    fireEvent.click(screen.getByRole("button", { name: "계약", expanded: false }));
    const inspector = screen.getByRole("complementary", { name: "계약" });
    expect(inspector).toHaveTextContent("runtime contract projection");
    expect(inspector).not.toHaveTextContent("/risk/max_name_weight");
  });

  it("keeps a document notice visible outside every representation panel", () => {
    matchMedia(false);
    mount({
      view: "graph",
      availableViews: ["graph", "yaml"],
      revisionDiff: () => <div>diff projection</div>,
      notice: <div role="alert">document recovery</div>,
    });

    expect(screen.getByRole("alert")).toHaveTextContent("document recovery");
    expect(screen.getByRole("tab", { name: "그래프" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("connects every tab to a labelled panel", () => {
    matchMedia(false);
    mount({ availableViews: ["graph", "yaml"] });

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
      availableViews: ["graph", "yaml"],
    });

    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "1", altKey: true });
    fireEvent.keyDown(window, {
      key: "s",
      ctrlKey: true,
      isComposing: true,
      keyCode: 229,
    });

    expect(onValidate).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onRunBacktest).toHaveBeenCalledTimes(1);
    expect(onViewChange).toHaveBeenCalledWith("graph");
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

  it("runs undo and redo from the global shortcut, even with the editor hidden (P1-02)", () => {
    matchMedia(false);
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    // Graph 탭처럼 편집기가 `hidden`인 상태: 포커스는 본문에 있고 CodeMirror 키맵은 닿지 않는다.
    mount({
      onUndo,
      onRedo,
      view: "graph",
      sourceView: "yaml",
      availableViews: ["graph", "yaml"],
      projections: { graph: <div>그래프</div> },
    });

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    fireEvent.keyDown(window, { key: "z", metaKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "Z", ctrlKey: true, shiftKey: true });

    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onRedo).toHaveBeenCalledTimes(2);
  });

  it("yields Ctrl+Z to whatever already edits text under the cursor (P1-02)", () => {
    matchMedia(false);
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    mount({ onUndo, onRedo });

    // 편집기 슬롯의 textarea, Form의 텍스트 입력, CodeMirror의 contenteditable — 셋 다 자기 되돌리기를 갖는다.
    const textarea = screen.getByLabelText("source");
    fireEvent.keyDown(textarea, { key: "z", ctrlKey: true });
    fireEvent.keyDown(textarea, { key: "z", ctrlKey: true, shiftKey: true });

    const input = document.createElement("input");
    input.type = "number";
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "z", ctrlKey: true });

    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    document.body.appendChild(editable);
    fireEvent.keyDown(editable, { key: "z", ctrlKey: true });

    expect(onUndo).not.toHaveBeenCalled();
    expect(onRedo).not.toHaveBeenCalled();

    // 되돌리기를 갖지 않는 컨트롤에서는 문서 되돌리기가 그대로 동작한다: 드롭다운, 그리고 분절 위젯인
    // 날짜 입력(Form 탭의 시작일·종료일) — 여기서 양보하면 Ctrl+Z가 죽는다.
    const select = document.createElement("select");
    document.body.appendChild(select);
    fireEvent.keyDown(select, { key: "z", ctrlKey: true });
    expect(onUndo).toHaveBeenCalledTimes(1);

    const date = document.createElement("input");
    date.type = "date";
    document.body.appendChild(date);
    fireEvent.keyDown(date, { key: "z", ctrlKey: true });
    expect(onUndo).toHaveBeenCalledTimes(2);

    input.remove();
    editable.remove();
    select.remove();
    date.remove();
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

  it("ignores repeated palette shortcuts and resets every closed session", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const search = screen.getByRole("combobox");
    await user.type(search, "닫기 계약");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true, repeat: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("combobox")).toHaveValue("");
  });

  it("moves focus to view tabs for palette and Alt navigation", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    render(<StatefulViewIde />);
    const source = screen.getByRole("textbox", { name: "source" });
    source.focus();

    fireEvent.keyDown(window, { key: "1", altKey: true });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "그래프" })).toHaveFocus(),
    );

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    source.focus();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await user.type(screen.getByRole("combobox"), "표현 열기 GRAPH");
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "그래프" })).toHaveFocus(),
    );
    expect(source.closest('[role="tabpanel"]')).toHaveAttribute("hidden");
  });

  it("moves focus from a hidden panel command to its restore toggle", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
    const collapse = screen.getByRole("button", { name: "계약 접기" });
    collapse.focus();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await user.type(screen.getByRole("combobox"), "닫기 계약");
    await user.keyboard("{Enter}");

    const restore = screen.getByRole("button", {
      name: "계약",
      expanded: false,
    });
    await waitFor(() => expect(restore).toHaveFocus());
  });

  it("persists bounded panel sizes and rejects hostile stored layouts", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const first = mount();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    const outline = screen.getByRole("separator", {
      name: "전략 구조 크기 조절",
    });
    outline.focus();
    await user.keyboard("{ArrowRight}");
    expect(outline).toHaveAttribute("aria-valuenow", "256");
    first.unmount();

    mount();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
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
      sizes: {
        outlineWidth: 256,
        inspectorWidth: 320,
        debuggerHeight: 220,
        assistantWidth: 360,
      },
      open: { assistant: false },
    });
  });

  // 배지·문제 목록은 탭 패널 밖에 있어야 한다. 탭 패널 안에 있으면 선택되지 않은 탭은 `hidden`이라
  // Graph·Form에서 편집 결과를 보려고 YAML 탭으로 돌아가야 했다(WORKFLOW P1-01).
  it("keeps the document status and the problem list outside the tab panels", () => {
    matchMedia(false);
    for (const view of ["graph", "yaml"] as const) {
      mount({
        view,
        sourceView: "yaml",
        availableViews: ["graph", "yaml"],
        projections: {
          graph: <div>Graph projection</div>,
        },
        documentStatus: (
          <p role="status" aria-label="문서 상태">
            검증 통과
          </p>
        ),
        problems: <section aria-label="문제">오류 1 · 경고 0</section>,
      });
      expect(screen.getByRole("status", { name: "문서 상태" })).toBeVisible();
      expect(screen.getByRole("region", { name: "문제" })).toBeVisible();
      cleanup();
    }
  });
});
