import type { ButtonHTMLAttributes } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary";
};

export const Button = ({
  tone = "secondary",
  className = "",
  ...props
}: ButtonProps) => (
  <button className={`button button--${tone} ${className}`.trim()} {...props} />
);
