import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { cx } from "@/lib/cx";

import styles from "./IconButton.module.css";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  label: string;
  size?: "md" | "sm";
  children: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

export function IconButton({ label, size = "md", className, children, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      title={label}
      className={cx(styles.button, size === "sm" && styles.sm, className)}
    >
      {children}
    </button>
  );
}
