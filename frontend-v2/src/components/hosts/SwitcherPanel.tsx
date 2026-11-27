"use client";

import { useMemo, useState } from "react";

import { buildBackendUrl, isCompleteHost, parsePort, splitBackendUrl, type TypedAddress } from "@waypoint/web-core/backendAddress";
import { useBackendProbe } from "@waypoint/web-core/hooks/useBackendProbe";
import { useTailnetSnapshot } from "@waypoint/web-core/hooks/useTailnetSnapshot";
import {
  buildHostGroups,
  buildSshRows,
  isPageDeviceBackend,
  pageDevice,
  rowKeyFor,
  type HostRow,
} from "@waypoint/web-core/hostPicker";
import type { RecentHost } from "@waypoint/web-core/recentHosts";
import { SETTINGS_HREF } from "@waypoint/web-core/shellNav";
import type { LaunchTargetSummary } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { ThemeRow } from "@/components/settings/DeviceSections";
import { LinkRow, SettingsSection } from "@/components/ui/Settings";
import { switchHost } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { PROBE_LABEL } from "@/lib/probe";
import { useShellData } from "@/lib/shell-data";
import { sshLamp } from "@/lib/ssh";

import { HostPicker } from "./HostPicker";
import styles from "./SwitcherPanel.module.css";

type Selection = { kind: "host"; url: string; name: string } | { kind: "ssh"; targetId: string; name: string };

interface SwitcherPanelProps {
  recents: RecentHost[];
  onDone: () => void;
  onConnectRequest: (targetId: string) => void;
}

export function SwitcherPanel({ recents, onDone, onConnectRequest }: SwitcherPanelProps) {
  const { host, token, launchTargets } = useShellData();
  const pointer = usePointerUI();
  const { snapshot } = useTailnetSnapshot({ kind: "backend", host, token });
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [sshError, setSshError] = useState("");

  const activeId = launchTargets.activeTargetId;
  // The snapshot is the signed-in backend's view: its `is_self` peer is This device
  // only when that backend runs on the machine serving the page.
  const groups = useMemo(() => {
    const device = pageDevice();
    return buildHostGroups({
      ...device,
      snapshot,
      selfIsPageHost: isPageDeviceBackend(host, device.pageHost),
      recents,
      query,
    });
  }, [host, snapshot, recents, query]);
  const sshRows = useMemo(
    () =>
      buildSshRows(launchTargets.targets, query).map((row) => ({
        ...row,
        online: sshLamp(launchTargets.targets.find((t) => t.id === row.targetId)),
      })),
    [launchTargets.targets, query],
  );

  const current: Selection = activeId
    ? { kind: "ssh", targetId: activeId, name: "" }
    : { kind: "host", url: host, name: "" };
  const picked = selection ?? current;
  // An address of this machine and its This device row are the same host.
  const isCurrentHost = (url: string) => rowKeyFor(groups, url) === rowKeyFor(groups, host);
  const selectedKey = picked.kind === "ssh" ? `ssh:${picked.targetId}` : rowKeyFor(groups, picked.url);
  const dirty = picked.kind === "ssh" ? picked.targetId !== activeId : activeId !== "" || !isCurrentHost(picked.url);

  const target = picked.kind === "ssh" ? launchTargets.targets.find((t) => t.id === picked.targetId) ?? null : null;
  const pickedHost = picked.kind === "host" ? splitBackendUrl(picked.url)?.hostname : undefined;
  const probe = useBackendProbe(
    picked.kind === "host" && !isCurrentHost(picked.url) && pickedHost && isCompleteHost(pickedHost)
      ? picked.url
      : null,
  );

  const select = (row: HostRow) => {
    setSshError("");
    setSelection(
      row.kind === "ssh" && row.targetId
        ? { kind: "ssh", targetId: row.targetId, name: row.name }
        : { kind: "host", url: row.url, name: row.name },
    );
  };

  const typed = (address: TypedAddress | null) => {
    const port = address ? parsePort(address.portText) : null;
    // A half-typed address is not a choice yet.
    if (!address || port === null || !isCompleteHost(address.hostname)) {
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
    } else if (isCurrentHost(picked.url)) {
      launchTargets.selectTarget("");
    } else if (splitBackendUrl(picked.url)) {
      switchHost(picked.url, picked.name);
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
        <p className={styles.status} data-state={probe} role="status">
          {picked.kind === "host" ? PROBE_LABEL[probe] : ""}
        </p>
        {target?.auth === "password" ? (
          <SshTargetStatus
            target={target}
            onConnect={() => onConnectRequest(target.id)}
            onDisconnect={() => void launchTargets.disconnect(target.id).then((failure) => setSshError(failure ?? ""))}
          />
        ) : null}
        {sshError ? (
          <p className={styles.error} role="alert">
            {sshError}
          </p>
        ) : null}
        {pointer ? null : <DeviceRows onDone={onDone} />}
        <Button variant="primary" block disabled={!dirty} onClick={apply}>
          Switch
        </Button>
      </div>
    </div>
  );
}

function SshTargetStatus({
  target,
  onConnect,
  onDisconnect,
}: {
  target: LaunchTargetSummary;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  return (
    <div className={styles.status} data-state={target.connected ? "reachable" : "idle"}>
      <span>{target.connected ? "SSH connected" : "SSH password required"}</span>
      <Button variant="text" size="sm" onClick={target.connected ? onDisconnect : onConnect}>
        {target.connected ? "Disconnect" : "Connect"}
      </Button>
    </div>
  );
}

// Touch has no nav rail, so the sheet carries the quick theme choice and the way to settings.
function DeviceRows({ onDone }: { onDone: () => void }) {
  return (
    <SettingsSection>
      <ThemeRow />
      <LinkRow label="Settings" href={SETTINGS_HREF} onNavigate={onDone} />
    </SettingsSection>
  );
}
