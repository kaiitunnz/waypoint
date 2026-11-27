import type { HostOnline } from "@waypoint/web-core/hostPicker";
import type { SessionLamp } from "@waypoint/web-core/sessionPresentation";

import { cx } from "@/lib/cx";

import styles from "./StatusLamp.module.css";

// A session's state or a host's reachability. Lamps differ by shape and fill as well
// as hue, so state survives colour blindness.
export type LampState = SessionLamp | HostOnline;

interface StatusLampProps {
  state: LampState;
  size?: "md" | "sm";
  // No halo: for a lamp that already sits on a card of the attention tier.
  flat?: boolean;
  // Spoken name; a lamp with no label is decoration beside text that already says it.
  label?: string;
}

export function StatusLamp({ state, size = "md", flat, label }: StatusLampProps) {
  return (
    <span
      className={cx(styles.lamp, styles[state], size === "sm" && styles.sm, flat && styles.flat)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
