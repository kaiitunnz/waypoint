import { cx } from "@/lib/cx";

import styles from "./StatusLamp.module.css";

// Lamps differ by shape and fill as well as hue, so state survives colour blindness.
export type LampState = "needs" | "working" | "done" | "error" | "idle" | "exited";

interface StatusLampProps {
  state: LampState;
  size?: "md" | "sm";
  // Spoken name; a lamp with no label is decoration beside text that already says it.
  label?: string;
}

export function StatusLamp({ state, size = "md", label }: StatusLampProps) {
  return (
    <span
      className={cx(styles.lamp, styles[state], size === "sm" && styles.sm)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
