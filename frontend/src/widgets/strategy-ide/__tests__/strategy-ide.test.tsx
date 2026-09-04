import { cleanup, render, screen, within } from "@testing-library/react";
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

const mount = (props: Partial<Parameters<typeof StrategyIde>[0]> = {}) =>
  render(
    <StrategyIde
      title="새 전략"
      editor={<textarea aria-label="source" />}
      {...props}
    />,
  );

describe("StrategyIde", () => {
  it("shows the outline with every section including parameters and no top stepper", () => {
    matchMedia(false);
    mount();
    const outline = screen.getByRole("navigation", { name: "전략 구조" });
    const sections = within(outline)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(sections.some((text) => text?.includes("/parameters"))).toBe(true);
    expect(sections.some((text) => text?.includes("/risk"))).toBe(true);
    expect(
      screen.queryByRole("tablist", { name: /step|단계/i }),
    ).not.toBeInTheDocument();
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
    ).toHaveTextContent("/risk");
    await user.click(
      within(outline).getByRole("button", { name: /\/portfolio/ }),
    );
    expect(onSelectSection).toHaveBeenCalledWith("portfolio");
  });

  it("resizes panels with the keyboard and collapses/restores them", async () => {
    matchMedia(false);
    const user = userEvent.setup();
    mount();
    const outlineHandle = screen.getByRole("separator", {
      name: "전략 구조 크기 조절",
    });
    expect(outlineHandle).toHaveAttribute("aria-valuenow", "240");
    outlineHandle.focus();
    await user.keyboard("{ArrowRight}");
    expect(outlineHandle).toHaveAttribute("aria-valuenow", "256");

    const inspector = screen.getByRole("complementary", { name: "계약" });
    await user.click(within(inspector).getByRole("button", { name: "접기" }));
    expect(
      screen.queryByRole("complementary", { name: "계약" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "계약" }));
    expect(
      screen.getByRole("complementary", { name: "계약" }),
    ).toBeInTheDocument();
  });

  it("turns the inspector and debugger into drawers below 1280px", async () => {
    matchMedia(true);
    const user = userEvent.setup();
    mount();
    expect(
      screen.queryByRole("separator", { name: "계약 크기 조절" }),
    ).not.toBeInTheDocument();
    const inspectorToggle = screen.getByRole("button", {
      name: "계약",
      expanded: true,
    });
    expect(screen.getByRole("dialog", { name: "계약" })).toBeInTheDocument();
    await user.click(inspectorToggle);
    expect(
      screen.queryByRole("dialog", { name: "계약" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "계약", expanded: false }),
    ).toBeInTheDocument();
  });
});
