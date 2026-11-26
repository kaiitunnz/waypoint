"use client";

import type { ReactNode } from "react";

import { useHoldRepeat } from "@waypoint/web-core/hooks/useHoldRepeat";
import type { ScrollDirection, TerminalDims } from "@waypoint/web-core/session/terminal";

import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./TerminalBar.module.css";

interface TerminalBarProps {
  // Shown for a pane that is locked until the person unlocks it.
  lock: { unlocked: boolean; onToggle: () => void } | null;
  // Shown for a pane that takes injected keys.
  onScroll: ((direction: ScrollDirection) => void) | null;
  // An interactive pane scrolled back into its scrollback.
  onJumpToLive: (() => void) | null;
  dims: TerminalDims | null;
  reconnect: { busy: boolean; run: () => void } | null;
  // Session state shown at the start of the bar, such as Focus.
  lead?: ReactNode;
  menu: ReactNode;
}

// One quiet row above the grid.
export function TerminalBar({ lock, onScroll, onJumpToLive, dims, reconnect, lead, menu }: TerminalBarProps) {
  const hold = useHoldRepeat((direction: ScrollDirection) => onScroll?.(direction));
  return (
    <div className={styles.bar} role="toolbar" aria-label="Terminal">
      {lead}
      {lock ? (
        <button
          type="button"
          className={cx(styles.lock, lock.unlocked && styles.unlocked)}
          aria-pressed={lock.unlocked}
          aria-label={lock.unlocked ? "Pane input unlocked. Lock it" : "Pane input locked. Unlock to type into the pane"}
          onClick={lock.onToggle}
        >
          <Icon name={lock.unlocked ? "unlock" : "lock"} size={15} strokeWidth={1.9} />
          {lock.unlocked ? "Unlocked" : "Locked"}
        </button>
      ) : null}
      <span className={styles.spacer} />
      {dims ? (
        <span className={styles.dims} aria-label={`${dims.cols} columns by ${dims.rows} rows`}>
          {dims.cols}×{dims.rows}
        </span>
      ) : null}
      {onJumpToLive ? (
        <ActionChip icon="arrow-down" onClick={onJumpToLive}>
          Jump to live
        </ActionChip>
      ) : null}
      {onScroll ? (
        <>
          <button type="button" className={styles.chip} aria-label="Scroll up" title="Scroll up" {...hold("up")}>
            <Icon name="chevron-up" size={16} strokeWidth={2} />
          </button>
          <button type="button" className={styles.chip} aria-label="Scroll down" title="Scroll down" {...hold("down")}>
            <Icon name="chevron-down" size={16} strokeWidth={2} />
          </button>
        </>
      ) : null}
      {reconnect ? (
        <Button variant="primary" size="sm" busy={reconnect.busy} onClick={reconnect.run}>
          {reconnect.busy ? "Reconnecting…" : "Reconnect"}
        </Button>
      ) : null}
      <span className={styles.divider} aria-hidden="true" />
      {menu}
    </div>
  );
}
