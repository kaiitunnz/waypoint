"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { useBackendCatalog } from "@waypoint/web-core/backends";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { PANEL_SHOW_EXITED_KEY, useShowExitedSessions } from "@waypoint/web-core/hooks/useShowExitedSessions";
import {
  agentModelLabel,
  formatSessionTime,
  lampWord,
  sessionLamp,
} from "@waypoint/web-core/sessionPresentation";
import { HOME_HIDDEN_SOURCES } from "@waypoint/web-core/sessionSections";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { useSessionHistory } from "@waypoint/web-core/sessionHistory";
import {
  mergeSwitcherEntries,
  rankSessionsForSwitcher,
  stepSelection,
  SWITCHER_SECTION_LABELS,
  type SwitcherEntry,
} from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Dialog } from "@/components/ui/Dialog";
import { SearchInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { useDesktop } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { PinMark } from "./PinMark";
import styles from "./SessionSwitcher.module.css";
import { EXITED_PAGE_SIZE } from "./useExitedSessions";

function highlighted(title: string, positions: number[]): ReactNode {
  if (positions.length === 0) return title;
  const marked = new Set(positions);
  return [...title].map((char, index) =>
    marked.has(index) ? (
      <b key={index} className={styles.hit}>
        {char}
      </b>
    ) : (
      char
    ),
  );
}

// Exited sessions page in from the server.
export function SessionSwitcher({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const desktop = useDesktop();
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const catalog = useBackendCatalog(host, token, null);
  const now = useNow();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();

  const [showExited, setShowExited] = useShowExitedSessions(PANEL_SHOW_EXITED_KEY);

  const { visible, askSessionIds } = feed;
  const historyOptions = {
    host,
    token,
    query,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    onAuthError: handleAuthFailure,
  };
  // The Exited rows load when the group is shown; while it is collapsed only the count does.
  const counted = useSessionHistory({ ...historyOptions, withItems: false, enabled: !showExited });
  const history = useSessionHistory({ ...historyOptions, withItems: true, enabled: showExited, pageSize: EXITED_PAGE_SIZE });

  const ranked = useMemo(() => rankSessionsForSwitcher(visible, askSessionIds, query), [visible, askSessionIds, query]);
  const all = useMemo(() => mergeSwitcherEntries(ranked, history.items), [ranked, history.items]);
  // Exited entries always come last.
  const listed = useMemo(() => all.filter((entry) => entry.section !== "exited"), [all]);
  const exited = useMemo(() => (showExited ? all.filter((entry) => entry.section === "exited") : []), [all, showExited]);
  const entries = useMemo(() => [...listed, ...exited], [listed, exited]);

  const exitedTotal = showExited ? history.total : counted.total;
  const exitedCount = ranked.filter((entry) => entry.section === "exited").length + (exitedTotal ?? 0);
  // The heading stays until the count is known to be zero, so the toggle is never unmounted under the focus.
  const exitedUnknown = exitedTotal === null && !(showExited ? history.error : counted.error);

  const selected = Math.min(active, Math.max(0, entries.length - 1));
  useEffect(() => {
    list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selected, entries]);

  function open(session: SessionRecord) {
    onClose();
    router.push(sessionHref(session.id));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        const next = stepSelection(selected, event.key === "ArrowDown" ? 1 : -1, entries.length);
        if (next !== null) setActive(next);
        break;
      }
      case "Home":
        if (!event.shiftKey && entries.length > 0 && event.ctrlKey) setActive(0);
        break;
      case "End":
        if (!event.shiftKey && entries.length > 0 && event.ctrlKey) setActive(entries.length - 1);
        break;
      case "Enter":
        event.preventDefault();
        if (entries[selected]) open(entries[selected].session);
        break;
    }
  }

  const renderEntry = (entry: SwitcherEntry, index: number) => {
    const { session } = entry;
    const lamp = sessionLamp(session, askSessionIds.has(session.id));
    const heading =
      entry.section !== "exited" && entry.section !== entries[index - 1]?.section
        ? SWITCHER_SECTION_LABELS[entry.section]
        : null;
    return (
      <li key={session.id} role="presentation">
        {heading ? <div className={styles.heading}>{heading}</div> : null}
        <button
          type="button"
          role="option"
          id={`${listId}-${session.id}`}
          aria-selected={index === selected}
          tabIndex={-1}
          className={styles.option}
          onMouseMove={() => setActive(index)}
          onClick={() => open(session)}
        >
          <StatusLamp state={lamp} label={lampWord(lamp, session.status)} />
          <span className={styles.text}>
            <span className={styles.title}>{highlighted(session.title, entry.positions)}</span>
            <span className={styles.meta}>{agentModelLabel(session, catalog)}</span>
          </span>
          <span className={styles.time}>
            {session.pinned_at && entry.section !== "pinned" ? <PinMark /> : null}
            {formatSessionTime(session.last_event_at, now)}
          </span>
        </button>
      </li>
    );
  };

  // Exited sessions sit under their own heading, which also hides and shows them.
  const exitedHeading =
    exitedCount > 0 || exitedUnknown ? (
      <li role="presentation">
        <button
          type="button"
          className={styles.groupToggle}
          aria-expanded={showExited}
          onClick={() => setShowExited(!showExited)}
        >
          <span>{SWITCHER_SECTION_LABELS.exited}</span>
          {exitedTotal === null ? null : <span className={styles.groupCount}>{exitedCount}</span>}
          <span className={showExited ? styles.open : styles.chevron}>
            <Icon name="chevron-right" size={14} strokeWidth={2} />
          </span>
        </button>
      </li>
    ) : null;

  return (
    <Dialog open onClose={onClose} title="Switch session" size="lg" flush>
      <div className={styles.search}>
        <SearchInput
          data-autofocus={desktop ? "" : undefined}
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={entries[selected] ? `${listId}-${entries[selected].session.id}` : undefined}
          aria-label="Search sessions"
          placeholder="Search sessions"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      <ul ref={list} id={listId} role="listbox" aria-label="Sessions" className={styles.list}>
        {listed.map((entry, index) => renderEntry(entry, index))}
        {exitedHeading}
        {exited.map((entry, index) => renderEntry(entry, listed.length + index))}
        {entries.length === 0 && (exitedUnknown || exitedCount === 0) ? (
          <li role="presentation" className={styles.empty}>
            {exitedUnknown ? "Searching…" : "No matching sessions"}
          </li>
        ) : null}
        {history.hasMore ? (
          <li role="presentation" className={styles.moreRow}>
            <button type="button" className={styles.more} tabIndex={-1} onClick={history.loadMore}>
              Show more
            </button>
          </li>
        ) : null}
      </ul>
    </Dialog>
  );
}
