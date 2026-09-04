import {
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import { t } from "../../../shared/config";
import {
  findOutlineNode,
  outlineAncestorIds,
  type StrategyOutlineNode,
} from "../model/strategy-outline";
import type { StrategyOutlineSnapshot } from "../model/use-strategy-outline";
import "./strategy-outline.css";

type StrategyOutlineProps = {
  snapshot: StrategyOutlineSnapshot | null;
  selectedPointer: string | undefined;
  onSelect: (node: StrategyOutlineNode) => void;
};

const visibleLabel = (node: StrategyOutlineNode): string =>
  node.kind === "basics" ? t("ide.section.identity") : node.label;

const accessibleLabel = (node: StrategyOutlineNode): string => {
  if (node.arrayIndex === null) return visibleLabel(node);
  const index = `${t("ide.outline.arrayIndex")} ${node.arrayIndex}`;
  const identity = node.semanticIdentity;
  return identity
    ? `${index}, ${identity.namespace}_id ${identity.value}`
    : index;
};

const matches = (node: StrategyOutlineNode, query: string): boolean => {
  const identity = node.semanticIdentity;
  return `${visibleLabel(node)} ${node.pointer} ${identity?.namespace ?? ""} ${identity?.value ?? ""}`
    .toLocaleLowerCase()
    .includes(query);
};

const filterNodes = (
  nodes: readonly StrategyOutlineNode[],
  query: string,
): StrategyOutlineNode[] => {
  if (query === "") return [...nodes];
  return nodes.flatMap((node) => {
    if (matches(node, query)) return [node];
    const children = filterNodes(node.children, query);
    return children.length > 0 ? [{ ...node, children }] : [];
  });
};

const containsId = (
  nodes: readonly StrategyOutlineNode[],
  id: string | null,
): boolean =>
  id !== null &&
  nodes.some(
    (node) => node.id === id || containsId(node.children, id),
  );

const groupChild = (item: HTMLElement): HTMLElement | null => {
  const container = item.parentElement;
  if (!container) return null;
  const group = Array.from(container.children).find(
    (child) => child.getAttribute("role") === "group",
  );
  return group?.querySelector<HTMLElement>('[role="treeitem"]') ?? null;
};

const parentItem = (item: HTMLElement): HTMLElement | null => {
  const group = item.parentElement?.parentElement;
  if (group?.getAttribute("role") !== "group") return null;
  const parentContainer = group.parentElement;
  return (
    Array.from(parentContainer?.children ?? []).find(
      (child) => child.getAttribute("role") === "treeitem",
    ) as HTMLElement | undefined
  ) ?? null;
};

export const StrategyOutline = ({
  snapshot,
  selectedPointer,
  onSelect,
}: StrategyOutlineProps) => {
  const tree = useRef<HTMLUListElement>(null);
  const [filter, setFilter] = useState("");
  const [expansionOverrides, setExpansionOverrides] = useState<
    ReadonlyMap<string, boolean>
  >(new Map());
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const query = filter.trim().toLocaleLowerCase();
  const nodes = useMemo(
    () => filterNodes(snapshot?.nodes ?? [], query),
    [snapshot?.nodes, query],
  );
  const selected = snapshot
    ? findOutlineNode(snapshot.nodes, selectedPointer ?? "")
    : null;
  const automaticallyExpanded = useMemo(() => {
    const result = new Set(snapshot?.nodes.map((node) => node.id) ?? []);
    if (snapshot && selected) {
      for (const id of outlineAncestorIds(snapshot.nodes, selected.id))
        result.add(id);
    }
    return result;
  }, [selected, snapshot]);
  const activeTabId = containsId(nodes, focusedId)
    ? focusedId
    : selected && containsId(nodes, selected.id)
      ? selected.id
      : (nodes[0]?.id ?? null);

  const toggle = (id: string, currentlyOpen: boolean): void =>
    setExpansionOverrides((before) => {
      const next = new Map(before);
      next.set(id, !currentlyOpen);
      return next;
    });

  const focusRelative = (current: HTMLElement, delta: number): void => {
    const items = Array.from(
      tree.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [],
    );
    const index = items.indexOf(current);
    items[Math.max(0, Math.min(index + delta, items.length - 1))]?.focus();
  };

  const onKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    node: StrategyOutlineNode,
    open: boolean,
  ): void => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusRelative(event.currentTarget, 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        focusRelative(event.currentTarget, -1);
        break;
      case "Home":
        event.preventDefault();
        tree.current?.querySelector<HTMLElement>('[role="treeitem"]')?.focus();
        break;
      case "End": {
        event.preventDefault();
        const items = tree.current?.querySelectorAll<HTMLElement>(
          '[role="treeitem"]',
        );
        items?.item(items.length - 1).focus();
        break;
      }
      case "ArrowRight":
        if (node.children.length === 0) break;
        event.preventDefault();
        if (!open) toggle(node.id, open);
        else groupChild(event.currentTarget)?.focus();
        break;
      case "ArrowLeft":
        event.preventDefault();
        if (open && node.children.length > 0) toggle(node.id, open);
        else parentItem(event.currentTarget)?.focus();
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        onSelect(node);
        break;
    }
  };

  const renderNode = (node: StrategyOutlineNode, level = 1) => {
    const hasChildren = node.children.length > 0;
    const configured = expansionOverrides.get(node.id);
    const open =
      hasChildren &&
      (query !== "" || (configured ?? automaticallyExpanded.has(node.id)));
    const isSelected = selected?.id === node.id;
    const tabIndex = activeTabId === node.id ? 0 : -1;
    return (
      <li key={node.id} role="none">
        <button
          type="button"
          role="treeitem"
          className="strategy-outline__item"
          aria-expanded={hasChildren ? open : undefined}
          aria-selected={isSelected}
          aria-label={accessibleLabel(node)}
          aria-level={level}
          data-present={node.present ? "true" : "false"}
          tabIndex={tabIndex}
          title={node.pointer || "/"}
          onFocus={() => setFocusedId(node.id)}
          onKeyDown={(event) => onKeyDown(event, node, open)}
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            setFocusedId(node.id);
            const target = event.target as HTMLElement;
            if (hasChildren && target.dataset.disclosure === "true") {
              toggle(node.id, open);
              return;
            }
            onSelect(node);
          }}
        >
          <span
            className="strategy-outline__disclosure"
            data-disclosure="true"
            aria-hidden="true"
          >
            {hasChildren ? (open ? "⌄" : "›") : ""}
          </span>
          {node.arrayIndex !== null ? (
            <span className="strategy-outline__index">#{node.arrayIndex}</span>
          ) : null}
          {node.arrayIndex === null ? (
            <span className="strategy-outline__label">{visibleLabel(node)}</span>
          ) : null}
          {node.semanticIdentity ? (
            <span className="strategy-outline__identity">
              {node.semanticIdentity.namespace}_id · {node.semanticIdentity.value}
            </span>
          ) : null}
          {!node.present ? (
            <span className="strategy-outline__missing" aria-label={t("ide.outline.missing")}>
              ○
            </span>
          ) : null}
        </button>
        {open ? (
          <ul role="group" className="strategy-outline__group">
            {node.children.map((child) => renderNode(child, level + 1))}
          </ul>
        ) : null}
      </li>
    );
  };

  return (
    <div className="strategy-outline">
      <div className="strategy-outline__filter">
        <input
          type="search"
          className="strategy-outline__filter-input"
          aria-label={t("ide.outline.filter")}
          placeholder={t("ide.outline.filterPlaceholder")}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>
      {snapshot?.stale ? (
        <p className="strategy-outline__notice" role="status">
          {snapshot.staleReason === "syntax-error"
            ? t("ide.outline.stale")
            : t("ide.outline.parsing")}
        </p>
      ) : null}
      {nodes.length > 0 ? (
        <ul
          ref={tree}
          role="tree"
          className="strategy-outline__tree"
          aria-label={t("ide.outline.tree")}
        >
          {nodes.map((node) => renderNode(node))}
        </ul>
      ) : (
        <p className="strategy-outline__notice" role="status">
          {snapshot ? t("ide.outline.noMatches") : t("ide.outline.parsing")}
        </p>
      )}
    </div>
  );
};
