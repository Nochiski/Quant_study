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
import { useEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemePreferenceProvider } from "../../../shared/lib/theme";
import { StrategyIde } from "..";
import { ASSISTANT_WINDOW_STORAGE_KEY } from "../../../entities/assistant";
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

/**
 * 이 파일의 시나리오는 "접힌 AI 열을 연다"에서 시작한다. AI 열은 기록이 없으면 열린 채로 뜨므로
 * (RD-04-A) 사용자가 접어 둔 기록을 먼저 심는다. 기본값(열림)과 기록 존중은 아래 전용 시나리오가 본다.
 */
const storeClosedAssistant = () =>
  localStorage.setItem(
    PANEL_LAYOUT_STORAGE_KEY,
    JSON.stringify({
      version: 1,
      sizes: { outlineWidth: 240, inspectorWidth: 320, debuggerHeight: 220 },
      open: { assistant: false },
    }),
  );

beforeEach(storeClosedAssistant);

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
        outline={null}
        snippets={null}
        inspector={null}
        debugger={null}
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
        outline={null}
        snippets={null}
        inspector={null}
        debugger={null}
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

const responsiveMedia = (initialWidth: number) => {
  let width = initialWidth;
  const listeners = new Map<(event: MediaQueryListEvent) => void, string>();
  const matches = (query: string) => width <= Number(query.match(/max-width:\s*(\d+)px/)?.[1] ?? 0);
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    get matches() { return matches(query); },
    media: query,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.set(listener, query),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
  })));
  return { resize: (next: number) => act(() => {
    width = next;
    [...listeners].forEach(([listener, query]) => listener({ matches: matches(query), media: query } as MediaQueryListEvent));
  }) };
};

const StatefulAssistant = ({ mounted, unmounted }: { mounted: () => void; unmounted: () => void }) => {
  const [text, setText] = useState("");
  const [session, setSession] = useState("recent");
  useEffect(() => { mounted(); return unmounted; }, [mounted, unmounted]);
  return <>
    <label>미전송 질문<input value={text} onChange={(event) => setText(event.target.value)} /></label>
    <label>선택 대화<select value={session} onChange={(event) => setSession(event.target.value)}><option value="recent">최근 대화</option><option value="earlier">이전 대화</option></select></label>
  </>;
};

