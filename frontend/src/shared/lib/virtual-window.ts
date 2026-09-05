import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useState,
  type RefObject,
  type UIEvent,
} from "react";

export type VirtualWindow = {
  start: number;
  end: number;
  paddingBefore: number;
  paddingAfter: number;
  virtualized: boolean;
};

export type VirtualWindowOptions = {
  itemCount: number;
  itemHeight: number;
  scrollTop: number;
  viewportHeight: number;
  overscan?: number;
  threshold?: number;
};

/** Pure, domain-free projection from scroll metrics to a bounded half-open item range. */
export const projectVirtualWindow = ({
  itemCount,
  itemHeight,
  scrollTop,
  viewportHeight,
  overscan = 6,
  threshold = 40,
}: VirtualWindowOptions): VirtualWindow => {
  const count = Math.max(0, Math.floor(itemCount));
  if (count <= threshold)
    return {
      start: 0,
      end: count,
      paddingBefore: 0,
      paddingAfter: 0,
      virtualized: false,
    };

  const height = Math.max(1, itemHeight);
  const viewport = Math.max(height, viewportHeight);
  const safeOverscan = Math.max(0, Math.floor(overscan));
  const maximumScroll = Math.max(0, count * height - viewport);
  const offset = Math.min(Math.max(0, scrollTop), maximumScroll);
  const firstVisible = Math.floor(offset / height);
  const start = Math.max(0, firstVisible - safeOverscan);
  const visibleCount = Math.ceil(viewport / height) + safeOverscan * 2;
  const end = Math.min(count, start + visibleCount);
  return {
    start,
    end,
    paddingBefore: start * height,
    paddingAfter: (count - end) * height,
    virtualized: true,
  };
};

type UseVirtualWindowOptions = Pick<
  VirtualWindowOptions,
  "itemCount" | "itemHeight" | "overscan" | "threshold"
> & {
  fallbackViewportHeight: number;
  anchorIndex?: number | null;
};

type UseVirtualWindowResult = VirtualWindow & {
  onScroll: (event: UIEvent<HTMLDivElement>) => void;
};

/** Browser metric adapter. Rendering and row semantics remain owned by the calling feature. */
export const useVirtualWindow = (
  viewportRef: RefObject<HTMLDivElement | null>,
  {
    itemCount,
    itemHeight,
    fallbackViewportHeight,
    anchorIndex = null,
    overscan,
    threshold,
  }: UseVirtualWindowOptions,
): UseVirtualWindowResult => {
  const initialOffset =
    anchorIndex === null ? 0 : Math.max(0, anchorIndex) * itemHeight;
  const [metrics, setMetrics] = useState({
    scrollTop: initialOffset,
    viewportHeight: fallbackViewportHeight,
  });

  const updateMetrics = useCallback(
    (element: HTMLDivElement): void => {
      const viewportHeight =
        element.clientHeight > 0
          ? element.clientHeight
          : fallbackViewportHeight;
      setMetrics((current) => {
        const next = { scrollTop: element.scrollTop, viewportHeight };
        return current.scrollTop === next.scrollTop &&
          current.viewportHeight === next.viewportHeight
          ? current
          : next;
      });
    },
    [fallbackViewportHeight],
  );

  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (element === null) return;
    const maximumScroll = Math.max(
      0,
      itemCount * itemHeight - (element.clientHeight || fallbackViewportHeight),
    );
    const requested =
      anchorIndex === null ? element.scrollTop : anchorIndex * itemHeight;
    element.scrollTop = Math.min(Math.max(0, requested), maximumScroll);
    updateMetrics(element);

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => updateMetrics(element));
    observer.observe(element);
    return () => observer.disconnect();
  }, [
    anchorIndex,
    fallbackViewportHeight,
    itemCount,
    itemHeight,
    updateMetrics,
    viewportRef,
  ]);

  const range = useMemo(
    () =>
      projectVirtualWindow({
        itemCount,
        itemHeight,
        scrollTop: metrics.scrollTop,
        viewportHeight: metrics.viewportHeight,
        overscan,
        threshold,
      }),
    [itemCount, itemHeight, metrics, overscan, threshold],
  );

  const onScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => updateMetrics(event.currentTarget),
    [updateMetrics],
  );
  return { ...range, onScroll };
};
