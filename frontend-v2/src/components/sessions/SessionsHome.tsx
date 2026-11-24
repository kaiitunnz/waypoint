"use client";

import { useCallback, useMemo, useState } from "react";

import { useBackendCatalog, humaniseBackend } from "@waypoint/web-core/backends";
import { useBoardSummary } from "@waypoint/web-core/hooks/useBoardSummary";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import { useUsageSummary } from "@waypoint/web-core/hooks/useUsageSummary";
import { agentModelLabel, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import {
  HOME_HIDDEN_SOURCES,
  buildSections,
  searchSessions,
  visibleSessions,
} from "@waypoint/web-core/sessionSections";
import { useMergedSessionPage, useSessionHistory } from "@waypoint/web-core/sessionHistory";
import { summarizeScheduled } from "@waypoint/web-core/usageSummary";
import type { PendingAsk, SessionRecord } from "@waypoint/web-core/types";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { List } from "@/components/ui/List";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ExitedGroup } from "./ExitedGroup";
import { HomeHeader } from "./HomeHeader";
import { NeedsYouSection } from "./NeedsYouSection";
import { NewSessionDialog } from "./NewSessionDialog";
import { RenameDialog } from "./RenameDialog";
import { SessionMenu, type SessionAction } from "./SessionMenu";
import { CompactRow, LiveRow, LiveTile, ResultRow } from "./SessionRows";
import { useSwitcher } from "./SwitcherProvider";
import { BoardSummaryCard, LimitsReadout, LimitsStrip, ScheduledSummaryList } from "./Summaries";
import styles from "./SessionsHome.module.css";

const PAGE_SIZE = 10;

type Dialog =
  | { kind: "rename"; session: SessionRecord }
  | { kind: "terminate"; session: SessionRecord }
  | { kind: "delete"; session: SessionRecord }
  | { kind: "deleteExited" };

