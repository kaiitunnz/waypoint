"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
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

import { ActionChip } from "@/components/ui/ActionChip";
import { Dialog } from "@/components/ui/Dialog";
import { SearchInput } from "@/components/ui/Field";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useShowExited } from "@/lib/show-exited";
import { usePointerUI } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ExitedToggle } from "./ExitedGroup";
import { MatchedTitle, RailItem, railItemClass, useRailRows } from "./RailItem";
import styles from "./SessionSwitcher.module.css";
import { EXITED_PAGE_SIZE } from "./useExitedSessions";

// The session rail's rows, searched. Exited sessions page in from the server.
export function SessionSwitcher({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const pointer = usePointerUI();
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const now = useServerNow();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();

  const [showExited, setShowExited] = useShowExited();

  const { visible, askSessionIds } = feed;
  const historyOptions = {
    host,
    token,
    query,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    onAuthError: handleAuthFailure,
  };
  // The count loads always; the Exited rows load when the group is shown.
  const counted = useSessionHistory({ ...historyOptions, withItems: false });
  const history = useSessionHistory({ ...historyOptions, withItems: true, enabled: showExited, pageSize: EXITED_PAGE_SIZE });

  const ranked = useMemo(() => rankSessionsForSwitcher(visible, askSessionIds, query), [visible, askSessionIds, query]);
  const all = useMemo(() => mergeSwitcherEntries(ranked, history.items), [ranked, history.items]);
  // Exited entries come last, so `listed` leads `all`.
  const listed = useMemo(() => all.filter((entry) => entry.section !== "exited"), [all]);
  const exited = useMemo(() => (showExited ? all.slice(listed.length) : []), [all, listed, showExited]);
  const entries = showExited ? all : listed;

  // The previews follow the rows that show them: pinned and live.
  const previewed = useMemo(
    () => listed.filter((entry) => entry.section !== "needs").map((entry) => entry.session),
    [listed],
  );
  const railRow = useRailRows(previewed);

  const exitedCount = ranked.filter((entry) => entry.section === "exited").length + (counted.total ?? 0);
  // The heading stays until the count is known to be zero, so the toggle is never unmounted under the focus.
  const exitedUnknown = counted.total === null && !counted.error;

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
    const { session, section } = entry;
    const { lamp, sub } = railRow(session, section);
    const heading = section !== "exited" && section !== entries[index - 1]?.section;
    return (
      <li key={session.id} role="presentation">
        {heading ? (
          <div className={styles.heading}>
            {SWITCHER_SECTION_LABELS[section]}{" "}
            <span className={styles.count}>{listed.filter((other) => other.section === section).length}</span>
          </div>
        ) : null}
        <button
          type="button"
          role="option"
          id={`${listId}-${session.id}`}
          aria-selected={index === selected}
          tabIndex={-1}
          className={cx(railItemClass(session, lamp, sub), styles.option)}
          onMouseMove={() => setActive(index)}
          onClick={() => open(session)}
        >
          <RailItem
            session={session}
            lamp={lamp}
            section={section}
            now={now}
            title={<MatchedTitle title={session.title} positions={entry.positions} />}
            sub={sub}
          />
        </button>
      </li>
    );
  };

  const exitedHeading =
    exitedCount > 0 || exitedUnknown ? (
      <li role="presentation">
        <ExitedToggle
          label={counted.total === null ? SWITCHER_SECTION_LABELS.exited : `${exitedCount} exited`}
          open={showExited}
          onToggle={() => setShowExited(!showExited)}
          className={styles.exitedToggle}
        />
      </li>
    ) : null;

  return (
    <Dialog open onClose={onClose} title="Switch session" size="lg" flush>
      <div className={styles.search}>
        <SearchInput
          data-autofocus={pointer ? "" : undefined}
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
            <ActionChip icon="chevron-down" tabIndex={-1} onClick={history.loadMore}>
              Show more
            </ActionChip>
          </li>
        ) : null}
      </ul>
    </Dialog>
  );
}
