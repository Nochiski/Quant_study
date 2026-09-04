import { forwardRef, type ButtonHTMLAttributes } from "react";

export type ButtonTone = "primary" | "secondary" | "ghost" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  size?: "small" | "medium";
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      tone = "secondary",
      size = "medium",
      className = "",
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
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
  },
);
