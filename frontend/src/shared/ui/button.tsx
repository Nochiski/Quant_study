import type { ButtonHTMLAttributes } from "react";

export type ButtonTone = "primary" | "secondary" | "ghost" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  size?: "small" | "medium";
};

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
