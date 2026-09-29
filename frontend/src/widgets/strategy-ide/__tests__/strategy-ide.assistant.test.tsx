import {
  act,
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
  vi.restoreAllMocks();
  document.head
    .querySelectorAll("style[data-ide-layout]")
    .forEach((style) => style.remove());
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

/** 1440px 창에서 전략 구조 240 + 계약 320 + 사이드바 기본 360 + 편집기 480에 손잡이 셋(18)을 더한 폭. */
const SIDE_BY_SIDE = 1418;

/**
 * 본문(`.ide__body`) 배치를 흉내 낸다. jsdom에는 배치가 없어, 편집기 최소 폭 판정(#269)이 읽는 폭을
 * 여기서 준다. `width`는 페이지 세로 스크롤바가 없을 때 좌우 패널이 나눠 갖는 폭이고, `scrollbar`는 지금
 * 스크롤바가 차지한 폭이다. 관찰 알림은 브라우저처럼 첫 칠 뒤에 오므로 `notify`로 따로 보낸다.
 */
const ideLayout = (width: number, scrollbar: () => number = () => 0) => {
  const viewport = 1440;
  const padding = 24;
  // 여백은 스타일시트가 준다 — 테스트는 컴포넌트 CSS를 싣지 않는다.
  const style = document.createElement("style");
  style.dataset.ideLayout = "";
  style.textContent = `.ide__body { padding: 0 ${padding}px; }`;
  document.head.append(style);
  vi.stubGlobal("innerWidth", viewport);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    function (this: HTMLElement) {
      if (this === document.documentElement) return viewport - scrollbar();
      return this.classList.contains("ide__body")
        ? width + 2 * padding - scrollbar()
        : 0;
    },
  );
  const observers = new Set<() => void>();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      readonly #notify: () => void;
      constructor(notify: ResizeObserverCallback) {
        this.#notify = () =>
          notify(
            [
              {
                contentRect: { width: width - scrollbar() },
              } as unknown as ResizeObserverEntry,
            ],
            this as unknown as ResizeObserver,
          );
      }
      observe() {
        observers.add(this.#notify);
      }
      unobserve() {}
      disconnect() {
        observers.delete(this.#notify);
      }
    },
  );
  return {
    notify: () => act(() => observers.forEach((notify) => notify())),
  };
};

const assistantFloating = () =>
  screen
    .getByRole("complementary", { name: "AI 어시스턴트" })
    .closest(".ide__drawer") !== null;

/** 제품과 같은 모양의 슬롯: 손잡이를 받아 자기 닫기를 그리는 함수. */
const sidebarSlot = ({ close }: { close: () => void }) => (
  <section>
    <button type="button" onClick={close}>
      사이드바 닫기
    </button>
    <p>AI 사이드바</p>
  </section>
);

const mount = (props: Partial<Parameters<typeof StrategyIde>[0]> = {}) =>
  render(
    <ThemePreferenceProvider>
      <StrategyIde
        title="새 전략"
        versionLabel="초안"
        editor={<textarea aria-label="source" />}
        assistant={sidebarSlot}
        {...props}
      />
    </ThemePreferenceProvider>,
  );

