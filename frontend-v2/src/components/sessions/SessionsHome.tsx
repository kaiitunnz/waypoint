"use client";

import { useCallback, useMemo, useState } from "react";

import { useBackendCatalog, humaniseBackend } from "@waypoint/web-core/backends";
import { useBoardSummary } from "@waypoint/web-core/hooks/useBoardSummary";
import { useLaunchContext } from "@waypoint/web-core/hooks/useLaunchContext";
import { seedRecentCwdsFromHistory } from "@waypoint/web-core/launch/folders";
import { useConversationPreviews } from "@waypoint/web-core/hooks/useConversationPreviews";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useScheduleTargetSessions } from "@waypoint/web-core/hooks/useScheduleTargetSessions";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import { useUsageSummary } from "@waypoint/web-core/hooks/useUsageSummary";
import { agentModelLabel, lampWord, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import { buildSections, searchSessions } from "@waypoint/web-core/sessionSections";
import { summarizeScheduled } from "@waypoint/web-core/usageSummary";
import type { PendingAsk, SessionRecord } from "@waypoint/web-core/types";

import { LaunchDialog, type LaunchRequest } from "@/components/launch/LaunchDialog";
import { StartFromPreset } from "@/components/launch/StartFromPreset";
import { ScheduledSheet } from "@/components/scheduled/ScheduledSheet";
import { EmptyState } from "@/components/ui/EmptyState";
import { List } from "@/components/ui/List";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { usePhoneLayout } from "@/lib/layout";
import { useShowExited } from "@/lib/show-exited";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ExitedGroup } from "./ExitedGroup";
import { HomeHeader } from "./HomeHeader";
import { NeedsYouSection } from "./NeedsYouSection";
import { SearchResults } from "./SearchResults";
import { SessionMenu } from "./SessionMenu";
import { CompactRow, LiveRow, LiveTile, ResultRow } from "./SessionRows";
import { useExitedSessions } from "./useExitedSessions";
import { useSessionMenuActions } from "./useSessionMenuActions";
import { useSwitcher } from "./SwitcherProvider";
import { BoardSummaryCard, LimitsReadout, LimitsStrip, ScheduledSummaryList } from "./Summaries";
import styles from "./SessionsHome.module.css";

const NO_SESSIONS: SessionRecord[] = [];

