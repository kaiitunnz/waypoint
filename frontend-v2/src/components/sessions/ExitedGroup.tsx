"use client";

import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { MenuButton } from "@/components/ui/MenuButton";
import { StatusLamp } from "@/components/ui/StatusLamp";

import styles from "./ExitedGroup.module.css";
import { PagingFooter } from "./PagingFooter";
import type { ExitedPaging } from "./useExitedSessions";

interface ExitedGroupProps {
  count: number;
  // The heading reads "3 exited", not "3 exited sessions".
  short?: boolean;
  open: boolean;
  onToggle: () => void;
  onDeleteExited?: () => void;
  children: ReactNode;
  paging: ExitedPaging;
}

// "Delete exited" lives in the menu, never as a loud button.
export function ExitedGroup({ count, short, open, onToggle, onDeleteExited, children, paging }: ExitedGroupProps) {
  return (
    <section className={styles.group}>
      <div className={styles.line}>
        <button type="button" className={styles.toggle} aria-expanded={open} onClick={onToggle}>
          <StatusLamp state="exited" />
          <span className={styles.label}>{short ? `${count} exited` : `${count} exited sessions`}</span>
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
          <PagingFooter paging={paging} loadingText="Loading older sessions…" />
        </div>
      ) : null}
    </section>
  );
}
