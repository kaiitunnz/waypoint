import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./ActionChip.module.css";
import { Icon, type IconName } from "./Icon";

interface ChipContent {
  icon?: IconName;
  children: ReactNode;
}

// A compact action inside flowing content: Load older, Show all, Retry. It has an outline,
// hover and pressed states, and a 44px target on mobile.
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

// The same chip for an action that navigates.
export function ActionChipLink({ href, icon, children }: ChipContent & { href: string }) {
  return (
    <Link href={href} className={styles.chip}>
      {children}
      {icon ? <Icon name={icon} size={14} strokeWidth={2} /> : null}
    </Link>
  );
}
