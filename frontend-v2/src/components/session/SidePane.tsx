"use client";

import { useRef, useState, type KeyboardEvent } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { cx } from "@/lib/cx";

import styles from "./SidePane.module.css";

type PaneTab = "overview" | "changes" | "files";

const TABS: { id: PaneTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "changes", label: "Changes" },
  { id: "files", label: "Files" },
];

// The terminal is a main-pane view, never a tab here.
export function SidePane() {
  const [tab, setTab] = useState<PaneTab>("overview");
  const tabs = useRef<Record<PaneTab, HTMLButtonElement | null>>({ overview: null, changes: null, files: null });

  const onKeyDown = (event: KeyboardEvent) => {
    const index = TABS.findIndex((item) => item.id === tab);
    const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const target = TABS[(next + TABS.length) % TABS.length].id;
    setTab(target);
    tabs.current[target]?.focus();
  };
  return (
    <aside className={styles.pane} aria-label="Session details">
      <div role="tablist" aria-label="Side pane" className={styles.tabs} onKeyDown={onKeyDown}>
        {TABS.map((item) => (
          <button
            key={item.id}
            ref={(node) => {
              tabs.current[item.id] = node;
            }}
            type="button"
            role="tab"
            id={`pane-tab-${item.id}`}
            aria-selected={item.id === tab}
            aria-controls={item.id === tab ? "pane-panel" : undefined}
            tabIndex={item.id === tab ? 0 : -1}
            className={cx(styles.tab, item.id === tab && styles.on)}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="pane-panel" aria-labelledby={`pane-tab-${tab}`} className={styles.body}>
        <EmptyState title="Not built yet" detail={TABS.find((item) => item.id === tab)?.label} />
      </div>
    </aside>
  );
}
