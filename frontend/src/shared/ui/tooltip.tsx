import {
  cloneElement,
  useId,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

type TriggerProps = {
  "aria-describedby"?: string;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onKeyDown?: (event: { key: string }) => void;
};

type TooltipProps = {
  /** Text read by screen readers through `aria-describedby` and shown in the bubble. */
  content: ReactNode;
  children: ReactElement<TriggerProps>;
};

/**
 * Hover/focus tooltip. The bubble stays in the DOM (hidden) so `aria-describedby` always
 * resolves; Escape dismisses it while the trigger keeps focus.
 */
export const Tooltip = ({ content, children }: TooltipProps) => {
  const id = useId();
  const [open, setOpen] = useState(false);
  const trigger = cloneElement(children, {
    "aria-describedby": id,
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
    onKeyDown: (event: { key: string }) => {
      if (event.key === "Escape") setOpen(false);
      children.props.onKeyDown?.(event);
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
