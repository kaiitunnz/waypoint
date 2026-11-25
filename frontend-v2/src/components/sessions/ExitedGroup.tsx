"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { MenuButton } from "@/components/ui/MenuButton";
import { StatusLamp } from "@/components/ui/StatusLamp";

import styles from "./ExitedGroup.module.css";

interface ExitedGroupProps {
  count: number;
  desktop: boolean;
  open: boolean;
  onToggle: () => void;
  // Without it the group has no menu.
  onDeleteExited?: () => void;
  children: ReactNode;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  hasMore: boolean;
  onMore: () => void;
}

// "Delete exited" lives in the menu, never as a loud button.
export function ExitedGroup({
  count,
  desktop,
  open,
  onToggle,
  onDeleteExited,
  children,
  loading,
  failed,
  onRetry,
  hasMore,
  onMore,
}: ExitedGroupProps) {
  return (
    <section className={styles.group}>
      <div className={styles.line}>
        <button type="button" className={styles.toggle} aria-expanded={open} onClick={onToggle}>
          <StatusLamp state="exited" />
          <span className={styles.label}>{desktop ? `${count} exited sessions` : `${count} exited`}</span>
          <span className={open ? styles.open : undefined}>
            <Icon name="chevron-right" size={16} strokeWidth={2} />
          </span>
        </button>
        {onDeleteExited ? (
          <MenuButton
            label="Exited sessions actions"
            title="Exited sessions"
            subtitle={`${count} exited`}
            items={[{ id: "delete", label: "Delete exited…", tone: "danger" }]}
            onSelect={onDeleteExited}
          />
        ) : null}
      </div>
      {open ? (
        <div>
          {children}
          {failed ? (
            <p className={styles.status} role="status">
              Older sessions failed to load.{" "}
              <Button variant="text" size="sm" onClick={onRetry}>
                Retry
              </Button>
            </p>
          ) : loading ? (
            <p className={styles.status} role="status">
              Loading older sessions…
            </p>
          ) : null}
          {hasMore ? (
            <div className={styles.more}>
              <Button variant="text" size="sm" onClick={onMore}>
                Show more
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
