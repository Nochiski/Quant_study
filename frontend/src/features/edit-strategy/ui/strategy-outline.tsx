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
  onCollapse: (node: StrategyOutlineNode) => void;
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

const groupChild = (item: HTMLElement): HTMLElement | null => {
  const group = Array.from(item.children).find(
    (child) => child.getAttribute("role") === "group",
  );
  return group?.querySelector<HTMLElement>('[role="treeitem"]') ?? null;
};

const parentItem = (item: HTMLElement): HTMLElement | null => {
  const group = item.parentElement;
  if (group?.getAttribute("role") !== "group") return null;
  const parent = group.parentElement;
  return parent?.getAttribute("role") === "treeitem" ? parent : null;
};

export const StrategyOutline = ({
  snapshot,
  selectedPointer,
  onSelect,
  onCollapse,
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
  const rootIds = useMemo(
    () => new Set(snapshot?.nodes.map((node) => node.id) ?? []),
    [snapshot],
  );
  const selectedAncestorIds = useMemo(() => {
    return new Set(
      snapshot && selected
        ? outlineAncestorIds(snapshot.nodes, selected.id)
        : [],
    );
  }, [selected, snapshot]);
  const visibility = useMemo(() => {
    const visibleIds = new Set<string>();
    const openIds = new Set<string>();
    const visit = (items: readonly StrategyOutlineNode[]): void => {
      for (const node of items) {
        visibleIds.add(node.id);
        const configured = expansionOverrides.get(node.id);
        const open =
          node.children.length > 0 &&
          (query !== "" ||
            selectedAncestorIds.has(node.id) ||
            (configured ?? rootIds.has(node.id)));
        if (open) {
          openIds.add(node.id);
          visit(node.children);
        }
      }
    };
    visit(nodes);
    return { visibleIds, openIds };
  }, [expansionOverrides, nodes, query, rootIds, selectedAncestorIds]);
  const activeTabId = visibility.visibleIds.has(focusedId ?? "")
    ? focusedId
    : selected && visibility.visibleIds.has(selected.id)
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
    event: KeyboardEvent<HTMLLIElement>,
    node: StrategyOutlineNode,
    open: boolean,
  ): void => {
    if (event.currentTarget !== event.target) return;
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
        event.preventDefault();
        if (node.children.length === 0) break;
        if (!open) toggle(node.id, open);
        else groupChild(event.currentTarget)?.focus();
        break;
      case "ArrowLeft":
        event.preventDefault();
        if (open && node.children.length > 0) {
          onCollapse(node);
          toggle(node.id, open);
        }
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
    const open = visibility.openIds.has(node.id);
    const isSelected = selected?.id === node.id;
    const tabIndex = activeTabId === node.id ? 0 : -1;
    return (
      <li
        key={node.id}
        role="treeitem"
        className="strategy-outline__item"
        aria-expanded={hasChildren ? open : undefined}
        aria-selected={isSelected}
        aria-label={accessibleLabel(node)}
        aria-level={level}
        data-present={node.present ? "true" : "false"}
        tabIndex={tabIndex}
        title={node.pointer || "/"}
        onFocus={(event) => {
          if (event.currentTarget === event.target) setFocusedId(node.id);
        }}
        onKeyDown={(event) => onKeyDown(event, node, open)}
        onClick={(event: MouseEvent<HTMLLIElement>) => {
          const target = event.target as HTMLElement;
          if (target.closest('[role="treeitem"]') !== event.currentTarget)
            return;
          setFocusedId(node.id);
          event.currentTarget.focus();
          if (hasChildren && target.dataset.disclosure === "true") {
            if (open) onCollapse(node);
            toggle(node.id, open);
            return;
          }
          onSelect(node);
        }}
      >
        <div className="strategy-outline__item-row">
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
        </div>
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
