import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  Badge,
  Button,
  EmptyState,
  SplitHandle,
  Tabs,
  Tooltip,
  panelId,
} from "..";

afterEach(cleanup);

const TabsHarness = ({ idBase }: { idBase?: string }) => {
  const [value, setValue] = useState<"yaml" | "json" | "diff">("yaml");
  const items = [
    { id: "yaml", label: "YAML" },
    { id: "json", label: "JSON", disabled: true },
    { id: "diff", label: "Diff" },
  ] as const;
  return (
    <>
      <Tabs
        label="표현 전환"
        value={value}
        onChange={setValue}
        items={items}
        idBase={idBase}
      />
      {idBase
        ? items.map((item) => (
            <div
              key={item.id}
              role="tabpanel"
              id={panelId(idBase, item.id)}
              hidden={item.id !== value}
            >
              {item.label} panel
            </div>
          ))
        : null}
    </>
  );
};

describe("Tabs", () => {
  it("moves selection with Left/Right, skipping disabled tabs, wraps, and ignores Down", async () => {
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

    await user.keyboard("{ArrowDown}");
    expect(yaml).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{Home}");
    expect(yaml).toHaveAttribute("aria-selected", "true");
  });

  it("keeps a single tab in the tab order and ignores clicks on disabled tabs", async () => {
    const user = userEvent.setup();
    render(<TabsHarness />);
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
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

  it("controls panels the caller renders with the shared id base", () => {
    render(<TabsHarness idBase="views" />);
    for (const tab of screen.getAllByRole("tab")) {
      const controls = tab.getAttribute("aria-controls");
      expect(document.getElementById(controls ?? "")).not.toBeNull();
    }
    expect(screen.getByRole("tabpanel")).toHaveTextContent("YAML panel");
  });
});

describe("Badge", () => {
  it("announces the status word before the text and marks it visually", () => {
    render(<Badge tone="error">2</Badge>);
    expect(screen.getByText("2", { exact: false })).toHaveTextContent("오류 2");
    render(<Badge tone="neutral">3</Badge>);
    expect(screen.getByText("3")).toHaveTextContent(/^3$/);
  });
});

describe("Tooltip", () => {
  it("shows on focus, composes aria-describedby and the child's handlers, closes on Escape", async () => {
    const user = userEvent.setup();
    const onFocus = vi.fn();
    render(
      <Tooltip content="JSON Pointer로 위치를 표시합니다">
        <Button onFocus={onFocus} aria-describedby="external-help">
          도움말
        </Button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "도움말" });
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(trigger.getAttribute("aria-describedby")).toBe(
      `external-help ${tooltip.id}`,
    );
    expect(tooltip).not.toBeVisible();

    await user.tab();
    expect(onFocus).toHaveBeenCalledTimes(1);
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
    expect(screen.getByRole("status")).toHaveTextContent(
      "저장된 전략이 없습니다",
    );
    expect(screen.getByRole("button", { name: "새 전략" })).toBeInTheDocument();
  });
});

describe("SplitHandle", () => {
  const Harness = ({
    onChange,
    invert = false,
  }: {
    onChange: (next: number) => void;
    invert?: boolean;
  }) => {
    const [value, setValue] = useState(300);
    return (
      <SplitHandle
        orientation="vertical"
        label="패널 크기 조절"
        value={value}
        min={240}
        max={320}
        step={16}
        invert={invert}
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

  it("resizes linearly with pointer drag and inverts for panes after the handle", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} invert />);
    const handle = screen.getByRole("separator", { name: "패널 크기 조절" });
    handle.setPointerCapture = vi.fn();
    handle.releasePointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn(() => true);
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 1000 });
    for (const clientX of [1010, 1020, 1030]) {
      fireEvent.pointerMove(handle, { pointerId: 1, clientX });
    }
    expect(onChange.mock.calls.map(([next]) => next)).toEqual([290, 280, 270]);
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 1030 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 1100 });
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(handle).toHaveAttribute("aria-valuenow", "270");
  });

  it("keeps Home/End absolute and arrow keys following the pane when inverted", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} invert />);
    const handle = screen.getByRole("separator", { name: "패널 크기 조절" });
    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith(316);
    await user.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith(240);
    await user.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith(320);
  });
});

describe("Button", () => {
  it("defaults to type=button", () => {
    render(<Button tone="primary">저장</Button>);
    expect(screen.getByRole("button", { name: "저장" })).toHaveAttribute(
      "type",
      "button",
    );
  });
});
