import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { t } from "../../../shared/config";
import { useCommittedRef } from "../../../shared/lib/react";
import { Button } from "../../../shared/ui";
import {
  CANVAS_WIDTH,
  CanvasLayout,
  canvasHeight,
  type Point,
} from "../model/canvas-layout";
import type { CanvasProjection } from "../model/canvas-projection";
import type { DocumentDiagnostic } from "../model/document-state";
import "./node-canvas.css";

type Props = {
  graph: CanvasProjection;
  documentKey: unknown;
  sourceVersion: number;
  factorIndex: number;
  factorKey: string;
  revealSignal?: number;
  active: boolean;
  busy: boolean;
  selectedPointer: string | null;
  diagnostics: DocumentDiagnostic[];
  onSelect: (pointer: string) => void;
  onInspect: (pointer: string) => void;
  onRemove: (pointer: string, label: string) => void;
  onCopy: (pointer: string) => void;
  onConnect: (from: string, to: string, input: string) => void;
};

export const NodeCanvas = (props: Props) => {
  const {
    graph,
    documentKey,
    sourceVersion,
    factorIndex,
    factorKey,
    active,
    busy,
    selectedPointer,
  } = props;
  const [owned, setOwned] = useState(() => ({
    documentKey,
    factorIndex,
    factorKey,
    store: new CanvasLayout(),
  }));
  if (
    !Object.is(owned.documentKey, documentKey) ||
    owned.factorIndex !== factorIndex ||
    owned.factorKey !== factorKey
  )
    setOwned({
      documentKey,
      factorIndex,
      factorKey,
      store: new CanvasLayout(),
    });
  const store = owned.store;
  const snapshot = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  useLayoutEffect(() => {
    store.reconcile(graph, active);
    return store.dispose;
  }, [store, graph, active, sourceVersion]);
  const current = useCommittedRef(props);
  const [wire, setWire] = useState<{
    graph: CanvasProjection;
    version: number;
    documentKey: unknown;
    factorIndex: number;
    factorKey: string;
    from: string;
  } | null>(null);
  const currentWire =
    wire?.graph === graph &&
    wire.version === sourceVersion &&
    Object.is(wire.documentKey, documentKey) &&
    wire.factorIndex === factorIndex &&
    wire.factorKey === factorKey &&
    active &&
    !busy
      ? wire
      : null;
  if (wire !== null && currentWire === null) setWire(null);
  const wireRef = useCommittedRef(currentWire);
  const moving = useRef<{
    graph: CanvasProjection;
    version: number;
    key: string;
    pointer: number;
    start: Point;
    mouse: Point;
  } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    moving.current = null;
  }, [graph, sourceVersion, active, documentKey, factorIndex, factorKey]);
  // 배치 완료 후에만 다시 드러낸다. 수동 좌표 이동은 사용자의 스크롤을 빼앗지 않는다.
  useLayoutEffect(() => {
    if (!active || selectedPointer === null) return;
    const inspector = document.activeElement?.closest(
      "[data-canvas-inspector]",
    );
    if (
      inspector &&
      root.current?.closest(".factor-graph__editor")?.contains(inspector)
    )
      return;
    root.current
      ?.querySelector<HTMLElement>('.node-canvas__node[aria-current="true"]')
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [store, snapshot.completed, active, selectedPointer, props.revealSignal]);
  const prefix = useId();
  const connect = (pointer: string, input: string) => {
    const source = wireRef.current;
    const now = current.current;
    if (
      source === null ||
      source.graph !== now.graph ||
      source.version !== now.sourceVersion ||
      !Object.is(source.documentKey, now.documentKey) ||
      source.factorIndex !== now.factorIndex ||
      source.factorKey !== now.factorKey ||
      now.busy ||
      !now.active
    )
      return;
    setWire(null);
    now.onConnect(source.from, pointer, input);
  };
  const begin = (from: string) => {
    if (!busy && active)
      setWire({
        graph,
        version: sourceVersion,
        documentKey,
        factorIndex,
        factorKey,
        from,
      });
  };
  const position = (key: string, index: number) =>
    snapshot.positions.get(key) ?? {
      x: 24 + (index % 3) * 300,
      y: 24 + Math.floor(index / 3) * 240,
    };
  const width = Math.max(
    320,
    ...graph.nodes.map(
      (node, i) => position(node.key, i).x + CANVAS_WIDTH + 24,
    ),
  );
  const height = Math.max(
    240,
    ...graph.nodes.map(
      (node, i) =>
        position(node.key, i).y + canvasHeight(node.inputs.length) + 24,
    ),
  );
  const focusNext = (index: number, delta: number) => {
    const buttons =
      root.current?.querySelectorAll<HTMLButtonElement>("[data-canvas-node]");
    if (buttons?.length)
      buttons[(index + delta + buttons.length) % buttons.length]?.focus();
  };

  return (
    <div
      className="node-canvas"
      ref={root}
      onPointerUp={(event) => {
        const target = document
          .elementFromPoint?.(event.clientX, event.clientY)
          ?.closest<HTMLElement>("[data-canvas-input]");
        if (
          target !== null &&
          target !== undefined &&
          root.current?.contains(target)
        ) {
          const pointer = target.dataset.canvasPointer,
            input = target.dataset.canvasInput;
          if (pointer !== undefined && input !== undefined)
            connect(pointer, input);
        }
      }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.key !== "Escape") return;
        setWire(null);
        const gesture = moving.current;
        if (gesture?.graph === graph && gesture.version === sourceVersion)
          store.move(gesture.key, gesture.start);
        moving.current = null;
      }}
    >
      <div className="node-canvas__toolbar">
        <Button
          size="small"
          onClick={() => void store.arrange()}
          disabled={!active || graph.nodes.length === 0}
        >
          {t("graph.canvas.arrange")}
        </Button>
        {snapshot.status === "loading" ? (
          <Button size="small" onClick={store.cancel}>
            {t("graph.canvas.cancelLayout")}
          </Button>
        ) : null}
        <span
          role="status"
          aria-label={t("graph.canvas.title")}
          data-layout-status={snapshot.status}
          data-layout-ms={snapshot.elapsedMs ?? undefined}
        >
          {t(`graph.canvas.${snapshot.status}`)}
        </span>
        {currentWire !== null ? (
          <Button size="small" onClick={() => setWire(null)}>
            {t("graph.canvas.cancelWire")}
          </Button>
        ) : null}
      </div>
      <p id={`${prefix}-help`} className="node-canvas__help">
        {t("graph.canvas.help")}
      </p>
      <div
        className="node-canvas__viewport"
        role="region"
        aria-label={t("graph.canvas.title")}
        aria-describedby={`${prefix}-help`}
      >
        <div className="node-canvas__surface" style={{ width, height }}>
          <svg
            className="node-canvas__edges"
            width={width}
            height={height}
            aria-hidden="true"
          >
            {graph.edges.map((edge) => {
              const fromIndex = graph.nodes.findIndex(
                (node) => node.key === edge.from,
              );
              const toIndex = graph.nodes.findIndex(
                (node) => node.key === edge.to,
              );
              const a = position(edge.from, fromIndex),
                b = position(edge.to, toIndex);
              const port = graph.nodes[toIndex].inputs.findIndex(
                (input) => input.key === edge.input,
              );
              const x1 = a.x + CANVAS_WIDTH,
                y1 = a.y + 80,
                x2 = b.x,
                y2 = b.y + 80 + port * 28;
              return (
                <path
                  key={`${edge.to}:${edge.input}`}
                  d={`M${x1},${y1} C${x1 + 48},${y1} ${x2 - 48},${y2} ${x2},${y2}`}
                />
              );
            })}
          </svg>
          {graph.nodes.map((node, index) => {
            const point = position(node.key, index);
            const notes = props.diagnostics.filter(
              (diagnostic) =>
                diagnostic.pointer === node.pointer ||
                diagnostic.pointer.startsWith(node.pointer + "/"),
            );
            return (
              <div
                key={node.key}
                className="node-canvas__node"
                style={{
                  left: point.x,
                  top: point.y,
                  width: CANVAS_WIDTH,
                  height: canvasHeight(node.inputs.length),
                }}
                aria-current={
                  node.pointer === selectedPointer ? "true" : undefined
                }
              >
                <button
                  type="button"
                  data-canvas-node
                  className="node-canvas__select"
                  onClick={() => props.onSelect(node.pointer)}
                  aria-label={t("graph.editNode").replace("{node}", node.label)}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) return;
                    if (
                      [
                        "ArrowRight",
                        "ArrowDown",
                        "ArrowLeft",
                        "ArrowUp",
                      ].includes(event.key)
                    ) {
                      event.preventDefault();
                      focusNext(
                        index,
                        event.key === "ArrowRight" || event.key === "ArrowDown"
                          ? 1
                          : -1,
                      );
                    }
                    if (event.key === "Enter") {
                      event.preventDefault();
                      props.onInspect(node.pointer);
                    }
                    if (event.key === "Delete" && !busy) {
                      event.preventDefault();
                      props.onRemove(node.pointer, node.label);
                    }
                  }}
                >
                  {node.label}
                </button>
                <button
                  type="button"
                  className="node-canvas__move"
                  aria-label={`${node.label} · ${t("graph.canvas.move")}`}
                  onPointerDown={(event) => {
                    if (event.button !== 0 || !active) return;
                    event.currentTarget.setPointerCapture(event.pointerId);
                    moving.current = {
                      graph,
                      version: sourceVersion,
                      key: node.key,
                      pointer: event.pointerId,
                      start: point,
                      mouse: { x: event.clientX, y: event.clientY },
                    };
                  }}
                  onPointerMove={(event) => {
                    const gesture = moving.current,
                      now = current.current;
                    if (
                      gesture === null ||
                      gesture.pointer !== event.pointerId ||
                      gesture.graph !== now.graph ||
                      gesture.version !== now.sourceVersion ||
                      !now.active
                    )
                      return;
                    store.move(gesture.key, {
                      x: gesture.start.x + event.clientX - gesture.mouse.x,
                      y: gesture.start.y + event.clientY - gesture.mouse.y,
                    });
                  }}
                  onPointerUp={() => {
                    moving.current = null;
                  }}
                  onPointerCancel={() => {
                    const gesture = moving.current;
                    if (
                      gesture?.graph === graph &&
                      gesture.version === sourceVersion
                    )
                      store.move(gesture.key, gesture.start);
                    moving.current = null;
                  }}
                  onKeyDown={(event) => {
                    const shift = event.shiftKey ? 50 : 10;
                    const deltas: Record<string, Point> = {
                      ArrowLeft: { x: -shift, y: 0 },
                      ArrowRight: { x: shift, y: 0 },
                      ArrowUp: { x: 0, y: -shift },
                      ArrowDown: { x: 0, y: shift },
                    };
                    const delta = deltas[event.key];
                    if (delta) {
                      event.preventDefault();
                      store.move(node.key, {
                        x: point.x + delta.x,
                        y: point.y + delta.y,
                      });
                    }
                  }}
                >
                  {t("graph.canvas.move")}
                </button>
                <div className="node-canvas__ports">
                  <div>
                    {node.inputs.map((input) => {
                      const from = graph.nodes.find(
                        (candidate) =>
                          candidate.connectable &&
                          candidate.nodeId === input.sourceId,
                      );
                      const description =
                        from?.label ?? t("graph.canvas.unconnected");
                      return (
                        <button
                          key={input.key}
                          type="button"
                          className="node-canvas__input"
                          disabled={busy || !active}
                          aria-label={`${node.label} · ${input.label} · ${description}`}
                          title={description}
                          data-canvas-input={input.key}
                          data-canvas-pointer={node.pointer}
                          onClick={() => connect(node.pointer, input.key)}
                        >
                          {input.label}
                        </button>
                      );
                    })}
                  </div>
                  <button
                    type="button"
                    className="node-canvas__output"
                    disabled={busy || !active || !node.connectable}
                    aria-label={`${node.label} · ${t("graph.canvas.output")}`}
                    aria-pressed={currentWire?.from === node.nodeId}
                    onPointerDown={(event) => {
                      if (event.button === 0 && node.nodeId !== null)
                        begin(node.nodeId);
                    }}
                    onClick={() => {
                      if (node.nodeId !== null) begin(node.nodeId);
                    }}
                  >
                    {t("graph.canvas.output")}
                  </button>
                </div>
                <div className="node-canvas__actions">
                  <Button
                    size="small"
                    disabled={busy || !node.connectable}
                    onClick={() => props.onCopy(node.pointer)}
                    aria-label={`${node.label} · ${t("graph.canvas.copy")}`}
                  >
                    {t("graph.canvas.copy")}
                  </Button>
                  <Button
                    size="small"
                    tone="danger"
                    disabled={busy}
                    onClick={() => props.onRemove(node.pointer, node.label)}
                    aria-label={`${node.label} · ${t("graph.removeNode")}`}
                  >
                    {t("graph.removeNode")}
                  </Button>
                </div>
                {notes.length > 0 ? (
                  <button
                    type="button"
                    className="node-canvas__issues"
                    onClick={() => props.onInspect(node.pointer)}
                  >
                    {t("graph.canvas.issues").replace(
                      "{count}",
                      String(notes.length),
                    )}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
