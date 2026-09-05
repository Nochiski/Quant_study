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
  /**
   * Id prefix shared with the caller's panels: render each panel with
   * `id={panelId(idBase, item.id)}` and `role="tabpanel"` so `aria-controls` resolves.
   * Defaults to a React id when the caller renders no panels.
   */
  idBase?: string;
  className?: string;
};

/**
 * WAI-ARIA tabs (horizontal): Left/Right move between enabled tabs, Home/End jump, Enter/Space
 * activate. Disabled tabs stay in the DOM with `aria-disabled` and are skipped by the keys.
 */
export function Tabs<Id extends string>({
  items,
  value,
  onChange,
  label,
  idBase,
  className = "",
}: TabsProps<Id>) {
  const generated = useId();
  const baseId = idBase ?? generated;
  const enabled = items.filter((item) => !item.disabled);

  const move = (event: KeyboardEvent<HTMLButtonElement>, current: Id) => {
    const index = enabled.findIndex((item) => item.id === current);
    if (index < 0 || enabled.length === 0) return;
    let next: TabItem<Id> | undefined;
    if (event.key === "ArrowRight")
      next = enabled[(index + 1) % enabled.length];
    else if (event.key === "ArrowLeft")
      next = enabled[(index - 1 + enabled.length) % enabled.length];
    else if (event.key === "Home") next = enabled[0];
    else if (event.key === "End") next = enabled[enabled.length - 1];
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
            aria-disabled={item.disabled || undefined}
            aria-controls={panelId(baseId, item.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => {
              if (!item.disabled) onChange(item.id);
            }}
            onKeyDown={(event) => move(event, item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