export function SessionsHome() {
  const desktop = useDesktop();
  const { host, token, needsYou } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const catalog = useBackendCatalog(host, token, null);
  const now = useNow();
  const usage = useUsageSummary(host, token, handleAuthFailure);
  const board = useBoardSummary(host, token, feed.boardRevision, handleAuthFailure);

  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [exitedOpen, setExitedOpen] = useState(false);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [newOpen, setNewOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const searching = query.trim() !== "";

  const asks = useMemo(() => feed.pendingAsks?.items ?? [], [feed.pendingAsks]);
  const askSessionIds = useMemo(() => new Set(asks.map((ask) => ask.session_id)), [asks]);
  const sessions = useMemo(() => visibleSessions(feed.sessions ?? []), [feed.sessions]);
  const sections = useMemo(() => buildSections(sessions, askSessionIds), [sessions, askSessionIds]);
  const found = useMemo(() => searchSessions(sessions, query), [sessions, query]);
  const activities = useSessionActivity(host, token, sections.live, handleAuthFailure);

  // Exited history is paged from the server, per query; live and pinned sessions
  // arrive over the socket.
  const historyCount = useSessionHistory({
    host,
    token,
    query: "",
    withItems: false,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    onAuthError: handleAuthFailure,
  });
  const history = useSessionHistory({
    host,
    token,
    query,
    withItems: true,
    enabled: searching || exitedOpen,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    pageSize: PAGE_SIZE,
    onAuthError: handleAuthFailure,
  });
  const exitedCount = sections.exited.length + (historyCount.total ?? 0);
  const listed = useMergedSessionPage(
    searching ? found.rest : sections.exited,
    history,
    true,
    1,
    limit,
    searching ? found.lead : undefined,
  );

  const agentFor = useCallback((session: SessionRecord) => agentModelLabel(session, catalog), [catalog]);
  const agentLabel = useCallback((ask: PendingAsk) => humaniseBackend(ask.session_backend, catalog), [catalog]);
  const lampFor = (session: SessionRecord) => sessionLamp(session, askSessionIds.has(session.id));
  const { actions } = feed;

  function onAction(action: SessionAction, session: SessionRecord) {
    if (action === "pin" || action === "unpin") void actions.setPinned(session.id, action === "pin");
    else setDialog({ kind: action, session });
  }

  const menuFor = (session: SessionRecord) => (
    <SessionMenu
      session={session}
      subtitle={agentFor(session)}
      onAction={onAction}
      size={desktop ? "sm" : "md"}
    />
  );
  const rowProps = (session: SessionRecord) => ({
    session,
    lamp: lampFor(session),
    now,
    agent: agentFor(session),
    menu: menuFor(session),
  });
  const compactRow = (session: SessionRecord, pinned = false) => (
    <CompactRow key={session.id} {...rowProps(session)} desktop={desktop} pinned={pinned} />
  );

  const loading = feed.sessions === null;
  const empty = !loading && sessions.length === 0 && asks.length === 0 && exitedCount === 0;
  const shownCount = listed.items.length;

  const results = searching ? (
    <section>
      <SectionHeading title="Results" count={listed.total} dense={!desktop} />
      {listed.items.length > 0 ? (
        <div className={styles.inset}>
          <List>
            {listed.items.map((session) => (
              <ResultRow key={session.id} {...rowProps(session)} />
            ))}
          </List>
        </div>
      ) : history.pending || listed.filling ? (
        <p className={styles.status}>Searching older sessions…</p>
      ) : history.error ? null : (
        <p className={styles.status}>No sessions match.</p>
      )}
      {history.error ? (
        <p className={styles.status}>
          Older sessions failed to load.{" "}
          <button type="button" className={styles.retry} onClick={history.retry}>
            Retry
          </button>
        </p>
      ) : null}
      {shownCount < listed.total ? (
        <div className={styles.more}>
          <button type="button" className={styles.retry} onClick={() => setLimit((value) => value + PAGE_SIZE)}>
            Show more
          </button>
        </div>
      ) : null}
    </section>
  ) : null;

  const sectionsView = searching ? null : (
    <>
      <NeedsYouSection asks={asks} waiting={needsYou} desktop={desktop} agentLabel={agentLabel} now={now} />
      {sections.pinned.length > 0 ? (
        <section>
          <SectionHeading title="Pinned" count={sections.pinned.length} dense={!desktop} />
          <div className={styles.inset}>
            <List>{sections.pinned.map((session) => compactRow(session, true))}</List>
          </div>
        </section>
      ) : null}
      {sections.live.length > 0 ? (
        <section>
          <SectionHeading title="Live" count={sections.live.length} dense={!desktop} />
          {desktop ? (
            <ul className={styles.tiles}>
              {sections.live.map((session) => (
                <LiveTile key={session.id} {...rowProps(session)} activity={activities.get(session.id)} />
              ))}
            </ul>
          ) : (
            <List>
              {sections.live.map((session) => (
                <LiveRow key={session.id} {...rowProps(session)} activity={activities.get(session.id)} />
              ))}
            </List>
          )}
        </section>
      ) : null}
      {exitedCount > 0 ? (
        <ExitedGroup
          count={exitedCount}
          desktop={desktop}
          open={exitedOpen}
          onToggle={() => setExitedOpen((value) => !value)}
          onDeleteExited={() => setDialog({ kind: "deleteExited" })}
          loading={history.pending || listed.filling}
          failed={Boolean(history.error)}
          onRetry={history.retry}
          hasMore={shownCount < listed.total}
          onMore={() => setLimit((value) => value + PAGE_SIZE)}
        >
          <div className={styles.inset}>
            <List>{listed.items.map((session) => compactRow(session))}</List>
          </div>
        </ExitedGroup>
      ) : null}
      {empty ? (
        <p className={styles.status}>No sessions yet. Start one with New.</p>
      ) : null}
    </>
  );

  const scheduled = useMemo(
    () =>
      summarizeScheduled(
        feed.schedules,
        feed.messageSchedules,
        new Map((feed.sessions ?? []).map((session) => [session.id, session.title])),
        3,
        now,
      ),
    [feed.schedules, feed.messageSchedules, feed.sessions, now],
  );

  const main = (
    <>
      <HomeHeader
        desktop={desktop}
        query={query}
        onQuery={(next) => {
          setQuery(next);
          setLimit(PAGE_SIZE);
        }}
        searchOpen={searchOpen}
        onSearchOpen={setSearchOpen}
        onNew={() => setNewOpen(true)}
        onTitle={switcher.open}
      />
      {desktop ? null : <LimitsStrip usage={usage} now={now} />}
      {results}
      {sectionsView}
      {desktop || searching ? null : (
        <div className={styles.summaries}>
          <ScheduledSummaryList summary={scheduled} />
          <BoardSummaryCard summary={board} hideEmpty />
        </div>
      )}
    </>
  );

  return (
    <div className={cx(styles.home, desktop && styles.desktop)}>
      {desktop ? <div className={styles.main}>{main}</div> : main}
      {desktop ? (
        <aside className={styles.side} aria-label="Limits and summaries">
          <div className={styles.sideInner}>
            <LimitsReadout usage={usage} now={now} />
            <ScheduledSummaryList summary={scheduled} />
            <BoardSummaryCard summary={board} />
          </div>
        </aside>
      ) : null}

      <NewSessionDialog open={newOpen} onClose={() => setNewOpen(false)} />
      {dialog?.kind === "rename" ? (
        <RenameDialog
          session={dialog.session}
          onSave={(title) => void actions.rename(dialog.session, title)}
          onClose={() => setDialog(null)}
        />
      ) : null}
      <ConfirmDialog
        open={dialog?.kind === "terminate"}
        title="Terminate session"
        detail="Any running command will be stopped."
        confirmLabel="Terminate"
        onConfirm={() => (dialog?.kind === "terminate" ? actions.terminate(dialog.session.id) : undefined)}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "delete"}
        title="Delete session"
        detail="The session and its transcript are deleted. This cannot be undone."
        confirmLabel="Delete"
        onConfirm={() => (dialog?.kind === "delete" ? actions.remove(dialog.session.id) : undefined)}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "deleteExited"}
        title="Delete exited sessions"
        detail="Every exited session and its transcript is deleted. This cannot be undone."
        confirmLabel="Delete exited"
        onConfirm={() => actions.removeExited()}
        onClose={() => setDialog(null)}
      />
    </div>
  );
}
