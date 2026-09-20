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

/** 질의마다 다른 답을 주는 matchMedia. 좁은 화면(narrow)과 편집기 최소 폭 질의를 따로 흉내 낸다. */
const matchMediaBy = (matches: (query: string) => boolean) =>
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: matches(query),
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

  it("편집기가 최소 폭 아래로 내려가면 나중에 연 패널을 오버레이로 돌린다", async () => {
    // 1279px 초과라 좁은 화면 규칙은 아니지만, 계약 320 + 사이드바 360 + 편집기 480을 담지 못한다.
    matchMediaBy((query) => query !== "(max-width: 1279px)");
    const user = userEvent.setup();
    mount();
    // 계약은 기본 펼침이고 자리에 박혀 있다.
    expect(
      screen.getByRole("complementary", { name: "계약" }).closest(".ide__drawer"),
    ).toBeNull();

    await user.click(
      screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }),
    );
    const assistant = screen.getByRole("complementary", {
      name: "AI 어시스턴트",
    });
    expect(assistant.closest(".ide__drawer")).not.toBeNull();
    expect(
      screen.getByRole("complementary", { name: "계약" }).closest(".ide__drawer"),
    ).toBeNull();
    expect(
      screen.queryByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).not.toBeInTheDocument();

    // Escape는 떠 있는 패널만 닫는다.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();

    // 계약을 닫았다가 다시 열면 이번에는 계약이 나중에 연 패널이다.
    await user.click(screen.getByRole("button", { name: "계약 접기" }));
    fireEvent.keyDown(window, { key: "a", altKey: true });
    await user.click(screen.getByRole("button", { name: "계약", expanded: false }));
    expect(
      screen.getByRole("complementary", { name: "계약" }).closest(".ide__drawer"),
    ).not.toBeNull();
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();
  });

  it("넓은 화면에서는 둘 다 자리에 박혀 있다", () => {
    matchMediaBy(() => false);
    mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();
  });

  it("슬롯이 요소를 여럿 넘겨도 본문 래퍼 하나가 패널 높이를 갖는다", () => {
    matchMediaBy(() => false);
    mount({
      assistant: (
        <>
          <p>알림 한 줄</p>
          <section>채팅</section>
        </>
      ),
    });
    fireEvent.keyDown(window, { key: "a", altKey: true });
    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    // 패널 직계 자식은 헤더와 본문 래퍼 둘뿐이다(리뷰 P1-1: fragment 자식이 높이를 나눠 가졌다).
    expect([...panel.children].map((child) => child.className)).toEqual([
      "ide__panel-header",
      "ide__assistant-body",
    ]);
  });

  it("좁은 화면에서는 우측 서랍이 한 번에 하나만 뜬다", async () => {
    matchMediaBy(() => true);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "계약" }));
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
    expect(
      document.querySelectorAll(
        ".ide__drawer:not(.ide__drawer--bottom):not([hidden])",
      ),
    ).toHaveLength(1);
  });

  it("폭 조절이 오버레이 판정을 바꾸지 않아 핸들이 남는다", async () => {
    // 1440px 화면: 전략 구조 240 + 계약 320 + 사이드바 기본 360 + 편집기 480 = 1399px 이하만 좁다.
    const asked: string[] = [];
    matchMediaBy((query) => {
      asked.push(query);
      return false;
    });
    const user = userEvent.setup();
    const first = mount();
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트" }));
    const handle = () =>
      screen.getByRole("separator", { name: "AI 어시스턴트 크기 조절" });
    handle().focus();
    const before = new Set(asked);

    // 예전 판정이 임계로 삼던 401px을 넘어간다(리뷰 P1-3: 여기서 핸들이 사라지고 드래그가 끊겼다).
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(handle()).toHaveAttribute("aria-valuenow", "408");
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();
    // 질의 문자열이 폭을 타지 않는다 — 드래그가 판정을 바꿀 수 없다.
    expect(new Set(asked)).toEqual(before);
    expect([...before].filter((query) => query.includes("1399"))).not.toEqual(
      [],
    );

    // 넓힌 폭은 저장되고, 다음 방문에도 고정 패널로 열린다.
    await user.keyboard("{ArrowLeft}");
    expect(handle()).toHaveAttribute("aria-valuenow", "392");
    first.unmount();

    matchMediaBy(() => false);
    mount();
    // 펼침 상태도 저장되므로 다시 열 필요가 없다.
    expect(
      screen.getByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "392");
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();
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
