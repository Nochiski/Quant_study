import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import { t } from "../config";
import { browserStorage } from "../lib/browser-storage";
import {
  fitRect,
  initialRect,
  isSheetViewport,
  sheetRect,
  moveRect,
  nudgeRect,
  parseWindowRect,
  presetRect,
  resizeRect,
  type ResizeEdges,
  type Viewport,
  type WindowLimits,
  type WindowRect,
  type WindowSizePreset,
} from "../lib/floating-geometry";
import "./floating-window.css";

const DEFAULT_LIMITS: WindowLimits = {
  minWidth: 320,
  minHeight: 360,
  maxWidth: 760,
  maxHeight: 960,
};
const DEFAULT_SIZE = { width: 400, height: 560 };
/** 화살표 한 번에 옮기는 거리. Shift 를 누르면 네 배다. */
const NUDGE_STEP = 16;

/** 가장자리 8개(변 4·모서리 4). 이름은 CSS 자리 이름이다. */
const HANDLES: readonly { name: string; edges: ResizeEdges }[] = [
  { name: "n", edges: { top: true } },
  { name: "s", edges: { bottom: true } },
  { name: "e", edges: { right: true } },
  { name: "w", edges: { left: true } },
  { name: "ne", edges: { top: true, right: true } },
  { name: "nw", edges: { top: true, left: true } },
  { name: "se", edges: { bottom: true, right: true } },
  { name: "sw", edges: { bottom: true, left: true } },
];

const viewportNow = (): Viewport => ({ width: window.innerWidth, height: window.innerHeight });

const readRect = (storageKey: string): WindowRect | null => {
  try {
    const raw = browserStorage()?.getItem(storageKey);
    return raw === null || raw === undefined ? null : parseWindowRect(JSON.parse(raw));
  } catch {
    return null;
  }
};

const writeRect = (storageKey: string, rect: WindowRect): void => {
  try {
    browserStorage()?.setItem(storageKey, JSON.stringify(rect));
  } catch {
    // 보관함이 없거나 가득 찼다. 이 화면 동안의 자리로 충분하다.
  }
};

type Gesture = {
  pointerId: number;
  startX: number;
  startY: number;
  start: WindowRect;
  /** null 이면 옮기기, 아니면 그 가장자리로 크기 조절. */
  edges: ResizeEdges | null;
  last: WindowRect;
};

const DefaultLauncherIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z" />
  </svg>
);

export type FloatingWindowProps = {
  /** 창(aside) id. 여는 조작이 초점을 옮길 자리·`aria-controls` 대상으로 쓴다. */
  id?: string;
  /** 창 제목. 창(landmark)의 이름이자 제목 줄 글자다. */
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 위치·크기를 기억할 보관함 키. 같은 키를 쓰는 화면은 같은 자리에 창을 연다. */
  storageKey: string;
  /** 최소화했을 때 우하단에 남는 런처 버튼 글자. */
  launcherLabel: string;
  /** 런처 버튼 id. 바깥이 닫은 뒤 초점을 돌려줄 자리로 쓴다. */
  launcherId?: string;
  /** 최소화 버튼 접근 이름. */
  minimizeLabel: string;
  /** 런처 버튼 앞 아이콘(장식). 없으면 기본 반짝임 아이콘이다. */
  launcherIcon?: ReactNode;
  /** 창 본문. 처음 열 때 마운트하고, 최소화해도 유지한다(진행 중 대화·입력이 남는다). */
  children: ReactNode;
  limits?: WindowLimits;
  defaultSize?: { width: number; height: number };
};

/**
 * 화면 위에 떠 있는 비모달 창. 제목 줄을 끌어 옮기고 가장자리·모서리를 끌어 크기를 바꾼다(pointer events, 외부
 * 라이브러리 없음). 키보드 대안은 옮기기 손잡이의 화살표 키와 크기 프리셋이다. 위치·크기는 `storageKey` 로
 * 기억하고, 창 크기가 바뀌면 뷰포트 안으로 보정한다. 열림 상태는 바깥이 소유한다(controlled) — 화면마다 열림의
 * 기본값·저장 규칙이 다르다.
 */
