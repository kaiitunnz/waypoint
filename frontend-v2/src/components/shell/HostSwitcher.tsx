"use client";

import { useMemo, useRef, useState } from "react";

import { useBackendProbe } from "@waypoint/web-core/hooks/useBackendProbe";
import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";
import { useTailnetSnapshot } from "@waypoint/web-core/hooks/useTailnetSnapshot";
import {
  buildHostGroups,
  buildSshRows,
  describeHost,
  hostKey,
  isCompleteHost,
  buildBackendUrl,
  parsePort,
  splitBackendUrl,
  type HostRow,
} from "@waypoint/web-core/hostPicker";
import { readRecentHosts } from "@waypoint/web-core/recentHosts";
import { deploymentBackendPort, sameBackendUrl } from "@waypoint/web-core/tailnet";

import { HostPicker, type TypedAddress } from "@/components/hosts/HostPicker";
import { Button } from "@/components/ui/Button";
import { Sheet } from "@/components/ui/Dialog";
import { Icon } from "@/components/ui/Icon";
import { Popover } from "@/components/ui/Popover";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { switchHost } from "@/lib/auth";
import { DESKTOP_QUERY } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";
import { useTheme } from "@/lib/theme";

import { SshConnectDialog } from "./SshConnectDialog";
import styles from "./HostSwitcher.module.css";

// The host chip: which backend (and SSH target) the app is driving, and the way
// to change it. Opens a popover on desktop and a sheet on mobile.
export function HostSwitcher() {
  const { host, connection, launchTargets } = useShellData();
  const desktop = useMediaQuery(DESKTOP_QUERY);
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
      ? { state: "done", label: "Live" }
      : { state: connection === "connecting" || connection === "reconnecting" ? "working" : "idle", label: "Connecting" };
  const name = describeHost(host, { targetName: activeTarget?.name, recents });

  const label = "Switch host";
  const panel = (
    <SwitcherPanel recents={recents} onDone={() => setOpen(false)} onConnectRequest={setConnecting} />
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
      {desktop ? (
        <Popover open={open} onOpenChange={requestOpen} triggerRef={trigger} label={label} width={380}>
          {open ? panel : null}
        </Popover>
      ) : (
        <Sheet open={open} onClose={() => requestOpen(false)} title={label} flush>
          {panel}
        </Sheet>
      )}
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

type Selection = { kind: "host"; url: string; name: string } | { kind: "ssh"; targetId: string; name: string };

interface SwitcherPanelProps {
  recents: ReturnType<typeof readRecentHosts>;
  onDone: () => void;
  onConnectRequest: (targetId: string) => void;
}

function SwitcherPanel({ recents, onDone, onConnectRequest }: SwitcherPanelProps) {
  const { host, token, launchTargets } = useShellData();
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const { theme, setTheme } = useTheme();
  const { snapshot } = useTailnetSnapshot({ kind: "backend", host, token });
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);

  const activeId = launchTargets.activeTargetId;
  const groups = useMemo(
    () =>
      buildHostGroups({
        pageHost: window.location.hostname || "localhost",
        protocol: window.location.protocol === "https:" ? "https" : "http",
        devicePort: deploymentBackendPort(),
        snapshot,
        recents,
        query,
      }),
    [snapshot, recents, query],
  );
  const sshRows = useMemo(() => buildSshRows(launchTargets.targets, query), [launchTargets.targets, query]);

  // What the panel shows as selected: the person's pick, else what is active now.
  const current: Selection = activeId
    ? { kind: "ssh", targetId: activeId, name: "" }
    : { kind: "host", url: host, name: "" };
  const picked = selection ?? current;
  const selectedKey = picked.kind === "ssh" ? `ssh:${picked.targetId}` : hostKey(picked.url);
  const dirty =
    picked.kind === "ssh" ? picked.targetId !== activeId : activeId !== "" || !sameBackendUrl(picked.url, host);

  const target = picked.kind === "ssh" ? launchTargets.targets.find((t) => t.id === picked.targetId) ?? null : null;
  const pickedHost = picked.kind === "host" ? splitBackendUrl(picked.url)?.hostname : undefined;
  const probe = useBackendProbe(
    picked.kind === "host" && !sameBackendUrl(picked.url, host) && pickedHost && isCompleteHost(pickedHost)
      ? picked.url
      : null,
  );

  const select = (row: HostRow) =>
    setSelection(
      row.kind === "ssh" && row.targetId
        ? { kind: "ssh", targetId: row.targetId, name: row.name }
        : { kind: "host", url: row.url, name: row.name },
    );

  const typed = (address: TypedAddress) => {
    const port = parsePort(address.portText);
    // A half-typed address is not a choice yet.
    if (port === null || !isCompleteHost(address.hostname)) {
      setSelection(null);
      return;
    }
    setSelection({
      kind: "host",
      url: buildBackendUrl({ protocol: address.protocol, hostname: address.hostname, port }),
      name: address.hostname,
    });
  };

  const apply = () => {
    if (picked.kind === "ssh") {
      launchTargets.selectTarget(picked.targetId);
    } else if (sameBackendUrl(picked.url, host)) {
      launchTargets.selectTarget("");
    } else if (splitBackendUrl(picked.url)) {
      switchHost(picked.url);
    }
    onDone();
  };

  return (
    <div className={styles.panel}>
      <HostPicker
        groups={groups}
        extraGroups={[{ id: "ssh", title: "SSH targets", rows: sshRows }]}
        query={query}
        onQueryChange={setQuery}
        selectedKey={selectedKey}
        onSelect={select}
        onAddress={typed}
        liveAddress
      />
      <div className={styles.footer}>
        {picked.kind === "host" && probe !== "idle" ? (
          <p className={styles.status} data-state={probe} role="status">
            {probe === "checking" ? "Checking" : probe === "reachable" ? "Reachable" : "Unreachable"}
          </p>
        ) : null}
        {target?.auth === "password" ? (
          <div className={styles.status} data-state={target.connected ? "reachable" : "idle"}>
            <span>{target.connected ? "SSH connected" : "SSH password required"}</span>
            {target.connected ? (
              <Button variant="text" size="sm" onClick={() => void launchTargets.disconnect(target.id)}>
                Disconnect
              </Button>
            ) : (
              <Button variant="text" size="sm" onClick={() => onConnectRequest(target.id)}>
                Connect
              </Button>
            )}
          </div>
        ) : null}
        {!desktop ? (
          <div className={styles.theme}>
            <span>Theme</span>
            <SegmentedControl
              label="Theme"
              value={theme}
              onChange={setTheme}
              segments={[
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
              ]}
            />
          </div>
        ) : null}
        <Button variant="primary" block disabled={!dirty} onClick={apply}>
          Switch
        </Button>
      </div>
    </div>
  );
}
