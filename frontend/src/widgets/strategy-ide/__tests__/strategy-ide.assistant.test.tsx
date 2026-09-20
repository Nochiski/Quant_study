import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemePreferenceProvider } from "../../../shared/lib/theme";
import { StrategyIde } from "..";
import { PANEL_LAYOUT_STORAGE_KEY } from "../model/use-panel-layout";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
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

const mount = (props: Partial<Parameters<typeof StrategyIde>[0]> = {}) =>
  render(
    <ThemePreferenceProvider>
      <StrategyIde
        title="새 전략"
        versionLabel="초안"
        editor={<textarea aria-label="source" />}
        assistant={<p>AI 사이드바</p>}
        {...props}
      />
    </ThemePreferenceProvider>,
  );

/** 그래프·YAML 탭을 오가도 같은 사이드바가 살아 있는지 보기 위한 view 소유 래퍼. */
const StatefulIde = () => {
  const [view, setView] =
    useState<NonNullable<Parameters<typeof StrategyIde>[0]["view"]>>("yaml");
  return (
    <ThemePreferenceProvider>
      <StrategyIde
        title="새 전략"
        versionLabel="초안"
        editor={<textarea aria-label="source" />}
        sourceView="yaml"
        projections={{ graph: <div>graph projection</div> }}
        view={view}
        onViewChange={setView}
        availableViews={["yaml", "graph"]}
        assistant={<p>AI 사이드바</p>}
      />
    </ThemePreferenceProvider>
  );
};

describe("StrategyIde assistant 슬롯", () => {
  it("기본은 접힘이고, 슬롯이 없으면 패널도 토글도 만들지 않는다", () => {
    matchMedia(false);
    mount({ assistant: undefined });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("토글 버튼으로 펼치고 접으며 aria-expanded와 aria-controls가 해소된다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();

    const open = screen.getByRole("button", {
      name: "AI 어시스턴트",
      expanded: false,
    });
    expect(
      document.getElementById(open.getAttribute("aria-controls") ?? ""),
    ).not.toBeNull();
    await user.click(open);

    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    expect(within(panel).getByText("AI 사이드바")).toBeInTheDocument();
    const collapse = screen.getByRole("button", {
      name: "AI 어시스턴트 접기",
    });
    await waitFor(() => expect(collapse).toHaveFocus());
    await user.click(collapse);
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("Alt+A 단축키로 사이드바를 여닫는다", () => {
    matchMedia(false);
    mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("그래프 탭으로 바꿔도 같은 사이드바가 남는다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    render(<StatefulIde />);
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(await screen.findByText("graph projection")).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
  });

  it("폭과 펼침 상태를 저장하고 다시 열 때 복원한다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const first = mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    const handle = screen.getByRole("separator", {
      name: "AI 어시스턴트 크기 조절",
    });
    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect(handle).toHaveAttribute("aria-valuenow", "376");
    first.unmount();

    mount();
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "376");
    expect(
      JSON.parse(localStorage.getItem(PANEL_LAYOUT_STORAGE_KEY) ?? "null"),
    ).toMatchObject({ open: { assistant: true } });
  });

  it("사이드바 키가 없던 기록도 그대로 읽고 기본값만 채운다", () => {
    matchMedia(false);
    localStorage.setItem(
      PANEL_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        sizes: {
          outlineWidth: 300,
          inspectorWidth: 320,
          debuggerHeight: 220,
        },
      }),
    );
    mount();
    expect(
      screen.getByRole("separator", { name: "전략 구조 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "300");
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("저장소를 못 쓰면 기본값으로 뜨고 화면은 그대로 동작한다", () => {
    matchMedia(false);
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("저장소 접근 거부");
      },
      setItem: () => {
        throw new Error("저장소 접근 거부");
      },
    });
    mount();
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.getByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "360");
  });

  it("좁은 폭에서는 오버레이 서랍이 되고 Escape로 닫힌다", () => {
    matchMedia(true);
    mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    expect(panel.closest(".ide__drawer")).not.toBeNull();
    // 오버레이 폭은 CSS가 정하므로 인라인 폭을 주지 않는다.
    expect(panel).not.toHaveAttribute("style");
    expect(
      screen.queryByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("명령 팔레트에서도 사이드바를 여닫는다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await user.type(screen.getByRole("combobox"), "AI 어시스턴트");
    await user.keyboard("{Enter}");
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
  });
});
