"use client";

import type { ReactNode } from "react";

import { usePointerUI } from "@/lib/layout";

import styles from "./Panel.module.css";

interface PanelProps {
  title: string;
  count?: ReactNode;
  // Controls beside the heading, such as Clear all.
  actions?: ReactNode;
  children: ReactNode;
}

// A strip's panel inside its Flyout: a popover needs its own heading and
// padding, while a sheet brings both.
export function Panel({ title, count, actions, children }: PanelProps) {
  const pointer = usePointerUI();
  return (
    <div className={pointer ? styles.popover : undefined}>
      {pointer || actions ? (
        <div className={styles.head}>
          {pointer ? (
            <h2 className={styles.title}>
              {title}
              {count !== undefined ? <span className={styles.count}>{count}</span> : null}
            </h2>
          ) : null}
          {actions ? <div className={styles.actions}>{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}
