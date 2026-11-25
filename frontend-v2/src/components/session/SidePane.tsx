"use client";

import { useState } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { cx } from "@/lib/cx";

import styles from "./SidePane.module.css";

type PaneTab = "overview" | "changes" | "files";

const TABS: { id: PaneTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "changes", label: "Changes" },
  { id: "files", label: "Files" },
];

// The desktop side pane. The terminal is a main-pane view, never a tab here.
export function SidePane() {
  const [tab, setTab] = useState<PaneTab>("overview");
  return (
    <aside className={styles.pane} aria-label="Session details">
      <div role="tablist" aria-label="Side pane" className={styles.tabs}>
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`pane-tab-${item.id}`}
            aria-selected={item.id === tab}
            aria-controls={`pane-panel-${item.id}`}
            className={cx(styles.tab, item.id === tab && styles.on)}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`pane-panel-${tab}`} aria-labelledby={`pane-tab-${tab}`} className={styles.body}>
        <EmptyState title="Not built yet" detail={TABS.find((item) => item.id === tab)?.label} />
      </div>
    </aside>
  );
}
