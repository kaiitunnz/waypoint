"use client";

import { useCallback, useMemo, useState } from "react";

import { useBackendCatalog, humaniseBackend } from "@waypoint/web-core/backends";
import { useBoardSummary } from "@waypoint/web-core/hooks/useBoardSummary";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useSessionActions } from "@waypoint/web-core/hooks/useSessionActions";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import { useUsageSummary } from "@waypoint/web-core/hooks/useUsageSummary";
import { agentModelLabel, lampWord, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import { HOME_HIDDEN_SOURCES, buildSections, searchSessions } from "@waypoint/web-core/sessionSections";
import { useMergedSessionPage, useSessionHistory } from "@waypoint/web-core/sessionHistory";
import { summarizeScheduled } from "@waypoint/web-core/usageSummary";
import type { PendingAsk, SessionRecord } from "@waypoint/web-core/types";

import { EmptyState } from "@/components/ui/EmptyState";
import { List } from "@/components/ui/List";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ExitedGroup } from "./ExitedGroup";
import { HomeHeader } from "./HomeHeader";
import { NeedsYouSection } from "./NeedsYouSection";
import { NewSessionDialog } from "./NewSessionDialog";
import { SearchResults } from "./SearchResults";
import { runSessionAction, SessionDialogs, type HomeDialog } from "./SessionDialogs";
import { SessionMenu, type SessionAction } from "./SessionMenu";
import { CompactRow, LiveRow, LiveTile, ResultRow } from "./SessionRows";
import { useSwitcher } from "./SwitcherProvider";
import { BoardSummaryCard, LimitsReadout, LimitsStrip, ScheduledSummaryList } from "./Summaries";
import styles from "./SessionsHome.module.css";

const PAGE_SIZE = 10;

export function SessionsHome() {
  const desktop = useDesktop();
  const { host, token, needsYou, inboxCount } = useShellData();
  const feed = useSessionsData();
  const { visible: sessions, askSessionIds } = feed;
  const { toast } = useToast();
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const actions = useSessionActions(host, token, feed, reportError, handleAuthFailure);
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
  const [dialog, setDialog] = useState<HomeDialog | null>(null);
  const searching = query.trim() !== "";

  const asks = useMemo(() => feed.pendingAsks?.items ?? [], [feed.pendingAsks]);
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
  const agentLabel = useCallback(
    (ask: PendingAsk) => humaniseBackend(ask.session_backend, catalog),
    [catalog],
  );

  const onAction = (action: SessionAction, session: SessionRecord) =>
    runSessionAction(action, session, actions, setDialog);

  const menuFor = (session: SessionRecord) => (
    <SessionMenu
      session={session}
      subtitle={agentFor(session)}
      onAction={onAction}
      size={desktop ? "sm" : "md"}
    />
  );
  const rowProps = (session: SessionRecord) => {
    const lamp = sessionLamp(session, askSessionIds.has(session.id));
    return {
      session,
      lamp,
      word: lampWord(lamp, session.status),
      now,
      agent: agentFor(session),
      menu: menuFor(session),
    };
  };
  const compactRow = (session: SessionRecord, pinned = false) => (
    <CompactRow key={session.id} {...rowProps(session)} desktop={desktop} pinned={pinned} />
  );

  const loading = feed.sessions === null;
  const empty = !loading && sessions.length === 0 && needsYou === 0 && exitedCount === 0;
  const shownCount = listed.items.length;

  const results = searching ? (
    <SearchResults
      total={listed.total}
      rows={listed.items.map((session) => (
        <ResultRow key={session.id} {...rowProps(session)} />
      ))}
      searching={history.pending || listed.filling}
      failed={Boolean(history.error)}
      onRetry={history.retry}
      hasMore={shownCount < listed.total}
      onMore={() => setLimit((value) => value + PAGE_SIZE)}
      desktop={desktop}
    />
  ) : null;

  const sectionsView = searching ? null : (
    <>
      <NeedsYouSection asks={asks} inbox={inboxCount} desktop={desktop} agentLabel={agentLabel} now={now} />
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
      {empty ? <EmptyState title="No sessions yet" detail="Start one with New." /> : null}
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
      <SessionDialogs dialog={dialog} actions={actions} onClose={() => setDialog(null)} />
    </div>
  );
}
