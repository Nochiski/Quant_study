import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

type SplitHandleProps = {
  orientation: "vertical" | "horizontal";
  /** Accessible name, e.g. "패널 크기 조절". */
  label: string;
  /** Current size of the controlled pane in px (the pane before the handle). */
  value: number;
  min: number;
  max: number;
  onChange: (next: number) => void;
  /** Keyboard step in px. */
  step?: number;
  /** Id of the pane this handle resizes (`aria-controls`). */
  controls?: string;
};

/**
 * WAI-ARIA window splitter: `role="separator"` with `aria-valuenow`, arrow keys resize by
 * `step`, Home/End jump to the bounds, pointer drag resizes continuously. State (the size) is
 * owned by the caller so it can live in a widget or URL search param.
 */
export const SplitHandle = ({
  orientation,
  label,
  value,
  min,
  max,
  onChange,
  step = 16,
  controls,
}: SplitHandleProps) => {
  const [dragging, setDragging] = useState(false);
  const origin = useRef<{ pointer: number; value: number } | null>(null);
  const clamp = (next: number) => Math.min(max, Math.max(min, next));
  const axis = orientation === "vertical" ? "clientX" : "clientY";
  const grow = orientation === "vertical" ? ["ArrowRight"] : ["ArrowDown"];
  const shrink = orientation === "vertical" ? ["ArrowLeft"] : ["ArrowUp"];

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let next: number | undefined;
    if (grow.includes(event.key)) next = value + step;
    else if (shrink.includes(event.key)) next = value - step;
    else if (event.key === "Home") next = min;
    else if (event.key === "End") next = max;
    if (next === undefined) return;
    event.preventDefault();
    onChange(clamp(next));
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    origin.current = { pointer: event[axis], value };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!origin.current) return;
    onChange(
      clamp(origin.current.value + (event[axis] - origin.current.pointer)),
    );
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    origin.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    setDragging(false);
  };

  return (
    <div
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation={orientation}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-controls={controls}
      data-dragging={dragging}
      className={`ui-split-handle ui-split-handle--${orientation}`}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <span className="ui-split-handle__grip" aria-hidden="true" />
    </div>
  );
};
