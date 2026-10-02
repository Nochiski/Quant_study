import { useEffect, useRef } from "react";
import { useRevealSelection } from "../model/use-reveal-selection";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ElkNode } from "elkjs/lib/elk-api";
import type { CanvasProjection } from "../model/canvas-projection";
import { NodeCanvas } from "../ui/node-canvas";

const layout = vi.hoisted(() => vi.fn());
vi.mock("elkjs/lib/elk-api", () => ({
  default: class {
    layout = async (graph: ElkNode) =>
      layout(graph) ?? {
        ...graph,
        children: graph.children?.map((node, i) => ({
          ...node,
          x: i * 300 + 24,
          y: 24,
        })),
      };
    terminateWorker = vi.fn();
  },
}));
afterEach(() => {
  cleanup();
  layout.mockReset();
  vi.restoreAllMocks();
});
const graph: CanvasProjection = {
  nodes: [
    {
      key: "id:a",
      nodeId: "a",
      pointer: "/factors/0/graph/nodes/0",
      label: "1. 종가",
      connectable: true,
      inputs: [],
    },
    {
      key: "id:b",
      nodeId: "b",
      pointer: "/factors/0/graph/nodes/1",
      label: "2. 평균",
      connectable: true,
      inputs: [{ key: "input_node_id", label: "입력 노드", sourceId: "a" }],
    },
  ],
  edges: [{ from: "id:a", to: "id:b", input: "input_node_id" }],
};
const setup = () => ({
  graph,
  documentKey: 1,
  sourceVersion: 0,
  factorIndex: 0,
  factorKey: "id:factor",
  active: true,
  busy: false,
  selectedPointer: null,
  diagnostics: [],
  onSelect: vi.fn(),
  onInspect: vi.fn(),
  onRemove: vi.fn(),
  onCopy: vi.fn(),
  onConnect: vi.fn(),
});
const output = () => screen.getByRole("button", { name: "1. 종가 · 출력" });
const input = () =>
  screen.getByRole("button", { name: "2. 평균 · 입력 노드 · 1. 종가" });

