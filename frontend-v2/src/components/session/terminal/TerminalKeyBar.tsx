"use client";

import { Fragment } from "react";

import { TERMINAL_KEYS } from "@waypoint/web-core/session/terminal";

import { cx } from "@/lib/cx";

import styles from "./TerminalKeyBar.module.css";

interface TerminalKeyBarProps {
  onSend: (data: string) => void;
  onPaste: () => void;
}

export function TerminalKeyBar({ onSend, onPaste }: TerminalKeyBarProps) {
  return (
    <div className={styles.bar} role="toolbar" aria-label="Send keys to the pane">
      {TERMINAL_KEYS.map((key, index) => (
        <Fragment key={key.label}>
          {index > 0 && TERMINAL_KEYS[index - 1].group !== key.group ? (
            <span className={styles.gap} aria-hidden="true" />
          ) : null}
          <button
            type="button"
            className={cx(styles.key, /^[A-Za-z]{4,}$/.test(key.label) && styles.wide)}
            aria-label={key.title}
            title={key.title}
            // A key never takes the focus: the pane keeps it, or nothing gains it.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => ("action" in key ? onPaste() : onSend(key.data))}
          >
            {key.label}
          </button>
        </Fragment>
      ))}
    </div>
  );
}
