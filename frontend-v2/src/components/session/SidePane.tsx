"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import { Inspector, type InspectorProps } from "./inspector/Inspector";
import { PanelButton } from "./PanelButton";
import styles from "./SidePane.module.css";

export type PaneTab = "overview" | "changes" | "files";

const TABS: { id: PaneTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "changes", label: "Changes" },
  { id: "files", label: "Files" },
];

interface SidePaneProps {
  // Held by the screen, so the pane drawn as a drawer comes back where it was left.
  tab: PaneTab;
  onTab: (tab: PaneTab) => void;
  // Settings is pushed over the tabs, with a way back to Overview.
  settings: ReactNode | null;
  onCloseSettings: () => void;
  // Null until the session has loaded.
  inspector: Omit<InspectorProps, "surface" | "identity"> | null;
  // For the pane drawn as a drawer.
  onClose?: () => void;
}

// The terminal is a main-pane view, never a tab here.
export function SidePane({ tab, onTab, settings, onCloseSettings, inspector, onClose }: SidePaneProps) {
  const setTab = onTab;
  const tabs = useRef<Record<PaneTab, HTMLButtonElement | null>>({ overview: null, changes: null, files: null });

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const index = TABS.findIndex((item) => item.id === tab);
    const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const target = TABS[(next + TABS.length) % TABS.length].id;
    setTab(target);
    tabs.current[target]?.focus();
  };
  const close = onClose ? (
    <span className={styles.close}>
      <PanelButton panel="pane" size="sm" control={{ open: true, toggle: onClose }} />
    </span>
  ) : null;
  if (settings) {
    return (
      <aside className={styles.pane} aria-label="Session settings">
        <div className={styles.top}>
          <span className={styles.back}>
            <IconButton label="Back to overview" size="sm" onClick={onCloseSettings}>
              <Icon name="chevron-left" size={20} />
            </IconButton>
          </span>
          <h2 className={styles.heading}>Settings</h2>
          {close}
        </div>
        <div className={styles.body}>{settings}</div>
      </aside>
    );
  }
  return (
    <aside className={styles.pane} aria-label="Session details">
      <div className={styles.top}>
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
        {close}
      </div>
      <div role="tabpanel" id="pane-panel" tabIndex={tab === "overview" ? -1 : 0} aria-labelledby={`pane-tab-${tab}`} className={styles.body}>
        {tab === "overview" ? (
          inspector ? (
            <Inspector surface="pane" {...inspector} />
          ) : null
        ) : (
          <EmptyState title="Not built yet" detail={TABS.find((item) => item.id === tab)?.label} />
        )}
      </div>
    </aside>
  );
}
