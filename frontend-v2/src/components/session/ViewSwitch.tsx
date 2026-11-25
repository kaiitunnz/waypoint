"use client";

import { useRef, type KeyboardEvent } from "react";

import { Icon } from "@/components/ui/Icon";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { cx } from "@/lib/cx";

import styles from "./ViewSwitch.module.css";

export type SessionView = "chat" | "terminal";

interface ViewSwitchProps {
  value: SessionView;
  onChange: (view: SessionView) => void;
  variant: "icon" | "text";
  // A transport that is only a terminal has no chat to switch to.
  terminalOnly?: boolean;
}

export function ViewSwitch({ value, onChange, variant, terminalOnly }: ViewSwitchProps) {
  const group = useRef<HTMLDivElement>(null);
  const views: SessionView[] = terminalOnly ? ["terminal"] : ["chat", "terminal"];
  if (variant === "text") {
    const segments = views.map((view) => ({ value: view, label: view === "chat" ? "Chat" : "Terminal" }));
    return <SegmentedControl label="View" segments={segments} value={value} onChange={onChange} size="sm" />;
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step || views.length < 2) return;
    event.preventDefault();
    const next = views[(views.indexOf(value) + step + views.length) % views.length];
    onChange(next);
    group.current?.querySelector<HTMLElement>(`[data-view="${next}"]`)?.focus();
  }

  return (
    <div ref={group} role="radiogroup" aria-label="View" className={styles.group} onKeyDown={onKeyDown}>
      {views.map((view) => (
        <button
          key={view}
          type="button"
          role="radio"
          aria-checked={view === value}
          aria-label={view === "chat" ? "Chat" : "Terminal"}
          data-view={view}
          tabIndex={view === value ? 0 : -1}
          className={cx(styles.tab, view === value && styles.on)}
          onClick={() => onChange(view)}
        >
          <Icon name={view === "chat" ? "chat" : "terminal"} size={18} />
        </button>
      ))}
    </div>
  );
}
