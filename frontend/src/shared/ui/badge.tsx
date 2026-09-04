import type { HTMLAttributes, ReactNode } from "react";

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

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  children: ReactNode;
};

export const Badge = ({
  tone = "neutral",
  className = "",
  children,
  ...props
}: BadgeProps) => (
  <span
    className={`ui-badge ui-badge--${tone} ${className}`.trim()}
    data-tone={tone}
    {...props}
  >
    {GLYPHS[tone] ? (
      <span className="ui-badge__glyph" aria-hidden="true">
        {GLYPHS[tone]}
      </span>
    ) : null}
    {children}
  </span>
);
