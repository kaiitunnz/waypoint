"use client";

import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { MenuButton } from "@/components/ui/MenuButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./ExitedGroup.module.css";
import { PagingFooter } from "./PagingFooter";
import type { ExitedPaging } from "./useExitedSessions";

interface ExitedGroupProps {
  count: number;
  // Shortens the heading to "3 exited".
  short?: boolean;
  divided?: boolean;
  open: boolean;
  onToggle: () => void;
  onDeleteExited?: () => void;
  children: ReactNode;
  paging: ExitedPaging;
}

interface ExitedToggleProps {
  label: string;
  open: boolean;
  onToggle: () => void;
  className?: string;
}

// The Exited group's heading, which shows or hides every exited session.
export function ExitedToggle({ label, open, onToggle, className }: ExitedToggleProps) {
  return (
    <button type="button" className={cx(styles.toggle, className)} aria-expanded={open} onClick={onToggle}>
      <StatusLamp state="exited" />
      <span className={styles.label}>{label}</span>
      <span className={open ? styles.open : undefined}>
        <Icon name="chevron-right" size={16} strokeWidth={2} />
      </span>
    </button>
  );
}

// "Delete exited" lives in the menu, never as a loud button.
export function ExitedGroup({ count, short, divided, open, onToggle, onDeleteExited, children, paging }: ExitedGroupProps) {
  return (
    <section className={cx(styles.group, divided && styles.divided)}>
      <div className={styles.line}>
        <ExitedToggle
          label={short ? `${count} exited` : `${count} exited sessions`}
          open={open}
          onToggle={onToggle}
        />
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
          <PagingFooter paging={paging} loadingText="Loading older sessions…" />
        </div>
      ) : null}
    </section>
  );
}
