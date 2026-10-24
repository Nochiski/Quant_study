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
    await user.selectOptions(within(windowBox()).getByRole("combobox", { name: "창 크기" }), "small");
    expect(windowBox().style.width).toBe("320px");
    unmount();
    render(<Harness initiallyOpen />);
    expect(windowBox().style.width).toBe("320px");
  });

  it("제목 줄을 끌어 옮기되 뷰포트 밖으로는 나가지 않는다", () => {
    render(<Harness initiallyOpen />);
    const bar = within(windowBox()).getByRole("heading", { name: "AI 도우미" }).parentElement!;
    fireEvent.pointerDown(bar, { pointerId: 1, button: 0, clientX: 500, clientY: 300 });
    fireEvent.pointerMove(bar, { pointerId: 1, clientX: -5_000, clientY: -5_000 });
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
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
      fireEvent(window, new Event("resize"));
      const right = Number.parseFloat(windowBox().style.left) + Number.parseFloat(windowBox().style.width);
      expect(right).toBeLessThanOrEqual(500 - 8);
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth });
    }
  });
});
