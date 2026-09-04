import type { HTMLAttributes, ReactNode } from "react";

import { t } from "../config";

export type BadgeTone = "neutral" | "ok" | "warn" | "error" | "info" | "accent";

/** Glyph shown before the text so a status is readable without colour. */
const GLYPHS: Record<BadgeTone, string> = {
  neutral: "",
  ok: "✓",
  warn: "!",
  error: "✕",
  info: "i",
  accent: "●",
};

const STATUS_LABEL = {
  ok: "ui.status.ok",
  warn: "ui.status.warn",
  error: "ui.status.error",
  info: "ui.status.info",
} as const;

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  children: ReactNode;
};

/** Status tones carry a visually hidden status word so "2" reads as "오류 2" to screen readers. */
export const Badge = ({
  tone = "neutral",
  className = "",
  children,
  ...props
}: BadgeProps) => (
  <span className={`ui-badge ui-badge--${tone} ${className}`.trim()} {...props}>
    {GLYPHS[tone] ? (
      <span className="ui-badge__glyph" aria-hidden="true">
        {GLYPHS[tone]}
      </span>
    ) : null}
    {tone in STATUS_LABEL ? (
      <span className="sr-only">
        {t(STATUS_LABEL[tone as keyof typeof STATUS_LABEL])}{" "}
      </span>
    ) : null}
    {children}
  </span>
);
