import { useId, type KeyboardEvent } from "react";

import { panelId, tabId } from "./tab-ids";

export type TabItem<Id extends string = string> = {
  id: Id;
  label: string;
  disabled?: boolean;
};

type TabsProps<Id extends string> = {
  items: readonly TabItem<Id>[];
  value: Id;
  onChange: (id: Id) => void;
  /** Accessible name of the tab list, e.g. "표현 전환". */
  label: string;
  className?: string;
};

/**
 * WAI-ARIA tabs: arrow keys move between enabled tabs, Home/End jump, Enter/Space activate.
 * Panels are owned by the caller; `panelId(id)` gives the matching `aria-controls` target.
 */
export function Tabs<Id extends string>({
  items,
  value,
  onChange,
  label,
  className = "",
}: TabsProps<Id>) {
  const baseId = useId();
  const enabled = items.filter((item) => !item.disabled);

  const move = (event: KeyboardEvent<HTMLButtonElement>, current: Id) => {
    const index = enabled.findIndex((item) => item.id === current);
    if (index < 0 || enabled.length === 0) return;
    const step: Record<string, number> = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    };
    let next: TabItem<Id> | undefined;
    if (event.key in step) {
      next =
        enabled[(index + step[event.key] + enabled.length) % enabled.length];
    } else if (event.key === "Home") {
      next = enabled[0];
    } else if (event.key === "End") {
      next = enabled[enabled.length - 1];
    }
    if (!next) return;
    event.preventDefault();
    onChange(next.id);
    document.getElementById(tabId(baseId, next.id))?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={`ui-tabs ${className}`.trim()}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            id={tabId(baseId, item.id)}
            type="button"
            role="tab"
            className="ui-tab"
            aria-selected={selected}
            aria-controls={panelId(baseId, item.id)}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => move(event, item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
