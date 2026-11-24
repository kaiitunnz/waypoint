"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { useBackendCatalog } from "@waypoint/web-core/backends";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { agentModelLabel, formatSessionTime, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import { HOME_HIDDEN_SOURCES, visibleSessions } from "@waypoint/web-core/sessionSections";
import { useSessionHistory } from "@waypoint/web-core/sessionHistory";
import {
  rankSessionsForSwitcher,
  stepSelection,
  SWITCHER_SECTION_LABELS,
  type SwitcherEntry,
} from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Modal, Sheet } from "@/components/ui/Dialog";
import { SearchInput } from "@/components/ui/Field";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { useDesktop } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import styles from "./SessionSwitcher.module.css";

const HISTORY_PAGE = 10;

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

// Jump to a session: fuzzy over name, repo, branch and directory, grouped like the
// home (Needs you, Pinned, Live, Exited). Exited sessions page in from the server.
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

  const askSessionIds = useMemo(
    () => new Set((feed.pendingAsks?.items ?? []).map((ask) => ask.session_id)),
    [feed.pendingAsks],
  );
  const history = useSessionHistory({
    host,
    token,
    query,
    withItems: true,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    pageSize: HISTORY_PAGE,
    onAuthError: handleAuthFailure,
  });

  const entries = useMemo<SwitcherEntry[]>(() => {
    const ranked = rankSessionsForSwitcher(visibleSessions(feed.sessions ?? []), askSessionIds, query);
    const known = new Set(ranked.map((entry) => entry.session.id));
    // The server already matched these, so they are not filtered again.
    const older = history.items
      .filter((session) => !known.has(session.id))
      .map((session): SwitcherEntry => ({ session, section: "exited", positions: [] }));
    return [...ranked, ...older];
  }, [feed.sessions, askSessionIds, query, history.items]);

  const selected = Math.min(active, Math.max(0, entries.length - 1));
  useEffect(() => {
    list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selected, entries]);

  function open(session: SessionRecord) {
    onClose();
    router.push(`/session/${encodeURIComponent(session.id)}`);
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

  const Surface = desktop ? Modal : Sheet;
  let previous: string | null = null;
  return (
    <Surface open onClose={onClose} title="Switch session" size="lg" flush>
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
        {entries.map((entry, index) => {
          const { session } = entry;
          const heading = entry.section !== previous ? SWITCHER_SECTION_LABELS[entry.section] : null;
          previous = entry.section;
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
                <StatusLamp state={sessionLamp(session, askSessionIds.has(session.id))} />
                <span className={styles.text}>
                  <span className={styles.title}>{highlighted(session.title, entry.positions)}</span>
                  <span className={styles.meta}>{agentModelLabel(session, catalog)}</span>
                </span>
                <span className={styles.time}>{formatSessionTime(session.last_event_at, now)}</span>
              </button>
            </li>
          );
        })}
        {entries.length === 0 ? (
          <li role="presentation" className={styles.empty}>
            {history.pending || history.loading ? "Searching…" : "No matching sessions"}
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
    </Surface>
  );
}
