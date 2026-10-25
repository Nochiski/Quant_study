import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { FloatingWindow } from "../floating-window";

const KEY = "test.floating-window";

const Harness = ({ initiallyOpen = false }: { initiallyOpen?: boolean }) => {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <FloatingWindow
      title="AI 도우미"
      open={open}
      onOpenChange={setOpen}
      storageKey={KEY}
      launcherLabel="AI 도우미 열기"
      minimizeLabel="AI 도우미 최소화"
    >
      <label>
        질문 <input />
      </label>
    </FloatingWindow>
  );
};

const windowBox = () => screen.getByRole("complementary", { name: "AI 도우미" });

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("FloatingWindow", () => {
  it("처음 열 때 본문을 마운트하고, 최소화하면 런처로 초점이 돌아오며 본문 입력은 남는다", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole("complementary", { name: "AI 도우미" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "질문" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "AI 도우미 열기" }));
    expect(windowBox()).toHaveFocus();
    await user.type(screen.getByRole("textbox", { name: "질문" }), "남을 문장");

    await user.click(within(windowBox()).getByRole("button", { name: "AI 도우미 최소화" }));
    const launcher = screen.getByRole("button", { name: "AI 도우미 열기" });
    expect(launcher).toHaveFocus();
    await user.click(launcher);
    expect(screen.getByRole("textbox", { name: "질문" })).toHaveValue("남을 문장");
  });

  it("옮기기 손잡이의 화살표 키로 옮기고 위치를 기억한다", async () => {
    const user = userEvent.setup();
    render(<Harness initiallyOpen />);
    const before = Number.parseFloat(windowBox().style.left);
    within(windowBox()).getByRole("button", { name: "창 옮기기" }).focus();
    await user.keyboard("{ArrowLeft}");
    expect(Number.parseFloat(windowBox().style.left)).toBe(before - 16);
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(Number.parseFloat(windowBox().style.left)).toBe(before - 16 - 64);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ x: before - 80 });
  });

  it("크기 프리셋으로 크기를 바꾸고, 다시 열면 기억한 자리·크기로 연다", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness initiallyOpen />);
    const sizes = within(windowBox()).getByRole("group", { name: "창 크기" });
    await user.click(within(sizes).getByRole("button", { name: "작게" }));
    expect(windowBox().style.width).toBe("320px");
    expect(within(sizes).getByRole("button", { name: "작게" })).toHaveAttribute("aria-pressed", "true");
    // 같은 프리셋을 다시 눌러도 다시 맞춘다(선택 상자처럼 마지막 값에 묶이지 않는다, 한정 리뷰 8).
    within(windowBox()).getByRole("button", { name: "창 옮기기" }).focus();
    await user.keyboard("{ArrowLeft}");
    await user.click(within(sizes).getByRole("button", { name: "크게" }));
    await user.click(within(sizes).getByRole("button", { name: "작게" }));
    expect(windowBox().style.width).toBe("320px");
    unmount();
    render(<Harness initiallyOpen />);
    expect(windowBox().style.width).toBe("320px");
  });

  it("제목 줄을 끌어 옮기되 뷰포트 밖으로는 나가지 않는다", () => {
    render(<Harness initiallyOpen />);
    const bar = within(windowBox()).getByRole("heading", { name: "AI 도우미" }).parentElement!;
    fireEvent.pointerDown(bar, { pointerId: 1, button: 0, buttons: 1, clientX: 500, clientY: 300 });
    fireEvent.pointerMove(bar, { pointerId: 1, buttons: 1, clientX: -5_000, clientY: -5_000 });
    fireEvent.pointerUp(bar, { pointerId: 1, clientX: -5_000, clientY: -5_000 });
    expect(windowBox().style.left).toBe("8px");
    expect(windowBox().style.top).toBe("8px");
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ x: 8, y: 8 });
  });

  it("브라우저 창이 줄면 창을 뷰포트 안으로 보정하고, 손상된 저장값은 버린다", () => {
    localStorage.setItem(KEY, "{\"x\":\"bad\"}");
    render(<Harness initiallyOpen />);
    expect(Number.parseFloat(windowBox().style.left)).toBeGreaterThanOrEqual(8);
    const innerWidth = window.innerWidth;
    try {
      // 시트가 되지 않는 폭(768 이상)으로 줄인다.
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
      fireEvent(window, new Event("resize"));
      const right = Number.parseFloat(windowBox().style.left) + Number.parseFloat(windowBox().style.width);
      expect(right).toBeLessThanOrEqual(900 - 8);
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth });
    }
  });

  it("좁은 화면에서는 하단 시트(폭 100%)이고 끌어 옮기기·크기 조절이 없다", () => {
    const innerWidth = window.innerWidth;
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      render(<Harness initiallyOpen />);
      expect(windowBox().style.width).toBe("390px");
      expect(windowBox().style.left).toBe("0px");
      expect(within(windowBox()).queryByRole("button", { name: "창 옮기기" })).toBeNull();
      expect(within(windowBox()).queryByRole("group", { name: "창 크기" })).toBeNull();
      expect(within(windowBox()).getByRole("button", { name: "AI 도우미 최소화" })).toBeVisible();
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth });
    }
  });

  it("창 크기 보정은 표시만 바꾸고 기억한 자리를 덮지 않는다 — 다시 넓어지면 돌아오고 보정값은 저장하지 않는다", () => {
    localStorage.setItem(KEY, JSON.stringify({ x: 600, y: 100, width: 400, height: 500 }));
    const innerWidth = window.innerWidth;
    try {
      render(<Harness initiallyOpen />);
      expect(windowBox().style.left).toBe("600px");
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
      fireEvent(window, new Event("resize"));
      expect(windowBox().style.left).toBe(`${900 - 8 - 400}px`);
      expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ x: 600 });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1200 });
      fireEvent(window, new Event("resize"));
      expect(windowBox().style.left).toBe("600px");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth });
    }
  });

  it("움직이지 않은 클릭은 저장하지 않고, 옮기기 손잡이를 끌어도 옮기며, 포인터를 놓치면 끌기가 끝난다(한정 리뷰 4·5·9)", () => {
    render(<Harness initiallyOpen />);
    const bar = within(windowBox()).getByRole("heading", { name: "AI 도우미" }).parentElement!;
    fireEvent.pointerDown(bar, { pointerId: 1, button: 0, buttons: 1, clientX: 500, clientY: 300 });
    fireEvent.pointerUp(bar, { pointerId: 1, clientX: 500, clientY: 300 });
    expect(localStorage.getItem(KEY)).toBeNull();

    const handle = within(windowBox()).getByRole("button", { name: "창 옮기기" });
    const before = Number.parseFloat(windowBox().style.left);
    fireEvent.pointerDown(handle, { pointerId: 2, button: 0, buttons: 1, clientX: 500, clientY: 300 });
    fireEvent.pointerMove(handle, { pointerId: 2, buttons: 1, clientX: 400, clientY: 300 });
    expect(Number.parseFloat(windowBox().style.left)).toBe(before - 100);
    // 창 밖에서 놓아 놓기를 못 받았다 — 버튼이 떨어진 이동이 오면 끝낸다.
    fireEvent.pointerMove(handle, { pointerId: 2, buttons: 0, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(handle, { pointerId: 2, buttons: 1, clientX: 200, clientY: 300 });
    expect(Number.parseFloat(windowBox().style.left)).toBe(before - 100);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ x: before - 100 });
  });
});