export function SessionsHome() {
  const phone = usePhoneLayout();
  const { host, token, needsYou, waiting, launchTargets } = useShellData();
  const feed = useSessionsData();
  const { visible: sessions, askSessionIds } = feed;
  const { onAction, openDialog, dialogs } = useSessionMenuActions();
  const switcher = useSwitcher();
  const catalog = useBackendCatalog(host, token, null);
  const now = useNow(30_000);
  const usage = useUsageSummary(host, token, handleAuthFailure);
  const board = useBoardSummary(host, token, feed.boardRevision, handleAuthFailure);

  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [showExited, setShowExited] = useShowExited();
  const launch = useLaunchContext(host, token, launchTargets.targets, launchTargets.activeTargetId, handleAuthFailure);
  const [launchRequest, setLaunchRequest] = useState<LaunchRequest | null>(null);
  const [scheduledOpen, setScheduledOpen] = useState(false);
  const { refresh: refreshLaunch } = launch;
  const { sessions: knownSessions, schedules: knownSchedules } = feed;
  const openLaunch = useCallback(
    (request: LaunchRequest) => {
      // Folders the server knows join the recent folders.
      if (knownSessions) seedRecentCwdsFromHistory(host, knownSessions, knownSchedules);
      setLaunchRequest(request);
      // Presets may have changed in another tab or client.
      void refreshLaunch();
    },
    [host, knownSessions, knownSchedules, refreshLaunch],
  );
  const searching = query.trim() !== "";

  const asks = useMemo(() => feed.pendingAsks?.items ?? [], [feed.pendingAsks]);
  const sections = useMemo(() => buildSections(sessions, askSessionIds), [sessions, askSessionIds]);
  const found = useMemo(() => searchSessions(sessions, query), [sessions, query]);
  const previewed = useMemo(() => [...sections.pinned, ...sections.live], [sections.pinned, sections.live]);
  const [previews] = useConversationPreviews();
  const activities = useSessionActivity(host, token, previewed, handleAuthFailure, previews);

  // Exited history is paged from the server, per query; live and pinned sessions
  // arrive over the socket.
  const {
    count: exitedCount,
    listed,
    paging,
  } = useExitedSessions({
    query,
    live: searching ? found.rest : sections.exited,
    lead: searching ? found.lead : undefined,
    enabled: searching || showExited,
  });

  const agentFor = useCallback((session: SessionRecord) => agentModelLabel(session, catalog), [catalog]);
  const agentLabel = useCallback(
    (ask: PendingAsk) => humaniseBackend(ask.session_backend, catalog),
    [catalog],
  );

  const menuFor = (session: SessionRecord) => (
    <SessionMenu
      session={session}
      subtitle={agentFor(session)}
      onAction={onAction}
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
  // Pinned and Live read alike: a serif title, the lamp, the time and, unless turned off, the preview line.
  const previewGroup = (title: string, group: SessionRecord[]) =>
    group.length > 0 ? (
      <section>
        <SectionHeading title={title} count={group.length} dense={phone} />
        {phone ? (
          <List>
            {group.map((session) => (
              <LiveRow key={session.id} {...rowProps(session)} previews={previews} activity={activities.get(session.id)} />
            ))}
          </List>
        ) : (
          <ul className={styles.tiles}>
            {group.map((session) => (
              <LiveTile key={session.id} {...rowProps(session)} previews={previews} activity={activities.get(session.id)} />
            ))}
          </ul>
        )}
      </section>
    ) : null;

  const loading = feed.sessions === null;
  const empty = !loading && sessions.length === 0 && needsYou === 0 && exitedCount === 0;

  const results = searching ? (
    <SearchResults
      total={listed.total}
      rows={listed.items.map((session) => (
        <ResultRow key={session.id} {...rowProps(session)} />
      ))}
      paging={paging}
      phone={phone}
    />
  ) : null;

  const sectionsView = searching ? null : (
    <>
      <NeedsYouSection
        asks={asks}
        inbox={waiting.inbox}
        board={waiting.board}
        phone={phone}
        agentLabel={agentLabel}
        now={now}
      />
      {previewGroup("Pinned", sections.pinned)}
      {previewGroup("Live", sections.live)}
      {exitedCount > 0 ? (
        <ExitedGroup
          count={exitedCount}
          short={phone}
          divided={phone}
          open={showExited}
          onToggle={() => setShowExited(!showExited)}
          onDeleteExited={() => openDialog({ kind: "deleteExited" })}
          paging={paging}
        >
          <div className={styles.inset}>
            <List>
              {listed.items.map((session) => (
                <CompactRow key={session.id} {...rowProps(session)} detailed={!phone} />
              ))}
            </List>
          </div>
        </ExitedGroup>
      ) : null}
      {empty ? <EmptyState title="No sessions yet" detail="Start one with New." /> : null}
    </>
  );

  const titleSessions = useScheduleTargetSessions(host, token, feed.sessions ?? NO_SESSIONS, feed.messageSchedules);
  const titleById = useMemo(
    () => new Map(titleSessions.map((session) => [session.id, session.title])),
    [titleSessions],
  );
  const scheduled = useMemo(
    () => summarizeScheduled(feed.schedules, feed.messageSchedules, titleById, 3, now),
    [feed.schedules, feed.messageSchedules, titleById, now],
  );
  const openScheduled = () => setScheduledOpen(true);

  const main = (
    <>
      <HomeHeader
        phone={phone}
        query={query}
        onQuery={setQuery}
        searchOpen={searchOpen}
        onSearchOpen={setSearchOpen}
        onNew={() => openLaunch({ mode: "new", preset: { kind: "default" } })}
        onTitle={switcher.open}
      />
      {phone ? <LimitsStrip usage={usage} now={now} /> : null}
      {results}
      {sectionsView}
      {phone && !searching ? (
        <div className={styles.summaries}>
          <ScheduledSummaryList summary={scheduled} now={now} onOpen={openScheduled} />
          <BoardSummaryCard summary={board} hideEmpty />
        </div>
      ) : null}
    </>
  );

  return (
    <div className={cx(styles.home, !phone && styles.full)}>
      <div className={styles.main}>{main}</div>
      {phone ? null : (
        <aside className={styles.side} aria-label="Limits and summaries">
          <div className={styles.sideInner}>
            {launch.ready ? (
              <StartFromPreset
                presets={launch.presets}
                defaultPresetId={launch.defaultPresetId}
                catalog={launch.catalog}
                onOpen={openLaunch}
              />
            ) : null}
            <LimitsReadout usage={usage} now={now} />
            <ScheduledSummaryList summary={scheduled} now={now} onOpen={openScheduled} />
            <BoardSummaryCard summary={board} />
          </div>
        </aside>
      )}

      <LaunchDialog request={launchRequest} launch={launch} onClose={() => setLaunchRequest(null)} />
      {scheduledOpen ? (
        <ScheduledSheet
          onClose={() => setScheduledOpen(false)}
          schedules={feed.schedules}
          messages={feed.messageSchedules}
          titleById={titleById}
          catalog={catalog}
          now={now}
        />
      ) : null}
      {dialogs}
    </div>
  );
}
