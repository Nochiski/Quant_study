import type { ButtonHTMLAttributes } from "react";

export type ButtonTone = "primary" | "secondary" | "ghost" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  size?: "small" | "medium";
};

/**
 * Legacy class names (`button button--primary`) stay on the element so the Quick/Advanced
 * builder stylesheet keeps working until P6-06; the `ui-button` classes are the tokenised look.
 */
export const Button = ({
  tone = "secondary",
  size = "medium",
  className = "",
  type = "button",
  ...props
}: ButtonProps) => (
  <button
    type={type}
    className={[
      "button",
      `button--${tone}`,
      "ui-button",
      `ui-button--${tone}`,
      size === "small" ? "ui-button--small" : "",
      className,
    ]
      .filter(Boolean)
      .join(" ")}
    {...props}
  />
);
