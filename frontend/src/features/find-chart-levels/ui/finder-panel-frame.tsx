import { useEffect, useId, useRef, type ReactNode } from "react";

import { t } from "../../../shared/config";

type FinderPanelFrameProps = {
  title: string;
  /** 제목 아래 한 줄(대상·주기·상태). */
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  /** 패널 아래 고정 단추 줄(추가·취소). */
  footer?: ReactNode;
};

/**
 * 생성·근거·중첩 패널의 틀(레이어 칸 자리를 바꾸는 비모달 패널). 열리면 제목으로 초점을 옮기고, Esc·닫기는 `onClose`로
 * 레이어 칸에 돌아간다 — 연 버튼으로 초점을 돌려주는 일은 패널 상태(`returnKey`)를 가진 쪽이 한다.
 */
export const FinderPanelFrame = ({
  title,
  subtitle,
  onClose,
  children,
  footer,
}: FinderPanelFrameProps) => {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return (
    <section
      className="finder-panel"
      aria-labelledby={`${id}-title`}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      <header className="finder-panel__head">
        <div>
          <h2 id={`${id}-title`} ref={heading} tabIndex={-1}>
            {title}
          </h2>
          {subtitle === undefined ? null : (
            <p className="finder-panel__subtitle">{subtitle}</p>
          )}
        </div>
        <button
          type="button"
          className="finder-panel__close"
          aria-label={t("chart.finder.close")}
          onClick={onClose}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            aria-hidden="true"
            focusable="false"
          >
            <path d="M5 5l10 10M15 5L5 15" />
          </svg>
        </button>
      </header>
      <div className="finder-panel__body">{children}</div>
      {footer === undefined ? null : (
        <div className="finder-panel__foot">{footer}</div>
      )}
    </section>
  );
};
