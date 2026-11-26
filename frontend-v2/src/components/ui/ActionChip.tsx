import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./ActionChip.module.css";
import { Icon, type IconName } from "./Icon";

interface ChipContent {
  icon?: IconName;
  children: ReactNode;
}

// An outlined action for flowing content, where bare text would not read as a control.
export function ActionChip({
  icon,
  children,
  className,
  type = "button",
  ...rest
}: ChipContent & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} type={type} className={cx(styles.chip, className)}>
      {icon ? <Icon name={icon} size={14} strokeWidth={2} /> : null}
      {children}
    </button>
  );
}