export const FloatingWindow = ({
  id,
  title,
  open,
  onOpenChange,
  storageKey,
  launcherLabel,
  launcherId,
  minimizeLabel,
  children,
  limits = DEFAULT_LIMITS,
  defaultSize = DEFAULT_SIZE,
  launcherIcon = <DefaultLauncherIcon />,
}: FloatingWindowProps) => {
  const titleId = useId();
  const moveHintId = useId();
  const windowRef = useRef<HTMLElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const gesture = useRef<Gesture | null>(null);
  // 기억한 자리·크기(사용자가 옮기거나 크기를 바꾼 값). 창 크기 변경 보정은 이 값을 덮지 않고 표시용으로만 한다 —
  // 다시 넓어지면 기억한 자리로 돌아오고, 보정값은 저장하지 않는다.
  const [rect, setRect] = useState<WindowRect>(
    () => readRect(storageKey) ?? initialRect(defaultSize, viewportNow(), limits),
  );
  // 본문은 처음 열 때 마운트하고 그 뒤로는 최소화해도 유지한다.
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);
  const [preset, setPreset] = useState<WindowSizePreset | "">("");
  const [viewport, setViewport] = useState<Viewport>(viewportNow);
  // 좁은 화면에서는 하단 시트다(폭 100%, 높이 상한). 끌어 옮기기·크기 조절은 없고, 넓은 화면의 자리·크기는 그대로
  // 기억해 둔다 — 다시 넓어지면 그 자리로 돌아온다.
  const sheet = isSheetViewport(viewport);
  const shown = sheet ? sheetRect(viewport, limits) : fitRect(rect, viewport, limits);

  // 창(브라우저) 크기만 따라간다 — 표시 보정은 렌더에서 `fitRect` 로 한다. window 리스너는 layout effect 로 한 번
  // 설치한다(`frontend-react-effects.md`). `limits` 를 의존성에 두지 않아 인라인 객체가 리스너를 흔들지 않는다.
  useLayoutEffect(() => {
    const onResize = () => setViewport(viewportNow());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const commit = (next: WindowRect) => {
    setRect(next);
    writeRect(storageKey, next);
  };

  const begin = (event: PointerEvent<HTMLElement>, edges: ResizeEdges | null) => {
    if (event.button !== 0 || sheet) return;
    // 제목 줄 안의 버튼·선택 상자는 끌기가 아니라 그 조작이다.
    if (edges === null && (event.target as HTMLElement).closest("button, select") !== null) return;
    event.preventDefault();
    // jsdom 처럼 포인터 캡처가 없는 환경에서도 끌기 자체는 돈다.
    event.currentTarget.setPointerCapture?.(event.pointerId);
    gesture.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      start: shown,
      edges,
      last: shown,
    };
  };

  const move = (event: PointerEvent<HTMLElement>) => {
    const current = gesture.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    const viewport = viewportNow();
    const next =
      current.edges === null
        ? moveRect(current.start, dx, dy, viewport, limits)
        : resizeRect(current.start, current.edges, dx, dy, viewport, limits);
    current.last = next;
    setRect(next);
  };

  const end = (event: PointerEvent<HTMLElement>) => {
    const current = gesture.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    gesture.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    writeRect(storageKey, current.last);
    if (current.edges !== null) setPreset("");
  };

  const onMoveKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowRight" &&
      event.key !== "ArrowUp" &&
      event.key !== "ArrowDown"
    )
      return;
    event.preventDefault();
    commit(
      nudgeRect(shown, event.key, event.shiftKey ? NUDGE_STEP * 4 : NUDGE_STEP, viewportNow(), limits),
    );
  };

  const minimize = () => {
    onOpenChange(false);
    // 런처는 닫힌 뒤 그려진다. 이산 이벤트의 commit 이 끝난 뒤(마이크로태스크) 그 자리로 초점을 돌린다.
    queueMicrotask(() => launcherRef.current?.focus());
  };

  const restore = () => {
    onOpenChange(true);
    queueMicrotask(() => windowRef.current?.focus());
  };

  return (
    <>
      {mounted ? (
        <aside
          ref={windowRef}
          id={id}
          className={sheet ? "floating-window floating-window--sheet" : "floating-window"}
          aria-labelledby={titleId}
          tabIndex={-1}
          hidden={!open}
          style={{ left: shown.x, top: shown.y, width: shown.width, height: shown.height }}
        >
          <header
            className="floating-window__bar"
            onPointerDown={(event) => begin(event, null)}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
          >
            <h2 id={titleId} className="floating-window__title">
              {title}
            </h2>
            <div className="floating-window__actions">
              {sheet ? null : (
                <>
                  <button
                    type="button"
                    className="floating-window__icon"
                    aria-label={t("ui.floating.move")}
                    aria-describedby={moveHintId}
                    onKeyDown={onMoveKey}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3" />
                    </svg>
                  </button>
                  <span id={moveHintId} className="sr-only">
                    {t("ui.floating.move.hint")}
                  </span>
                  <select
                    className="floating-window__size ui-select"
                    aria-label={t("ui.floating.size")}
                    value={preset}
                    onChange={(event) => {
                      const next = event.target.value as WindowSizePreset | "";
                      setPreset(next);
                      if (next !== "") commit(presetRect(shown, next, viewportNow(), limits));
                    }}
                  >
                    <option value="" disabled>
                      {t("ui.floating.size")}
                    </option>
                    <option value="small">{t("ui.floating.size.small")}</option>
                    <option value="medium">{t("ui.floating.size.medium")}</option>
                    <option value="large">{t("ui.floating.size.large")}</option>
                  </select>
                </>
              )}
              <button
                type="button"
                className="floating-window__icon"
                aria-label={minimizeLabel}
                onClick={minimize}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M5 12h14" />
                </svg>
              </button>
            </div>
          </header>
          <div className="floating-window__body">{children}</div>
          {sheet ? null : HANDLES.map((handle) => (
            // 끄는 가장자리는 포인터 전용이다. 키보드 대안은 크기 프리셋이다.
            <div
              key={handle.name}
              aria-hidden="true"
              className={`floating-window__resize floating-window__resize--${handle.name}`}
              onPointerDown={(event) => begin(event, handle.edges)}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={end}
            />
          ))}
        </aside>
      ) : null}
      {open ? null : (
        <button
          ref={launcherRef}
          id={launcherId}
          type="button"
          className="floating-window__launcher ui-button ui-button--primary"
          // 런처는 창을 여닫는 토글이다. 창이 아직 마운트되지 않았으면 가리킬 대상이 없다.
          aria-expanded={false}
          aria-controls={mounted ? id : undefined}
          onClick={restore}
        >
          {launcherIcon}
          {launcherLabel}
        </button>
      )}
    </>
  );
};
