/** ELK는 좌표만 소유한다. 문서·팩터·버전 신원은 호출자가 바뀔 때마다 reconcile한다. */
import type { ElkNode } from "elkjs/lib/elk-api";
import type { CanvasProjection } from "./canvas-projection";

export const CANVAS_WIDTH = 240;
export const canvasHeight = (inputs: number) => 112 + Math.max(1, inputs) * 28;
export type Point = { x: number; y: number };
export type LayoutSnapshot = {
  positions: ReadonlyMap<string, Point>;
  status: "idle" | "loading" | "ready" | "error";
  elapsedMs: number | null;
  completed: number;
};
export type LayoutEngine = {
  layout: (graph: ElkNode) => Promise<ElkNode>;
  terminateWorker: () => void;
};
export const createLayoutEngine = async (): Promise<LayoutEngine> => {
  const [{ default: ELK }, { default: url }] = await Promise.all([
    import("elkjs/lib/elk-api"),
    import("elkjs/lib/elk-worker.min.js?url"),
  ]);
  return new ELK({ workerFactory: () => new Worker(url) });
};

const elkGraph = (projection: CanvasProjection): ElkNode => {
  const index = new Map(projection.nodes.map((node, i) => [node.key, i]));
  return {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "36",
    },
    children: projection.nodes.map((node, i) => ({
      id: `n${i}`,
      width: CANVAS_WIDTH,
      height: canvasHeight(node.inputs.length),
      layoutOptions: { "elk.portConstraints": "FIXED_SIDE" },
      ports: [
        { id: `n${i}out`, layoutOptions: { "elk.port.side": "EAST" } },
        ...node.inputs.map((_, j) => ({
          id: `n${i}in${j}`,
          layoutOptions: { "elk.port.side": "WEST" },
        })),
      ],
    })),
    edges: projection.edges.map((edge, i) => {
      const to = index.get(edge.to)!;
      const port = projection.nodes[to].inputs.findIndex(
        (input) => input.key === edge.input,
      );
      return {
        id: `e${i}`,
        sources: [`n${index.get(edge.from)}out`],
        targets: [`n${to}in${port}`],
      };
    }),
  };
};

/** 외부 Worker 응답과 수동 이동을 직렬화하는 UI 저장소. source/cache에는 쓰지 않는다. */
export class CanvasLayout {
  private snapshot: LayoutSnapshot = {
    positions: new Map(),
    status: "idle",
    elapsedMs: null,
    completed: 0,
  };
  private listeners = new Set<() => void>();
  private generation = 0;
  private engine: LayoutEngine | null = null;
  private graph: CanvasProjection = { nodes: [], edges: [] };
  private topology = "";
  private active = false;
  constructor(
    private factory: () => Promise<LayoutEngine> = createLayoutEngine,
  ) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(snapshot: LayoutSnapshot) {
    this.snapshot = snapshot;
    this.listeners.forEach((listener) => listener());
  }
  private stop() {
    this.generation++;
    this.engine?.terminateWorker();
    this.engine = null;
  }
  reconcile(graph: CanvasProjection, active: boolean) {
    this.stop();
    this.graph = graph;
    this.active = active;
    const topology = JSON.stringify([
      graph.nodes.map((node) => [
        node.key,
        node.inputs.map((input) => input.key),
      ]),
      graph.edges,
    ]);
    const changed = topology !== this.topology;
    this.topology = topology;
    // 임시 pointer key는 구조 변경에 따라 뜻이 달라질 수 있다. 정상 고유 ID 좌표만 이어 쓴다.
    const positions = new Map(
      graph.nodes.map((node, i) => [
        node.key,
        (node.connectable
          ? this.snapshot.positions.get(node.key)
          : undefined) ?? {
          x: 24 + (i % 3) * 300,
          y: 24 + Math.floor(i / 3) * 240,
        },
      ]),
    );
    const needsLayout =
      changed ||
      this.snapshot.status === "loading" ||
      this.snapshot.status === "idle";
    this.publish({
      ...this.snapshot,
      positions,
      status:
        !active && (changed || this.snapshot.status === "loading")
          ? "idle"
          : this.snapshot.status,
    });
    if (active && graph.nodes.length > 0 && needsLayout) void this.arrange();
  }
  arrange = async () => {
    if (!this.active || this.graph.nodes.length === 0) return;
    this.stop();
    const generation = this.generation;
    const graph = this.graph;
    const start = performance.now();
    this.publish({ ...this.snapshot, status: "loading", elapsedMs: null });
    try {
      const engine = await this.factory();
      if (generation !== this.generation) {
        engine.terminateWorker();
        return;
      }
      this.engine = engine;
      const placed = await engine.layout(elkGraph(graph));
      if (generation !== this.generation) return;
      const positions = new Map<string, Point>();
      for (let i = 0; i < graph.nodes.length; i++) {
        const node = placed.children?.find(
          (candidate) => candidate.id === `n${i}`,
        );
        if (!Number.isFinite(node?.x) || !Number.isFinite(node?.y))
          throw new Error("ELK coordinates missing");
        positions.set(graph.nodes[i].key, { x: node!.x!, y: node!.y! });
      }
      this.publish({
        positions,
        completed: this.snapshot.completed + 1,
        status: "ready",
        elapsedMs: performance.now() - start,
      });
    } catch {
      if (generation === this.generation)
        this.publish({ ...this.snapshot, status: "error" });
    } finally {
      if (generation === this.generation) {
        this.engine?.terminateWorker();
        this.engine = null;
      }
    }
  };
  move(key: string, point: Point) {
    if (!this.active || !this.snapshot.positions.has(key)) return;
    this.stop();
    this.publish({
      ...this.snapshot,
      status: "ready",
      positions: new Map(this.snapshot.positions).set(key, {
        x: Math.max(0, point.x),
        y: Math.max(0, point.y),
      }),
    });
  }
  cancel = () => {
    this.stop();
    this.publish({ ...this.snapshot, status: "ready" });
  };
  dispose = () => {
    this.active = false;
    this.stop();
  };
}
