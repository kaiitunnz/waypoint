"use client";

import { useRef, useState } from "react";

import { describeHost } from "@waypoint/web-core/hostPicker";
import { readRecentHosts } from "@waypoint/web-core/recentHosts";

import { Flyout } from "@/components/ui/Flyout";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { useShellData } from "@/lib/shell-data";

import { SshConnectDialog } from "./SshConnectDialog";
import styles from "./HostSwitcher.module.css";
import { SwitcherPanel } from "./SwitcherPanel";

export function HostSwitcher() {
  const { host, connection, launchTargets } = useShellData();
  const [open, setOpen] = useState(false);
  const [recents, setRecents] = useState(readRecentHosts);
  // The SSH password dialog stacks over the open popover or sheet, which stays
  // put underneath it and does not dismiss.
  const [connecting, setConnecting] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const activeTarget = launchTargets.targets.find((target) => target.id === launchTargets.activeTargetId) ?? null;
  const sshBlocked = activeTarget?.auth === "password" && !activeTarget.connected;
  const lamp: { state: LampState; label: string } = sshBlocked
    ? { state: "error", label: "SSH disconnected" }
    : connection === "open"
      ? { state: "online", label: "Live" }
      : { state: "working", label: "Connecting" };
  const name = describeHost(host, { targetName: activeTarget?.name, recents });

  const label = "Switch host";
  const panel = (
    <SwitcherPanel
      recents={recents}
      onDone={() => {
        setOpen(false);
        trigger.current?.focus();
      }}
      onConnectRequest={setConnecting}
    />
  );
  const requestOpen = (next: boolean) => {
    if (!next && connecting) return;
    setOpen(next);
  };
  const connectingTarget = launchTargets.targets.find((target) => target.id === connecting) ?? null;

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={styles.chip}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${name}, ${lamp.label}. ${label}`}
        onClick={() => {
          if (!open) setRecents(readRecentHosts());
          setOpen((value) => !value);
        }}
      >
        <StatusLamp state={lamp.state} size="sm" />
        <span className={styles.name}>{name}</span>
        <Icon name="chevron-down" size={15} strokeWidth={2} />
      </button>
      <Flyout open={open} onClose={() => requestOpen(false)} triggerRef={trigger} title={label} width={380} flush>
        {panel}
      </Flyout>
      {connecting && connectingTarget ? (
        <SshConnectDialog
          targetName={connectingTarget.name}
          onConnect={(password) => launchTargets.connect(connecting, password)}
          onClose={() => setConnecting(null)}
        />
      ) : null}
    </>
  );
}
