"use client";

import { useRef, type KeyboardEvent } from "react";

import { cx } from "@/lib/cx";
import { Icon } from "@/components/ui/Icon";

import styles from "./ViewSwitch.module.css";

export type SessionView = "chat" | "terminal";

interface ViewSwitchProps {
  value: SessionView;
  onChange: (view: SessionView) => void;
  // Icons on mobile, words on the desktop header.
  variant: "icon" | "text";
  // A transport that is only a terminal has no chat to switch to.
  terminalOnly?: boolean;
}

export function ViewSwitch({ value, onChange, variant, terminalOnly }: ViewSwitchProps) {
  const group = useRef<HTMLDivElement>(null);
  const views: SessionView[] = terminalOnly ? ["terminal"] : ["chat", "terminal"];

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step || views.length < 2) return;
    event.preventDefault();
    const next = views[(views.indexOf(value) + step + views.length) % views.length];
    onChange(next);
    group.current?.querySelector<HTMLElement>(`[data-view="${next}"]`)?.focus();
  }

  return (
    <div ref={group} role="tablist" aria-label="View" className={cx(styles.group, variant === "text" && styles.text)} onKeyDown={onKeyDown}>
      {views.map((view) => (
        <button
          key={view}
          type="button"
          role="tab"
          aria-selected={view === value}
          aria-label={variant === "icon" ? (view === "chat" ? "Chat" : "Terminal") : undefined}
          data-view={view}
          tabIndex={view === value ? 0 : -1}
          className={cx(styles.tab, view === value && styles.on)}
          onClick={() => onChange(view)}
        >
          {variant === "icon" ? <Icon name={view === "chat" ? "chat" : "terminal"} size={18} /> : view === "chat" ? "Chat" : "Terminal"}
        </button>
      ))}
    </div>
  );
}
