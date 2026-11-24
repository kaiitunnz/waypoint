import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { cx } from "@/lib/cx";

import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "text" | "danger";

interface CommonProps {
  variant?: ButtonVariant;
  size?: "md" | "sm";
  block?: boolean;
  busy?: boolean;
  children: ReactNode;
  className?: string;
}

type ButtonProps = CommonProps &
  ButtonHTMLAttributes<HTMLButtonElement> & { href?: undefined; ref?: Ref<HTMLButtonElement> };
type LinkProps = CommonProps & { href: string; onClick?: () => void };

// One filled primary per card or screen; everything else is a quiet or text button.
export function Button({
  variant = "secondary",
  size = "md",
  block,
  busy,
  className,
  children,
  ...rest
}: ButtonProps | LinkProps) {
  const classes = cx(
    styles.button,
    styles[variant],
    size === "sm" && styles.sm,
    block && styles.block,
    className,
  );
  if (rest.href !== undefined) {
    return (
      <Link href={rest.href} className={classes} onClick={rest.onClick}>
        {children}
      </Link>
    );
  }
  const { type = "button", disabled, ...buttonProps } = rest;
  return (
    <button
      {...buttonProps}
      type={type}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  );
}
