"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { batchDeleteInboxItems, deleteInboxItem, deleteResolvedInboxItems } from "@waypoint/web-core/api";
import { humaniseBackend, useBackendCatalog } from "@waypoint/web-core/backends";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useSelection } from "@waypoint/web-core/hooks/useSelection";
import type { InboxStatusFilter } from "@waypoint/web-core/inbox";
import type { QueueEntry, SourceFilter } from "@waypoint/web-core/needsYou";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";

import { ActionChip } from "@/components/ui/ActionChip";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { SearchInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { MenuButton } from "@/components/ui/MenuButton";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import { CheckMark } from "./CheckMark";
import { entryView } from "./entryView";
import { useNeedsYou } from "./NeedsYouContext";
import { QueueCard } from "./QueueCard";
import { QueueRow } from "./QueueRow";
import { useQueue } from "./useQueue";
import styles from "./QueuePane.module.css";

const SOURCES: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sessions", label: "Sessions" },
  { value: "inbox", label: "Inbox" },
  { value: "board", label: "Board" },
];

const STATUSES: { value: InboxStatusFilter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

const NO_ENTRIES: QueueEntry[] = [];

type Deletion = { kind: "one"; id: string } | { kind: "selected"; ids: string[] } | { kind: "resolved" };

const CONFIRM: Record<Deletion["kind"], (count: number) => { title: string; detail: string; confirm: string }> = {
  one: () => ({ title: "Delete inbox item?", detail: "This cannot be undone.", confirm: "Delete" }),
  selected: (count) => ({
    title: `Delete ${count} inbox item${count > 1 ? "s" : ""}?`,
    detail: "This cannot be undone.",
    confirm: "Delete",
  }),
  resolved: () => ({
    title: "Delete every resolved item?",
    detail: "Including any hidden by the current search. This cannot be undone.",
    confirm: "Delete resolved",
  }),
};

export function QueuePane() {
  const { host, token } = useShellData();
  const { columns, selected, leave } = useNeedsYou();
  const now = useNow(30_000);
  const catalog = useBackendCatalog(host, token, null);
  const toastFailure = useFailureToast();

  const [status, setStatus] = useState<InboxStatusFilter>("open");
  const [source, setSource] = useState<SourceFilter>("all");
  const [q, setQ] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [showToday, setShowToday] = useState(false);
  const [deletion, setDeletion] = useState<Deletion | null>(null);
  const searchField = useRef<HTMLInputElement>(null);
  const queue = useQueue(status, source, q, now);
  const { entries, todayEntries, list, resolved, counts, loaded, awaiting } = queue;

  // Select-all and bulk delete act on the rows shown, never on a hidden item.
  const shownToday = showToday ? todayEntries : NO_ENTRIES;
  const selectable = useMemo(
    () => [...entries, ...shownToday].flatMap((entry) => (entry.source === "inbox" ? [entry.item.id] : [])),
    [entries, shownToday],
  );
  const selection = useSelection(selectable);
  const { exit: exitSelect } = selection;
  useEffect(() => {
    exitSelect();
  }, [status, source, list.query, exitSelect]);

  useEffect(() => {
    if (searchOpen) searchField.current?.focus();
  }, [searchOpen]);

  const agentName = useCallback((backend: string) => humaniseBackend(backend, catalog), [catalog]);

  const closeSearch = () => {
    setQ("");
    setSearchOpen(false);
  };

  async function confirmDeletion(target: Deletion) {
    try {
      let gone: Set<string>;
      if (target.kind === "one") {
        await deleteInboxItem(host, token, target.id);
        gone = new Set([target.id]);
      } else if (target.kind === "selected") {
        gone = new Set(await batchDeleteInboxItems(host, token, target.ids));
        exitSelect();
      } else {
        gone = new Set(await deleteResolvedInboxItems(host, token));
        exitSelect();
      }
      // The live update removes them too; this is immediate.
      list.remove(gone);
      resolved.remove(gone);
      if (selected && gone.has(selected)) leave();
    } catch (error) {
      toastFailure(error, "Could not delete");
    }
  }

  const row = (entry: QueueEntry) => {
    const view = entryView(entry, agentName);
    const common = {
      entry,
      view,
      time: formatSessionTime(entry.time, now),
      selecting: selection.selecting,
      checked: selection.selected.has(entry.key),
      onToggle: selection.toggle,
      onDelete: (id: string) => setDeletion({ kind: "one", id }),
    };
    return columns ? (
      <QueueRow key={entry.key} {...common} selected={selected === entry.routeId} />
    ) : (
      <QueueCard
        key={entry.key}
        {...common}
        agent={entry.source === "sessions" ? agentName(entry.ask.session_backend) : ""}
        now={now}
      />
    );
  };

  const loading = (list.loading && !loaded) || (awaiting && entries.length === 0);
  const failed = !loaded && !list.loading && list.error !== null;
  const pageable = list.hasMore && loaded && (source === "all" || source === "inbox");
  const empty = entries.length === 0 && loaded && !awaiting;
  const confirm = deletion ? CONFIRM[deletion.kind](deletion.kind === "selected" ? deletion.ids.length : 1) : null;

  return (
    <div className={cx(styles.pane, columns && styles.columns)}>
      <header className={styles.head}>
        <h1 className={styles.title}>Needs you</h1>
        <Badge count={counts.all} className={styles.badge} />
        <span className={styles.grow} />
        <IconButton
          label={searchOpen ? "Close search" : "Search"}
          size={columns ? "sm" : "md"}
          aria-pressed={searchOpen}
          onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
        >
          <Icon name={searchOpen ? "close" : "search"} size={columns ? 18 : 21} />
        </IconButton>
        <Button
          variant="text"
          size="sm"
          className={styles.select}
          aria-pressed={selection.selecting}
          disabled={!selection.selecting && selectable.length === 0}
          onClick={() => (selection.selecting ? exitSelect() : selection.start())}
        >
          {selection.selecting ? "Done" : "Select"}
        </Button>
        <MenuButton
          label="Queue actions"
          title="Inbox"
          size={columns ? "sm" : "md"}
          items={[{ id: "resolved", label: "Delete resolved…", tone: "danger" }]}
          onSelect={() => setDeletion({ kind: "resolved" })}
        />
      </header>
      <div className={styles.filters}>
        <div className={styles.sources}>
          <SegmentedControl
            label="Source"
            size="sm"
            value={source}
            onChange={setSource}
            segments={SOURCES.map(({ value, label }) => ({
              value,
              label,
              count: status === "open" ? counts[value] : undefined,
            }))}
          />
        </div>
        <SegmentedControl label="Status" size="sm" value={status} onChange={setStatus} segments={STATUSES} />
      </div>
      {searchOpen ? (
        <div className={styles.search}>
          <SearchInput
            ref={searchField}
            value={q}
            aria-label="Search"
            placeholder="Search"
            onChange={(event) => setQ(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeSearch();
            }}
          />
        </div>
      ) : null}

      <div className={styles.scroll}>
        {failed ? (
          <div className={styles.failed} role="alert">
            <p>{list.error}</p>
            <ActionChip icon="refresh" onClick={list.retry}>
              Retry
            </ActionChip>
          </div>
        ) : null}
        <ul className={styles.list}>{entries.map(row)}</ul>
        {loading ? (
          <ul className={styles.skeleton} aria-label="Loading">
            {Array.from({ length: entries.length ? 2 : 6 }, (_, index) => (
              <li key={index} aria-hidden="true">
                <span />
                <span />
              </li>
            ))}
          </ul>
        ) : null}
        {empty ? (
          <EmptyState title={status === "open" && !list.query.trim() ? "Nothing needs you" : "Nothing here"} />
        ) : null}
        {pageable ? (
          <div className={styles.more}>
            <ActionChip disabled={list.loading} onClick={list.loadMore}>
              {list.loading ? "Loading…" : "Load more"}
            </ActionChip>
          </div>
        ) : null}
        {todayEntries.length > 0 ? (
          <section className={styles.today}>
            <button type="button" className={styles.todayHead} aria-expanded={showToday} onClick={() => setShowToday(!showToday)}>
              <span>Resolved today</span>
              <span className={styles.todayCount}>
                {todayEntries.length}
                {queue.todayMore ? "+" : ""}
              </span>
              <span className={cx(styles.chevron, showToday && styles.chevronOpen)}>
                <Icon name="chevron-right" size={16} strokeWidth={2} />
              </span>
            </button>
            {showToday ? <ul className={styles.list}>{todayEntries.map(row)}</ul> : null}
          </section>
        ) : null}
      </div>

      {selection.selecting ? (
        <div className={styles.bulk} role="toolbar" aria-label="Bulk actions">
          <button
            type="button"
            role="checkbox"
            aria-checked={selection.allState === "all" ? true : selection.allState === "some" ? "mixed" : false}
            aria-label="Select all"
            className={styles.selectAll}
            disabled={selectable.length === 0}
            onClick={selection.toggleAll}
          >
            <CheckMark checked={selection.allState === "all"} mixed={selection.allState === "some"} />
          </button>
          <span className={styles.bulkCount}>{selection.selected.size} selected</span>
          <span className={styles.grow} />
          <Button
            variant="danger"
            size="sm"
            disabled={selection.selected.size === 0}
            onClick={() => setDeletion({ kind: "selected", ids: [...selection.selected] })}
          >
            Delete
          </Button>
          <Button variant="text" size="sm" onClick={exitSelect}>
            Cancel
          </Button>
        </div>
      ) : null}

      <ConfirmDialog
        open={deletion !== null}
        title={confirm?.title ?? ""}
        detail={confirm?.detail ?? ""}
        confirmLabel={confirm?.confirm ?? ""}
        onClose={() => setDeletion(null)}
        onConfirm={async () => {
          if (deletion) await confirmDeletion(deletion);
        }}
      />
    </div>
  );
}
