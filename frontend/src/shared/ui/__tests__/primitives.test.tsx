import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Badge, Button, EmptyState, SplitHandle, Tabs, Tooltip } from "..";

afterEach(cleanup);

const TabsHarness = () => {
  const [value, setValue] = useState<"yaml" | "json" | "diff">("yaml");
  return (
    <Tabs
      label="표현 전환"
      value={value}
      onChange={setValue}
      items={[
        { id: "yaml", label: "YAML" },
        { id: "json", label: "JSON", disabled: true },
        { id: "diff", label: "Diff" },
      ]}
    />
  );
};

describe("Tabs", () => {
  it("moves selection with arrow keys, skipping disabled tabs, and wraps around", async () => {
    const user = userEvent.setup();
    render(<TabsHarness />);
    const yaml = screen.getByRole("tab", { name: "YAML" });
    yaml.focus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(yaml).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("tablist", { name: "표현 전환" }),
    ).toBeInTheDocument();
  });

  it("selects on click and keeps a single tab in the tab order", async () => {
    const user = userEvent.setup();
    render(<TabsHarness />);
    await user.click(screen.getByRole("tab", { name: "Diff" }));
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "tabindex",
      "0",
    );
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
  });
});

describe("Badge", () => {
  it("marks status with a glyph in addition to colour", () => {
    render(<Badge tone="error">오류 2</Badge>);
    const badge = screen.getByText("오류 2");
    expect(badge).toHaveAttribute("data-tone", "error");
    expect(badge.querySelector(".ui-badge__glyph")).toHaveTextContent("✕");
  });
});

describe("Tooltip", () => {
  it("shows on focus, is referenced by aria-describedby, and closes on Escape", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="JSON Pointer로 위치를 표시합니다">
        <Button>도움말</Button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "도움말" });
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);
    expect(tooltip).not.toBeVisible();

    await user.tab();
    expect(tooltip).toBeVisible();
    await user.keyboard("{Escape}");
    expect(tooltip).not.toBeVisible();
    expect(trigger).toHaveFocus();
  });
});

describe("EmptyState", () => {
  it("announces as a status region with title, description and action", () => {
    render(
      <EmptyState
        title="저장된 전략이 없습니다"
        description="새 전략을 만들어 시작하세요."
        action={<Button tone="primary">새 전략</Button>}
      />,
    );
    const region = screen.getByRole("status");
    expect(region).toHaveTextContent("저장된 전략이 없습니다");
    expect(screen.getByRole("button", { name: "새 전략" })).toBeInTheDocument();
  });
});

describe("SplitHandle", () => {
  const Harness = ({ onChange }: { onChange: (next: number) => void }) => {
    const [value, setValue] = useState(300);
    return (
      <SplitHandle
        orientation="vertical"
        label="패널 크기 조절"
        value={value}
        min={240}
        max={320}
        step={16}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    );
  };

  it("is a keyboard-operable separator that clamps to its bounds", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const handle = screen.getByRole("separator", { name: "패널 크기 조절" });
    expect(handle).toHaveAttribute("aria-valuenow", "300");
    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith(316);
    await user.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith(320);
    expect(handle).toHaveAttribute("aria-valuenow", "320");
    await user.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith(240);
    await user.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenCalledTimes(3);
  });
});

describe("Button", () => {
  it("defaults to type=button and keeps legacy and tokenised classes", () => {
    render(<Button tone="primary">저장</Button>);
    const button = screen.getByRole("button", { name: "저장" });
    expect(button).toHaveAttribute("type", "button");
    expect(button.className).toContain("button--primary");
    expect(button.className).toContain("ui-button--primary");
  });
});
