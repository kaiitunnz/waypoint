"use client";

import { useRef, useState } from "react";

import { describeHost } from "@waypoint/web-core/hostPicker";
import { readRecentHosts } from "@waypoint/web-core/recentHosts";
import type { LaunchTargetSummary } from "@waypoint/web-core/types";

import { SshConnectDialog } from "@/components/hosts/SshConnectDialog";
import { Flyout } from "@/components/ui/Flyout";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";

import styles from "./TargetChip.module.css";

const needsPassword = (target: LaunchTargetSummary | null) => target?.auth === "password" && !target.connected;

function lampFor(target: LaunchTargetSummary | null): { state: LampState; label: string } {
  if (!target) return { state: "online", label: "This host" };
  if (needsPassword(target)) return { state: "error", label: "SSH password required" };
  return { state: target.connected ? "online" : "unknown", label: "SSH target" };
}

// "on <target> ▾": where the session launches, this host or an SSH target. A
// password-auth target is connected from here.
export function TargetChip({ disabled }: { disabled?: boolean }) {
  const { host, launchTargets } = useShellData();
  const [open, setOpen] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const active = launchTargets.targets.find((target) => target.id === launchTargets.activeTargetId) ?? null;
  const hostName = describeHost(host, { recents: readRecentHosts() });
  const lamp = lampFor(active);
  const connectingTarget = launchTargets.targets.find((target) => target.id === connecting) ?? null;

  const choose = (target: LaunchTargetSummary | null) => {
    launchTargets.selectTarget(target?.id ?? "");
    setOpen(false);
    if (target && needsPassword(target)) setConnecting(target.id);
  };

  const rows: { key: string; target: LaunchTargetSummary | null; name: string }[] = [
    { key: "", target: null, name: hostName },
    ...launchTargets.targets.map((target) => ({ key: target.id, target, name: target.name })),
  ];

  return (
    <>
      <span className={styles.on}>
        on
        <button
          ref={trigger}
          type="button"
          className={styles.chip}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`Launch on ${active?.name ?? hostName}, ${lamp.label}. Change`}
          disabled={disabled}
          onClick={() => setOpen((value) => !value)}
        >
          <StatusLamp state={lamp.state} size="sm" />
          <span className={styles.name}>{active?.name ?? hostName}</span>
          <Icon name="chevron-down" size={15} strokeWidth={2} />
        </button>
      </span>
      <Flyout open={open} onClose={() => setOpen(false)} triggerRef={trigger} title="Launch on" width={300} flush>
        <ul className={styles.list} role="listbox" aria-label="Launch on">
          {rows.map((row) => {
            const selected = row.key === launchTargets.activeTargetId;
            const rowLamp = lampFor(row.target);
            return (
              <li key={row.key || "host"} role="none">
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={cx(styles.row, selected && styles.selected)}
                  onClick={() => choose(row.target)}
                >
                  <span className={styles.lamp}>
                    <StatusLamp state={rowLamp.state} size="sm" />
                  </span>
                  <span className={styles.text}>
                    <span className={styles.rowName}>{row.name}</span>
                    <span className={styles.detail}>{rowLamp.label}</span>
                  </span>
                  {selected ? <Icon name="check" size={18} /> : null}
                </button>
              </li>
            );
          })}
        </ul>
      </Flyout>
      {connectingTarget ? (
        <SshConnectDialog
          targetName={connectingTarget.name}
          onConnect={(password) => launchTargets.connect(connectingTarget.id, password)}
          onClose={() => setConnecting(null)}
        />
      ) : null}
    </>
  );
}