describe("node canvas keyboard and identity", () => {
  it("navigates, opens the inspector and delegates guarded deletion without using source IDs as names", async () => {
    const user = userEvent.setup(),
      props = setup();
    render(<NodeCanvas {...props} />);
    const first = screen.getByRole("button", { name: "노드 편집: 1. 종가" });
    first.focus();
    await user.keyboard("{ArrowRight}{Enter}{Delete}");
    expect(
      screen.getByRole("button", { name: "노드 편집: 2. 평균" }),
    ).toHaveFocus();
    expect(props.onInspect).toHaveBeenCalledWith(graph.nodes[1].pointer);
    expect(props.onRemove).toHaveBeenCalledWith(
      graph.nodes[1].pointer,
      "2. 평균",
    );
  });
  it("connects with keyboard and cancels a pending wire with Escape", async () => {
    const user = userEvent.setup(),
      props = setup();
    render(<NodeCanvas {...props} />);
    output().focus();
    await user.keyboard("{Enter}");
    input().focus();
    await user.keyboard("{Enter}");
    expect(props.onConnect).toHaveBeenCalledExactlyOnceWith(
      "a",
      graph.nodes[1].pointer,
      "input_node_id",
    );
    await user.click(output());
    await user.keyboard("{Escape}");
    await user.click(input());
    expect(props.onConnect).toHaveBeenCalledTimes(1);
  });
  it("reveals the current selection after delayed placement, repeats diagnostics and leaves manual movement alone", async () => {
    let resolve!: (graph: ElkNode) => void;
    layout.mockReturnValueOnce(
      new Promise<ElkNode>((done) => {
        resolve = done;
      }),
    );
    const scroll = vi.fn();
    const previous = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      const props = {
        ...setup(),
        selectedPointer: graph.nodes[1].pointer,
        revealSignal: 1,
      };
      const view = render(<NodeCanvas {...props} />);
      await waitFor(() => expect(layout).toHaveBeenCalledOnce());
      scroll.mockClear();
      await act(async () =>
        resolve({
          id: "root",
          children: [
            { id: "n0", x: 24, y: 24 },
            { id: "n1", x: 1800, y: 1400 },
          ],
        }),
      );
      expect(scroll).toHaveBeenCalledOnce();
      expect(scroll.mock.instances[0]).toHaveAttribute("aria-current", "true");
      scroll.mockClear();
      view.rerender(<NodeCanvas {...props} revealSignal={2} />);
      expect(scroll).toHaveBeenCalledOnce();
      scroll.mockClear();
      const move = screen.getByRole("button", { name: /2. 평균.*이동/ });
      move.focus();
      await userEvent.setup().keyboard("{ArrowRight}");
      expect(scroll).not.toHaveBeenCalled();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });
  it("keeps the inspector visible across parent reveal and a delayed layout", async () => {
    let resolve!: (graph: ElkNode) => void;
    layout.mockReturnValueOnce(
      new Promise<ElkNode>((done) => {
        resolve = done;
      }),
    );
    const scroll = vi.fn();
    const previous = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    const Inspector = ({ inspect }: { inspect: boolean }) => {
      const ref = useRef<HTMLFieldSetElement>(null);
      useEffect(() => {
        if (inspect) ref.current?.focus();
      }, [inspect]);
      return (
        <fieldset ref={ref} data-canvas-inspector tabIndex={-1}>
          <legend>Inspector</legend>
        </fieldset>
      );
    };
    const Surface = ({ inspect }: { inspect: boolean }) => {
      const selected = inspect ? graph.nodes[1].pointer : undefined;
      const root = useRevealSelection<HTMLDivElement>(selected);
      return (
        <div ref={root} className="factor-graph__editor">
          <NodeCanvas {...setup()} selectedPointer={selected ?? null} />
          <Inspector inspect={inspect} />
        </div>
      );
    };
    try {
      const view = render(<Surface inspect={false} />);
      await waitFor(() => expect(layout).toHaveBeenCalledOnce());
      view.rerender(<Surface inspect />);
      expect(screen.getByRole("group", { name: "Inspector" })).toHaveFocus();
      scroll.mockClear();
      await act(async () =>
        resolve({
          id: "root",
          children: [
            { id: "n0", x: 24, y: 24 },
            { id: "n1", x: 1800, y: 1400 },
          ],
        }),
      );
      expect(scroll).not.toHaveBeenCalled();
      expect(screen.getByRole("group", { name: "Inspector" })).toHaveFocus();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });
  it("resets local coordinates when another factor occupies the same index and node IDs", async () => {
    const props = setup();
    const view = render(<NodeCanvas {...props} />);
    await waitFor(() =>
      expect(
        screen.getByRole("status", { name: "노드 캔버스" }),
      ).toHaveAttribute("data-layout-status", "ready"),
    );
    const node = screen
      .getByRole("button", { name: "노드 편집: 1. 종가" })
      .closest(".node-canvas__node")!;
    screen.getByRole("button", { name: /1. 종가.*이동/ }).focus();
    await userEvent.setup().keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(node).toHaveStyle({ left: "74px" });
    view.rerender(<NodeCanvas {...props} factorKey="id:replacement" />);
    await waitFor(() => expect(node).toHaveStyle({ left: "24px" }));
  });
  it.each(["version", "document", "factor", "hidden", "busy"])(
    "discards a wire across %s changes even when the graph object is reused",
    async (change) => {
      const user = userEvent.setup(),
        props = setup();
      const view = render(<NodeCanvas {...props} />);
      await user.click(output());
      view.rerender(
        <NodeCanvas
          {...props}
          factorKey={change === "factor" ? "id:other" : props.factorKey}
          sourceVersion={change === "version" ? 1 : 0}
          documentKey={change === "document" ? 2 : 1}
          active={change !== "hidden"}
          busy={change === "busy"}
        />,
      );
      view.rerender(<NodeCanvas {...props} />);
      await user.click(input());
      expect(props.onConnect).not.toHaveBeenCalled();
    },
  );
});
