import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

export type CommandPaletteItem = {
  id: string;
  group: string;
  label: string;
  description?: string;
  keywords?: readonly string[];
  shortcut?: string;
  disabled?: boolean;
  /** Logical focus destination after an executing command changes or hides the current UI. */
  focusAfterExecute?: () => HTMLElement | null;
  execute: () => void;
};

type CommandPaletteProps = {
  open: boolean;
  label: string;
  searchLabel: string;
  searchPlaceholder: string;
  emptyLabel: string;
  closeLabel: string;
  commands: readonly CommandPaletteItem[];
  onClose: () => void;
};

const searchable = (command: CommandPaletteItem): string =>
  [
    command.label,
    command.description,
    command.group,
    ...(command.keywords ?? []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();

const nextEnabled = (
  commands: readonly CommandPaletteItem[],
  current: number,
  direction: 1 | -1,
): number => {
  if (commands.length === 0) return -1;
  for (let step = 1; step <= commands.length; step += 1) {
    const index =
      (current + direction * step + commands.length) % commands.length;
    if (!commands[index]?.disabled) return index;
  }
  return -1;
};

const canRestoreFocus = (element: HTMLElement | null): element is HTMLElement =>
  element !== null &&
  element.isConnected &&
  element.closest("[hidden], [aria-hidden='true']") === null &&
  !element.matches(":disabled");

type OpenCommandPaletteProps = Omit<CommandPaletteProps, "open">;

const OpenCommandPalette = ({
  label,
  searchLabel,
  searchPlaceholder,
  emptyLabel,
  closeLabel,
  commands,
  onClose,
}: OpenCommandPaletteProps) => {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const focusAfterClose = useRef<(() => HTMLElement | null) | null>(null);
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle === ""
      ? commands
      : commands.filter((command) => searchable(command).includes(needle));
  }, [commands, query]);
  const [active, setActive] = useState(-1);
  const activeIndex =
    active >= 0 && active < filtered.length && !filtered[active]?.disabled
      ? active
      : nextEnabled(filtered, -1, 1);

  useEffect(() => {
    restoreFocus.current = document.activeElement as HTMLElement | null;
    queueMicrotask(() => input.current?.focus());
    return () => {
      queueMicrotask(() => {
        const requested = focusAfterClose.current?.() ?? null;
        if (canRestoreFocus(requested)) requested.focus();
        else if (canRestoreFocus(restoreFocus.current))
          restoreFocus.current.focus();
      });
    };
  }, []);

  useEffect(() => {
    if (activeIndex < 0) return;
    document
      .getElementById(`${id}-option-${activeIndex}`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, id]);

  const closePalette = (): void => {
    onClose();
  };
  const execute = (index: number): void => {
    const command = filtered[index];
    if (!command || command.disabled) return;
    focusAfterClose.current = command.focusAfterExecute ?? null;
    command.execute();
    closePalette();
  };
  const onInputKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive(
        nextEnabled(filtered, activeIndex, event.key === "ArrowDown" ? 1 : -1),
      );
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const edge = event.key === "Home" ? -1 : 0;
      setActive(nextEnabled(filtered, edge, event.key === "Home" ? 1 : -1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      execute(activeIndex);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closePalette();
    }
  };

  return (
    <div
      className="ui-command-palette__backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closePalette();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") closePalette();
        if (event.key !== "Tab") return;
        const target = event.target;
        if (event.shiftKey && target === input.current) {
          event.preventDefault();
          close.current?.focus();
        } else if (!event.shiftKey && target === close.current) {
          event.preventDefault();
          input.current?.focus();
        }
      }}
    >
      <section
        className="ui-command-palette"
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <header className="ui-command-palette__header">
          <label className="sr-only" htmlFor={`${id}-search`}>
            {searchLabel}
          </label>
          <input
            ref={input}
            id={`${id}-search`}
            type="search"
            role="combobox"
            autoComplete="off"
            aria-expanded="true"
            aria-controls={`${id}-list`}
            aria-activedescendant={
              activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined
            }
            value={query}
            placeholder={searchPlaceholder}
            onChange={(event) => {
              setQuery(event.currentTarget.value);
              setActive(-1);
            }}
            onKeyDown={onInputKeyDown}
          />
          <button
            ref={close}
            type="button"
            onClick={closePalette}
            aria-label={closeLabel}
          >
            ×
          </button>
        </header>
        <div
          id={`${id}-list`}
          className="ui-command-palette__list"
          role="listbox"
        >
          {filtered.length === 0 ? (
            <p className="ui-command-palette__empty">{emptyLabel}</p>
          ) : (
            filtered.map((command, index) => (
              <button
                key={command.id}
                id={`${id}-option-${index}`}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                aria-disabled={command.disabled || undefined}
                disabled={command.disabled}
                tabIndex={-1}
                onMouseMove={() => {
                  if (!command.disabled) setActive(index);
                }}
                onClick={() => execute(index)}
              >
                <span className="ui-command-palette__group">
                  {command.group}
                </span>
                <strong>{command.label}</strong>
                {command.description ? (
                  <small>{command.description}</small>
                ) : null}
                {command.shortcut ? <kbd>{command.shortcut}</kbd> : null}
              </button>
            ))
          )}
        </div>
      </section>
    </div>
  );
};

/** Domain-free command chooser. Callers own command meaning, availability and search data. */
export const CommandPalette = ({ open, ...props }: CommandPaletteProps) =>
  open ? <OpenCommandPalette {...props} /> : null;
