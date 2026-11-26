"use client";

import { useRouter, useSelectedLayoutSegment } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from "react";

import { useResizablePane } from "@waypoint/web-core/hooks/useResizablePane";
import { decodeNeedsId } from "@waypoint/web-core/needsYou";
import type { PaneWidthConfig } from "@waypoint/web-core/paneResize";

import { AskLocksProvider } from "@/components/sessions/AskLocks";
import { EmptyState } from "@/components/ui/EmptyState";
import { cx } from "@/lib/cx";
import { useMultiColumn } from "@/lib/layout";

import { NeedsYouContext } from "./NeedsYouContext";
import { QueuePane } from "./QueuePane";
import styles from "./NeedsYouScreen.module.css";

const QUEUE_WIDTH: PaneWidthConfig = {
  storageKey: "waypoint.inboxSidebarWidth",
  defaultWidth: 400,
  min: 300,
  max: 560,
  snap: 14,
};

// The queue and the open item. Both stay mounted in every layout, only shown or
// hidden, so a half-written answer survives a resize and the queue keeps its place.
export function NeedsYouScreen({ children }: { children: ReactNode }) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();
  const selected = segment ? decodeNeedsId(segment) : null;
  const columns = useMultiColumn();
  const pane = useResizablePane(QUEUE_WIDTH);

  // The pane scrolls for every item in turn; each one starts at its top.
  const detail = useRef<HTMLDivElement>(null);
  useEffect(() => {
    detail.current?.scrollTo(0, 0);
  }, [selected]);

  // Whether the item was opened from the queue, so that "back" is a history step.
  const previous = useRef<string | null | undefined>(undefined);
  const opened = useRef(false);
  useEffect(() => {
    if (selected === null) opened.current = false;
    else if (previous.current === null) opened.current = true;
    previous.current = selected;
  }, [selected]);

  const leave = useCallback(() => {
    if (!columns && opened.current) router.back();
    else router.replace("/inbox", { scroll: false });
  }, [columns, router]);

  const context = useMemo(() => ({ columns, selected, leave }), [columns, selected, leave]);

  return (
    <AskLocksProvider>
      <NeedsYouContext.Provider value={context}>
        <div
          className={cx(styles.screen, columns && styles.split)}
          style={columns ? ({ "--queue-width": `${pane.rendered}px` } as CSSProperties) : undefined}
        >
          <section className={cx(styles.queue, !columns && selected !== null && styles.off)} aria-label="Needs-you queue">
            <QueuePane />
            {columns ? (
              <div
                className={styles.resizer}
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize queue"
                aria-valuenow={pane.rendered}
                aria-valuemin={QUEUE_WIDTH.min}
                aria-valuemax={QUEUE_WIDTH.max}
                tabIndex={0}
                onPointerDown={pane.startDrag}
                onKeyDown={pane.onKeyDown}
              />
            ) : null}
          </section>
          <div ref={detail} className={cx(styles.detail, !columns && selected === null && styles.off)}>
            {children}
            {columns && selected === null ? (
              <div className={styles.placeholder}>
                <EmptyState title="Select an item to read it" />
              </div>
            ) : null}
          </div>
        </div>
      </NeedsYouContext.Provider>
    </AskLocksProvider>
  );
}
