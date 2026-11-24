"use client";

import { useState, type ClipboardEvent, type FormEvent, type ReactNode } from "react";

import {
  formatLastUsed,
  parseAddress,
  parsePort,
  type BackendProtocol,
  type HostGroups,
  type HostRow,
} from "@waypoint/web-core/hostPicker";
import { DEFAULT_BACKEND_PORT } from "@waypoint/web-core/tailnet";

import { Button } from "@/components/ui/Button";
import { Field, SearchInput, TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./HostPicker.module.css";

export interface TypedAddress {
  protocol: BackendProtocol;
  hostname: string;
  portText: string;
}

interface ExtraGroup {
  id: string;
  title: string;
  rows: HostRow[];
}

interface HostPickerProps {
  groups: HostGroups;
  // Groups after Offline, e.g. the host switcher's SSH targets.
  extraGroups?: ExtraGroup[];
  query: string;
  onQueryChange: (query: string) => void;
  selectedKey: string | null;
  onSelect: (row: HostRow) => void;
  // Called with a typed address. `live` reports every edit, otherwise only on submit.
  onAddress: (address: TypedAddress) => void;
  liveAddress?: boolean;
  // Open the address form with these values (e.g. the saved custom host).
  initialAddress?: TypedAddress | null;
  searchRef?: React.Ref<HTMLInputElement>;
  // Rendered between the controls and the list (a notice, a status).
  children?: ReactNode;
}

const LAMP: Record<HostRow["online"], LampState> = {
  online: "done",
  offline: "exited",
  unknown: "idle",
};

export function HostPicker({
  groups,
  extraGroups = [],
  query,
  onQueryChange,
  selectedKey,
  onSelect,
  onAddress,
  liveAddress,
  initialAddress,
  searchRef,
  children,
}: HostPickerProps) {
  const [offlineOpen, setOfflineOpen] = useState(false);
  const [now] = useState(() => Date.now());
  const searching = query.trim() !== "";
  const showOffline = offlineOpen || searching;
  const empty =
    !groups.thisDevice &&
    groups.recent.length === 0 &&
    groups.online.length === 0 &&
    groups.offline.length === 0 &&
    extraGroups.every((group) => group.rows.length === 0);

  const renderRows = (rows: HostRow[]) => (
    <ul className={styles.rows}>
      {rows.map((row) => (
        <li key={row.key}>
          <button
            type="button"
            className={cx(styles.row, row.key === selectedKey && styles.selected)}
            aria-current={row.key === selectedKey ? "true" : undefined}
            onClick={() => onSelect(row)}
          >
            <StatusLamp state={LAMP[row.online]} size="sm" label={row.online} />
            <span className={styles.name}>
              {row.name}
              {row.lastUsedAt !== null ? (
                <small>{formatLastUsed(row.lastUsedAt, now)}</small>
              ) : null}
            </span>
            <span className={cx(styles.address, "mono")}>{row.address}</span>
            <Icon name="chevron-right" size={14} strokeWidth={2} />
          </button>
        </li>
      ))}
    </ul>
  );

  return (
    <div className={styles.picker}>
      <div className={styles.controls}>
        <SearchInput
          ref={searchRef}
          aria-label="Search hosts"
          placeholder={`Search ${groups.total} hosts`}
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        <AddressEntry onAddress={onAddress} live={liveAddress} initial={initialAddress} />
      </div>
      {children}
      <div className={styles.scroll}>
        {groups.thisDevice ? (
          <section aria-label="This device">
            <h3 className={styles.head}>This device</h3>
            {renderRows([groups.thisDevice])}
          </section>
        ) : null}
        {groups.recent.length > 0 ? (
          <section aria-label="Recent">
            <h3 className={styles.head}>Recent</h3>
            {renderRows(groups.recent)}
          </section>
        ) : null}
        {groups.online.length > 0 ? (
          <section aria-label="Online">
            <h3 className={styles.head}>
              Online <span>{groups.online.length}</span>
            </h3>
            {renderRows(groups.online)}
          </section>
        ) : null}
        {extraGroups
          .filter((group) => group.rows.length > 0)
          .map((group) => (
            <section key={group.id} aria-label={group.title}>
              <h3 className={styles.head}>
                {group.title} <span>{group.rows.length}</span>
              </h3>
              {renderRows(group.rows)}
            </section>
          ))}
        {groups.offline.length > 0 ? (
          <section aria-label="Offline">
            <button
              type="button"
              className={styles.more}
              aria-expanded={showOffline}
              onClick={() => setOfflineOpen((open) => !open)}
            >
              Offline <span>{groups.offline.length}</span>
              <Icon name={showOffline ? "chevron-up" : "chevron-down"} size={15} strokeWidth={2} />
            </button>
            {showOffline ? renderRows(groups.offline) : null}
          </section>
        ) : null}
        {empty ? <p className={styles.none}>No matching hosts</p> : null}
      </div>
    </div>
  );
}

interface AddressEntryProps {
  onAddress: (address: TypedAddress) => void;
  live?: boolean;
  initial?: TypedAddress | null;
}

function AddressEntry({ onAddress, live, initial }: AddressEntryProps) {
  const [open, setOpen] = useState(initial !== null && initial !== undefined);
  const [hostText, setHostText] = useState(initial?.hostname ?? "");
  const [portText, setPortText] = useState(initial?.portText ?? String(DEFAULT_BACKEND_PORT));
  const [protocol, setProtocol] = useState<BackendProtocol>(initial?.protocol ?? "http");

  const resolve = (nextHost: string, nextPort: string, nextProtocol: BackendProtocol) => {
    const parsed = parseAddress(nextHost);
    if (!parsed) return null;
    return {
      protocol: parsed.protocol === "https" ? "https" : nextProtocol,
      hostname: parsed.hostname,
      portText: parsed.port !== null ? String(parsed.port) : nextPort,
    } satisfies TypedAddress;
  };

  const update = (nextHost: string, nextPort: string, nextProtocol = protocol) => {
    setHostText(nextHost);
    setPortText(nextPort);
    setProtocol(nextProtocol);
    if (!live) return;
    const address = resolve(nextHost, nextPort, nextProtocol);
    if (address) onAddress(address);
  };

  // A pasted URL fills the port field and drops the scheme and path.
  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const text = event.clipboardData.getData("text");
    const parsed = parseAddress(text);
    if (!parsed || (parsed.port === null && !/^https?:\/\//i.test(text))) return;
    event.preventDefault();
    update(parsed.hostname, parsed.port !== null ? String(parsed.port) : portText, parsed.protocol);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const address = resolve(hostText, portText, protocol);
    if (address && parsePort(address.portText) !== null) onAddress(address);
  };

  const portInvalid = parsePort(portText) === null;
  return (
    <form className={styles.addressForm} onSubmit={submit}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.toggleIcon}>
          <Icon name="link" size={17} />
        </span>
        Enter an address
        <Icon name={open ? "chevron-up" : "chevron-right"} size={15} strokeWidth={2} />
      </button>
      {open ? (
        <div className={styles.fields}>
          <Field label="Host" htmlFor="address-host">
            <TextInput
              id="address-host"
              mono
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              value={hostText}
              onChange={(event) => update(event.target.value, portText)}
              onPaste={onPaste}
            />
          </Field>
          <Field label="Port" htmlFor="address-port">
            <TextInput
              id="address-port"
              mono
              inputMode="numeric"
              autoComplete="off"
              aria-invalid={portInvalid || undefined}
              value={portText}
              onChange={(event) => update(hostText, event.target.value)}
            />
          </Field>
          {live ? null : (
            <Button variant="text" size="sm" type="submit" className={styles.continue} disabled={!resolve(hostText, portText, protocol) || portInvalid}>
              Continue
            </Button>
          )}
        </div>
      ) : null}
    </form>
  );
}
