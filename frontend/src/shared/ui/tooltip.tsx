import {
  cloneElement,
  useId,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";

type TriggerProps = {
  "aria-describedby"?: string;
  onMouseEnter?: (event: MouseEvent) => void;
  onMouseLeave?: (event: MouseEvent) => void;
  onFocus?: (event: FocusEvent) => void;
  onBlur?: (event: FocusEvent) => void;
  onKeyDown?: (event: KeyboardEvent) => void;
};

type TooltipProps = {
  /** Text read by screen readers through `aria-describedby` and shown in the bubble. */
  content: ReactNode;
  children: ReactElement<TriggerProps>;
};

/**
 * Hover/focus tooltip. The bubble stays in the DOM (hidden) so `aria-describedby` always
 * resolves; Escape dismisses it while the trigger keeps focus. The child's own handlers and
 * `aria-describedby` are preserved and composed, never replaced.
 */
export const Tooltip = ({ content, children }: TooltipProps) => {
  const id = useId();
  const [open, setOpen] = useState(false);
  const own = children.props;
  const trigger = cloneElement(children, {
    "aria-describedby": [own["aria-describedby"], id].filter(Boolean).join(" "),
    onMouseEnter: (event: MouseEvent) => {
      setOpen(true);
      own.onMouseEnter?.(event);
    },
    onMouseLeave: (event: MouseEvent) => {
      setOpen(false);
      own.onMouseLeave?.(event);
    },
    onFocus: (event: FocusEvent) => {
      setOpen(true);
      own.onFocus?.(event);
    },
    onBlur: (event: FocusEvent) => {
      setOpen(false);
      own.onBlur?.(event);
    },
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
      own.onKeyDown?.(event);
    },
  });
  return (
    <span className="ui-tooltip">
      {trigger}
      <span
        role="tooltip"
        id={id}
        className="ui-tooltip__bubble"
        hidden={!open}
      >
        {content}
      </span>
    </span>
  );
};
