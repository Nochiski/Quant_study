import { describe, expect, it, vi } from "vitest";
import type { ElkNode } from "elkjs/lib/elk-api";
import { CanvasLayout, type LayoutEngine } from "../model/canvas-layout";
import type { CanvasProjection } from "../model/canvas-projection";

const graph: CanvasProjection = {
  nodes: [
    {
      key: "id:a",
      nodeId: "a",
      pointer: "/factors/0/graph/nodes/0",
      label: "1. 가격",
      connectable: true,
      inputs: [],
    },
  ],
  edges: [],
};
const deferred = () => {
  let resolve!: (value: ElkNode) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<ElkNode>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const placed = (x: number): ElkNode => ({
  id: "root",
  children: [{ id: "n0", x, y: 30 }],
});
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

describe("canvas layout lifecycle", () => {
  it("does not start a worker for a hidden or empty graph", () => {
    const factory = vi.fn();
    const store = new CanvasLayout(factory);
    store.reconcile(graph, false);
    store.reconcile({ nodes: [], edges: [] }, true);
    expect(factory).not.toHaveBeenCalled();
  });
  it("lays out topology changed while hidden but preserves unchanged manual positions", async () => {
    const layout = vi.fn(async (input: ElkNode) => ({
      ...input,
      children: input.children?.map((node, i) => ({
        ...node,
        x: 100 + i * 300,
        y: 30,
      })),
    }));
    const factory = vi.fn(async () => ({ layout, terminateWorker: vi.fn() }));
    const store = new CanvasLayout(factory);
    store.reconcile(graph, true);
    await flush();
    store.move("id:a", { x: 77, y: 88 });
    store.reconcile(graph, false);
    store.reconcile(graph, true);
    expect(factory).toHaveBeenCalledOnce();
    expect(store.getSnapshot().positions.get("id:a")).toEqual({ x: 77, y: 88 });
    const changed = {
      ...graph,
      nodes: [
        ...graph.nodes,
        {
          ...graph.nodes[0],
          key: "id:b",
          nodeId: "b",
          pointer: "/factors/0/graph/nodes/1",
        },
      ],
    };
    store.reconcile(changed, false);
    expect(factory).toHaveBeenCalledOnce();
    store.reconcile(changed, true);
    await flush();
    expect(factory).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().positions.get("id:b")).toEqual({
      x: 400,
      y: 30,
    });
  });
  it("discards a pending layout after manual movement and terminates its worker", async () => {
    const pending = deferred();
    const engine: LayoutEngine = {
      layout: () => pending.promise,
      terminateWorker: vi.fn(),
    };
    const store = new CanvasLayout(async () => engine);
    store.reconcile(graph, true);
    await flush();
    store.move("id:a", { x: 345, y: 456 });
    pending.resolve(placed(1));
    await flush();
    expect(store.getSnapshot().positions.get("id:a")).toEqual({
      x: 345,
      y: 456,
    });
    expect(engine.terminateWorker).toHaveBeenCalledOnce();
  });
  it("ignores an old source version and accepts only the latest placement", async () => {
    const first = deferred(),
      second = deferred();
    const factory = vi
      .fn()
      .mockResolvedValueOnce({
        layout: () => first.promise,
        terminateWorker: vi.fn(),
      })
      .mockResolvedValueOnce({
        layout: () => second.promise,
        terminateWorker: vi.fn(),
      });
    const store = new CanvasLayout(factory);
    store.reconcile(graph, true);
    await flush();
    store.reconcile(graph, true);
    await flush();
    second.resolve(placed(200));
    await flush();
    first.resolve(placed(1));
    await flush();
    expect(store.getSnapshot().positions.get("id:a")?.x).toBe(200);
  });
  it("terminates an engine whose import completed after disposal", async () => {
    let resolve!: (value: LayoutEngine) => void;
    const engine: LayoutEngine = { layout: vi.fn(), terminateWorker: vi.fn() };
    const store = new CanvasLayout(
      () =>
        new Promise((yes) => {
          resolve = yes;
        }),
    );
    store.reconcile(graph, true);
    store.dispose();
    resolve(engine);
    await flush();
    expect(engine.terminateWorker).toHaveBeenCalledOnce();
    expect(engine.layout).not.toHaveBeenCalled();
  });
  it("keeps editable fallback coordinates on failure and permits retry or cancel", async () => {
    const pending = deferred();
    const factory = vi
      .fn()
      .mockResolvedValueOnce({
        layout: () => Promise.reject(new Error("worker")),
        terminateWorker: vi.fn(),
      })
      .mockResolvedValueOnce({
        layout: () => pending.promise,
        terminateWorker: vi.fn(),
      });
    const store = new CanvasLayout(factory);
    store.reconcile(graph, true);
    await flush();
    await flush();
    expect(store.getSnapshot().status).toBe("error");
    expect(store.getSnapshot().positions.size).toBe(1);
    void store.arrange();
    await flush();
    store.cancel();
    pending.resolve(placed(999));
    await flush();
    expect(store.getSnapshot().status).toBe("ready");
    expect(store.getSnapshot().positions.get("id:a")?.x).not.toBe(999);
  });
});
