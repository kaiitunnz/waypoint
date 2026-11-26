"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { sessionHref, sessionTerminalHref } from "@waypoint/web-core/shellNav";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { MenuButton } from "@/components/ui/MenuButton";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { useNeedsYou } from "./NeedsYouContext";
import styles from "./DetailFrame.module.css";

interface DetailFrameProps {
  // Who is asking: in the header on a pointer client, else above the content.
  source?: ReactNode;
  sessionId?: string;
  // Open session goes to the Terminal view, where a dialog read off the screen shows.
  terminal?: boolean;
  onDelete?: () => void;
  children: ReactNode;
}

function Back() {
  const { leave } = useNeedsYou();
  return (
    <button type="button" className={styles.back} onClick={leave}>
      <Icon name="chevron-left" size={22} />
      <span>Needs you</span>
    </button>
  );
}

export function DetailFrame({ source, sessionId, terminal, onDelete, children }: DetailFrameProps) {
  const { columns } = useNeedsYou();
  const pointer = usePointerUI();

  return (
    <article className={styles.frame}>
      <header className={cx(styles.head, columns && styles.columns)}>
        {columns ? null : <Back />}
        {pointer && source ? <div className={styles.source}>{source}</div> : null}
        <span className={styles.grow} />
        {sessionId ? (
          <Link className={styles.action} href={terminal ? sessionTerminalHref(sessionId) : sessionHref(sessionId)}>
            Open session
          </Link>
        ) : null}
        {onDelete ? (
          pointer ? (
            <Button variant="danger" size="sm" onClick={onDelete}>
              Delete
            </Button>
          ) : (
            <MenuButton
              label="Item actions"
              title="Item"
              items={[{ id: "delete", label: "Delete…", tone: "danger" }]}
              onSelect={onDelete}
            />
          )
        ) : null}
      </header>
      <div className={styles.column}>
        {source && !pointer ? <div className={styles.lead}>{source}</div> : null}
        {children}
      </div>
    </article>
  );
}

export function DetailState({ title, sessionId, action }: { title: string; sessionId?: string; action?: ReactNode }) {
  return (
    <DetailFrame sessionId={sessionId}>
      <EmptyState title={title} action={action} />
    </DetailFrame>
  );
}
