import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./GlassBar.module.css";

interface GlassBarProps {
  children: ReactNode;
  placement?: "top" | "bottom";
  // `absolute` floats over its positioned parent; `fixed` over the viewport.
  position?: "absolute" | "fixed";
  className?: string;
}

// Floating chrome: translucent glass over scrolling content, padded by the
// device's safe-area inset on the edge it sits against. In-flow content is
// never glass.
export function GlassBar({ children, placement = "top", position = "absolute", className }: GlassBarProps) {
  return (
    <header
      className={cx(
        styles.bar,
        placement === "top" ? styles.top : styles.bottom,
        position === "fixed" && styles.fixed,
        className,
      )}
    >
      <div className={styles.inner}>{children}</div>
    </header>
  );
}