describe("StrategyIde assistant 슬롯", () => {
  it.each([1440, 960, 360])("%ipx에서 편집·추적 왕복은 AI 인스턴스와 미전송 문장·선택 대화를 보존한다", async (width) => {
    responsiveMedia(width);
    const user = userEvent.setup();
    const mounted = vi.fn();
    const unmounted = vi.fn();
    mount({ assistant: <StatefulAssistant mounted={mounted} unmounted={unmounted} /> });
    expect(mounted).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }));
    const input = screen.getByRole("textbox", { name: "미전송 질문" });
    await user.type(input, "원천 값을 비교할 질문");
    await user.selectOptions(screen.getByRole("combobox", { name: "선택 대화" }), "earlier");
    await user.click(screen.getByRole("button", { name: "중간 결과" }));
    // 떠 있는 창은 추적 작업공간으로 가도 닫히지 않는다.
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "미전송 질문" })).toBe(input);
    expect(input).toHaveValue("원천 값을 비교할 질문");
    expect(screen.getByRole("combobox", { name: "선택 대화" })).toHaveValue("earlier");
    await user.click(screen.getByRole("button", { name: "전략 편집" }));
    expect(screen.getByRole("textbox", { name: "미전송 질문" })).toBe(input);
    expect(input).toHaveValue("원천 값을 비교할 질문");
    expect(screen.getByRole("combobox", { name: "선택 대화" })).toHaveValue("earlier");
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(unmounted).not.toHaveBeenCalled();
  });

  it("슬롯은 지금 펼쳤는지(open)를 받는다 — 접어도 마운트된 내용이 조회를 멈출 수 있다(5차 리뷰 9)", () => {
    mount({ assistant: ({ open }) => <p>{open ? "슬롯 열림" : "슬롯 접힘"}</p> });
    // 이 테스트 환경의 기본은 접힘이고, 한 번도 펼치지 않은 내용은 마운트하지 않는다.
    expect(screen.queryByText(/^슬롯/u)).toBeNull();

    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(screen.getByText("슬롯 열림")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(screen.getByText("슬롯 접힘")).toBeInTheDocument();
  });

  it("화면 폭을 왕복해도 AI 인스턴스와 작성 중 문장을 유지한다", async () => {
    const media = responsiveMedia(1440);
    const user = userEvent.setup();
    const mounted = vi.fn();
    const unmounted = vi.fn();
    mount({ assistant: <StatefulAssistant mounted={mounted} unmounted={unmounted} /> });
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }));
    const input = screen.getByRole("textbox", { name: "미전송 질문" });
    await user.type(input, "다른 화면에서도 이어 쓸 질문");
    await user.selectOptions(screen.getByRole("combobox", { name: "선택 대화" }), "earlier");
    for (const width of [960, 360, 1440]) {
      media.resize(width);
      if (screen.queryByRole("complementary", { name: "AI 어시스턴트" }) === null) await user.click(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }));
      expect(screen.getByRole("textbox", { name: "미전송 질문" })).toBe(input);
      expect(input).toHaveValue("다른 화면에서도 이어 쓸 질문");
      expect(screen.getByRole("combobox", { name: "선택 대화" })).toHaveValue("earlier");
    }
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(unmounted).not.toHaveBeenCalled();
  });

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

  it("떠 있는 창은 최소화 버튼을 그리고, 최소화하면 우하단 런처로 초점이 돌아온다", async () => {
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
    await user.click(open);

    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    expect(within(panel).getByText("AI 사이드바")).toBeInTheDocument();
    await waitFor(() => expect(panel).toHaveFocus());
    await user.click(within(panel).getByRole("button", { name: "AI 어시스턴트 접기" }));
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false })).toHaveFocus(),
    );
  });

  it("펼치면 포커스가 떠 있는 창으로 간다 — 런처는 스스로 사라진다", async () => {
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

    await user.click(screen.getByRole("tab", { name: "그래프" }));
    expect(await screen.findByText("graph projection")).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();
  });

  it("펼침 상태를 패널 배치에 저장하고, 창 위치·크기는 공용 창 키에 저장한다", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    const first = mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    // 펼침은 초점을 창으로 옮긴다(microtask). 그 뒤에 옮기기 손잡이를 잡는다.
    await waitFor(() => expect(panel).toHaveFocus());
    within(panel).getByRole("button", { name: "창 옮기기" }).focus();
    await user.keyboard("{ArrowLeft}");
    const left = panel.style.left;
    first.unmount();

    mount();
    const again = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    expect(again.style.left).toBe(left);
    expect(
      JSON.parse(localStorage.getItem(PANEL_LAYOUT_STORAGE_KEY) ?? "null"),
    ).toMatchObject({ open: { assistant: true } });
    expect(localStorage.getItem(ASSISTANT_WINDOW_STORAGE_KEY)).not.toBeNull();
  });

  it("사이드바 키가 없던 기록도 그대로 읽고 기본값만 채운다", async () => {
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
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "전략 구조", expanded: false }));
    expect(
      screen.getByRole("separator", { name: "전략 구조 크기 조절" }),
    ).toHaveAttribute("aria-valuenow", "300");
    // 기록에 AI 키가 없으면 새 기본값(열림)을 쓴다(RD-04-A).
    expect(
      screen.getByRole("complementary", { name: "AI 어시스턴트" }),
    ).toBeVisible();
  });

  it("처음 연 편집기는 AI 창을 열어 두고, 접어 둔 기록은 그대로 존중한다 — 옛 열 폭 기록은 무시한다", async () => {
    matchMedia(false);
    localStorage.clear();
    const user = userEvent.setup();
    const first = mount();
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
    // AI 는 더 이상 본문 열이 아니다 — 열 폭 조절 손잡이가 없다.
    expect(
      screen.queryByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    first.unmount();

    mount();
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "AI 어시스턴트", expanded: false }));
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
  });

  it("옛 판의 AI 열 폭 기록이 있어도 깨지지 않고 열린다", () => {
    matchMedia(false);
    localStorage.setItem(
      PANEL_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        sizes: {
          outlineWidth: 240,
          inspectorWidth: 320,
          debuggerHeight: 220,
          assistantWidth: 400,
        },
        open: { assistant: true },
      }),
    );
    mount();
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
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
    // 기록을 읽지 못하면 기본값이다: AI 창이 열려 있다.
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(
      screen.queryByRole("complementary", { name: "AI 어시스턴트" }),
    ).not.toBeInTheDocument();
  });

  it("좁은 폭에서도 AI 는 떠 있는 창이고, Escape 는 창을 닫지 않는다", () => {
    matchMedia(true);
    mount();
    fireEvent.keyDown(window, { key: "a", altKey: true });
    const panel = screen.getByRole("complementary", { name: "AI 어시스턴트" });
    expect(panel.closest(".ide__drawer")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
  });

  it("좁은 화면에서 계약 서랍을 열어 둔 채 AI 창을 열어도 서랍은 그대로다(창은 자리를 다투지 않는다)", async () => {
    matchMedia(true);
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "계약" }));
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(screen.getByRole("complementary", { name: "AI 어시스턴트" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "계약" })).toBeInTheDocument();
  });

  it("슬롯이 손잡이를 받으면 슬롯의 닫기로도 접고, 창 제목은 위젯이 그린다", async () => {
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
    // 제목은 창 제목 줄이 그린다 — 슬롯 내용은 이름 없는 section이라 landmark 이름이 여기서만 나온다.
    expect(
      within(panel).getByRole("heading", { name: "AI 어시스턴트" }),
    ).toBeInTheDocument();

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