/** 접기 버튼을 위젯이 그리는 쪽(슬롯이 평범한 노드). */
const mountNodeSlot = (props: Partial<Parameters<typeof StrategyIde>[0]> = {}) =>
  mount({ assistant: <p>AI 사이드바</p>, ...props });

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

  it("노드 슬롯이면 위젯이 접기 버튼을 그리고 거기로 포커스를 넘긴다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mountNodeSlot();
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

  it("펼치면 포커스가 사이드바 패널로 간다 — 상단 토글은 스스로 사라진다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트" }));

    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    await waitFor(() => expect(panel).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
  });

  it("명령 팔레트로 펼쳐도 포커스가 사이드바 패널로 간다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await user.type(screen.getByRole("combobox"), "AI 어시스턴트");
    await user.keyboard("{Enter}");

    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    await waitFor(() => expect(panel).toHaveFocus());
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
    // 펼침은 포커스를 패널로 옮긴다(microtask). 그 뒤에 핸들을 잡는다.
    const handle = await screen.findByRole("separator", {
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
    // 1440px 창이라 좁은 화면 규칙은 아니지만, 셸 사이드바·여백을 뺀 본문 1204px은 패널 셋과 편집기
    // 최소 폭을 나란히 담는 1418px보다 좁다. 뷰포트로 재면 담는 것으로 보여 편집기가 266px로 눌렸다(#269).
    matchMedia(false);
    ideLayout(1204);
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
    // 서랍은 붙어 있는 계약 자리만 덮는다 — 계약 폭(기본 320)을 따른다(#290 리뷰 P2-1).
    expect(assistant.closest(".ide__drawer")).toHaveStyle({ width: "320px" });
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
    ).toHaveStyle({ width: "360px" });
    expect(assistantFloating()).toBe(false);
  });

  it("편집기 최소 폭 480px에 손잡이 폭을 넣지 않는다", async () => {
    // 패널 셋과 편집기 480을 담고도 손잡이 몫이 1px 모자란 본문. 손잡이를 480에 넣어 재면 붙어서
    // 편집기가 462px까지 내려갔다(#290 리뷰 P3-3).
    matchMedia(false);
    ideLayout(SIDE_BY_SIDE - 1);
    const user = userEvent.setup();
    mount();
    await user.click(
      screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }),
    );
    expect(assistantFloating()).toBe(true);
  });

  it("첫 칠 전에 폭을 재어, 열어 둔 사이드바가 처음부터 겹쳐 뜬다", () => {
    // 관찰 알림은 칠한 뒤에야 온다(여기서는 `notify`를 부르지 않는다). 첫 칠 전 측정이 없으면 폭을
    // 모르는 배치(붙은 사이드바)로 먼저 그린다(#290 리뷰 P3-2).
    matchMedia(false);
    localStorage.setItem(
      PANEL_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        sizes: { outlineWidth: 240, inspectorWidth: 320, debuggerHeight: 220 },
        open: { assistant: true },
      }),
    );
    ideLayout(1204);
    mount();
    expect(assistantFloating()).toBe(true);
  });

  it("페이지 스크롤바가 켜지고 꺼져도 사이드바 자리가 흔들리지 않는다", async () => {
    // 사이드바를 붙이면 페이지가 길어져 세로 스크롤바(15px)가 본문 폭을 먹고, 띄우면 스크롤바가 사라지는
    // 창이다. 판정이 스크롤바를 뺀 폭을 읽으면 관찰 알림마다 붙었다 떴다를 되풀이했다(#290 리뷰 P1-1).
    matchMedia(false);
    const layout = ideLayout(SIDE_BY_SIDE + 7, () =>
      document.querySelector(".ide__body .ide__assistant") === null ? 0 : 15,
    );
    const user = userEvent.setup();
    mount();
    await user.click(
      screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }),
    );
    const placements = [assistantFloating()];
    for (let round = 0; round < 3; round += 1) {
      layout.notify();
      placements.push(assistantFloating());
    }
    expect(placements).toEqual([false, false, false, false]);
  });

  it("넓은 화면에서는 둘 다 자리에 박혀 있다", () => {
    matchMedia(false);
    // 1920px 창의 본문 폭.
    ideLayout(1684);
    mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();
  });

  it("슬롯이 요소를 여럿 넘겨도 본문 래퍼 하나가 패널 높이를 갖는다", () => {
    matchMedia(false);
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
    matchMedia(true);
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
    // 본문이 패널 셋과 편집기 최소 폭을 딱 담는다.
    matchMedia(false);
    ideLayout(SIDE_BY_SIDE);
    const user = userEvent.setup();
    const first = mount();
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트" }));
    const handle = () =>
      screen.getByRole("separator", { name: "AI 어시스턴트 크기 조절" });
    handle().focus();

    // 기본 폭을 넘겨 넓혀도 판정은 기본 폭으로 한다. 지금 폭으로 재면 여기서 오버레이로 바뀌어 핸들이
    // 사라지고 드래그가 끊겼다(리뷰 P1-3).
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(handle()).toHaveAttribute("aria-valuenow", "408");
    expect(
      screen
        .getByRole("complementary", { name: "AI 어시스턴트" })
        .closest(".ide__drawer"),
    ).toBeNull();

    // 넓힌 폭은 저장되고, 다음 방문에도 고정 패널로 열린다.
    await user.keyboard("{ArrowLeft}");
    expect(handle()).toHaveAttribute("aria-valuenow", "392");
    first.unmount();

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

  it("슬롯이 손잡이를 받으면 닫기는 슬롯이 그리고 패널 제목은 슬롯 헤더가 그린다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount({
      assistant: ({ close }) => (
        <section>
          <button type="button" onClick={close}>
            사이드바 닫기
          </button>
        </section>
      ),
    });
    fireEvent.keyDown(window, { key: "a", altKey: true });

    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    // 제목은 슬롯 헤더가 그린다 — 슬롯 내용은 이름 없는 section이라 landmark 이름이 여기서만 나온다.
    expect(
      within(panel).getByRole("heading", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
    // 닫기는 한 곳만: 슬롯이 그리므로 패널 헤더의 접기 버튼은 없다.
    expect(
      screen.queryByRole("button", { name: "AI 어시스턴트 접기" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "사이드바 닫기" }));
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }),
      ).toHaveFocus(),
    );
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
