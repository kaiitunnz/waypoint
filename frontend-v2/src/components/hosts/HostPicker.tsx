"use client";

import { useState, type FormEvent } from "react";

import {
  isCompleteHost,
  parsePort,
  portAfterHostEdit,
  resolveTypedAddress,
  type TypedAddress,
} from "@waypoint/web-core/backendAddress";
import {
  formatLastUsed,
  pageDevice,
  type HostGroups,
  type HostRow,
} from "@waypoint/web-core/hostPicker";
import { DEFAULT_BACKEND_PORT } from "@waypoint/web-core/tailnet";

import { Button } from "@/components/ui/Button";
import { Field, SearchInput, TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./HostPicker.module.css";

interface ExtraGroup {
  id: string;
  title: string;
  rows: HostRow[];
}

interface HostPickerProps {
  groups: HostGroups;
  // Groups listed between Online and Offline, e.g. the host switcher's SSH targets.
  extraGroups?: ExtraGroup[];
  query: string;
  onQueryChange: (query: string) => void;
  selectedKey: string | null;
  onSelect: (row: HostRow) => void;
  // Called with a typed address: on every edit with `liveAddress`, otherwise on submit.
  onAddress: (address: TypedAddress | null) => void;
  liveAddress?: boolean;
  // The port field's value, when the caller keeps it in step with another port field.
  port?: { text: string; onChange: (text: string) => void };
  searchRef?: React.Ref<HTMLInputElement>;
  // Level of the group headings, so they nest under the page's own headings.
  headingLevel?: "h2" | "h3";
}

export function HostPicker({
  groups,
  extraGroups = [],
  query,
  onQueryChange,
  selectedKey,
  onSelect,
  onAddress,
  liveAddress,
  port,
  searchRef,
  headingLevel: Heading = "h3",
}: HostPickerProps) {
  const [offlineOpen, setOfflineOpen] = useState(false);
  // A row pick clears the typed address, so the form never contradicts the selection.
  const [picks, setPicks] = useState(0);
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
            className={cx(styles.row, row.online === "offline" && styles.offline, row.key === selectedKey && styles.selected)}
            aria-current={row.key === selectedKey ? "true" : undefined}
            onClick={() => {
              setPicks((count) => count + 1);
              onSelect(row);
            }}
          >
            <StatusLamp state={row.online} size="sm" label={row.online} />
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
          placeholder="Search hosts"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        <AddressEntry key={picks} onAddress={onAddress} live={liveAddress} port={port} />
      </div>
      <div className={styles.scroll}>
        {groups.thisDevice ? (
          <section aria-label="This device">
            <Heading className={styles.head}>This device</Heading>
            {renderRows([groups.thisDevice])}
          </section>
        ) : null}
        {groups.recent.length > 0 ? (
          <section aria-label="Recent">
            <Heading className={styles.head}>Recent</Heading>
            {renderRows(groups.recent)}
          </section>
        ) : null}
        {groups.online.length > 0 ? (
          <section aria-label="Online">
            <Heading className={styles.head}>
              Online <span>{groups.online.length}</span>
            </Heading>
            {renderRows(groups.online)}
          </section>
        ) : null}
        {extraGroups
          .filter((group) => group.rows.length > 0)
          .map((group) => (
            <section key={group.id} aria-label={group.title}>
              <Heading className={styles.head}>
                {group.title} <span>{group.rows.length}</span>
              </Heading>
              {renderRows(group.rows)}
            </section>
          ))}
        {groups.offline.length > 0 ? (
          <section aria-label="Offline">
            {searching ? (
              <Heading className={styles.head}>
                Offline <span>{groups.offline.length}</span>
              </Heading>
            ) : (
              <button
                type="button"
                className={styles.more}
                aria-expanded={offlineOpen}
                onClick={() => setOfflineOpen((open) => !open)}
              >
                Offline <span>{groups.offline.length}</span>
                <Icon name={offlineOpen ? "chevron-up" : "chevron-down"} size={15} strokeWidth={2} />
              </button>
            )}
            {showOffline ? renderRows(groups.offline) : null}
          </section>
        ) : null}
        {empty ? <p className={styles.none}>No matching hosts</p> : null}
      </div>
    </div>
  );
}

interface AddressEntryProps {
  onAddress: (address: TypedAddress | null) => void;
  live?: boolean;
  port?: { text: string; onChange: (text: string) => void };
}

function AddressEntry({ onAddress, live, port }: AddressEntryProps) {
  const [open, setOpen] = useState(false);
  const [hostText, setHostText] = useState("");
  const [ownPortText, setOwnPortText] = useState(String(DEFAULT_BACKEND_PORT));
  const portText = port ? port.text : ownPortText;
  const setPortText = port ? port.onChange : setOwnPortText;
  const pageProtocol = pageDevice().protocol;

  const update = (nextHost: string, nextPort: string) => {
    setHostText(nextHost);
    setPortText(nextPort);
    if (live) onAddress(resolveTypedAddress(nextHost, nextPort, pageProtocol));
  };

  const address = resolveTypedAddress(hostText, portText, pageProtocol);
  const portInvalid = parsePort(portText) === null;
  const complete = address !== null && !portInvalid && isCompleteHost(address.hostname);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (complete) onAddress(address);
  };

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
              onChange={(event) =>
                update(event.target.value, portAfterHostEdit(hostText, event.target.value, portText))
              }
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
            <Button
              variant="text"
              size="sm"
              type="submit"
              className={styles.continue}
              disabled={!complete}
            >
              Continue
            </Button>
          )}
        </div>
      ) : null}
    </form>
  );
}
