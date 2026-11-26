"use client";

import { useRouter } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { postAction } from "@waypoint/web-core/api";
import { liveTerminal, hasTerminalPane, fidelityFor, supportsAttachments, useBackendCatalog } from "@waypoint/web-core/backends";
import { useOptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { useSessionStream } from "@waypoint/web-core/hooks/useSessionStream";
import { COMPACT_HEADER_WIDTH } from "@waypoint/web-core/layoutRules";
import { indexAskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import { composerAvailability, isExitedSession } from "@waypoint/web-core/session/send";
import { deriveTranscript, TRANSCRIPT_DENSITIES, type TranscriptDensity } from "@waypoint/web-core/session/transcriptView";
import { agentLabel, agentModelLabel, sessionStanding } from "@waypoint/web-core/sessionPresentation";

import { Composer } from "@/components/composer/Composer";
import { FocusPill } from "@/components/composer/FocusPill";
import { TerminalCompose } from "@/components/composer/TerminalCompose";
import { useSessionFocus } from "@/components/composer/useSessionFocus";
import { SessionMenu } from "@/components/sessions/SessionMenu";
import { useSessionMenuActions } from "@/components/sessions/useSessionMenuActions";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useNarrowerThan, usePointerUI } from "@/lib/layout";
import { useLocalSetting } from "@/lib/local-setting";
import { useSessionsData, useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import { DensityToggle } from "./DensityToggle";
import type { PanelControl } from "./PanelButton";
import { screenMenus } from "./screenMenus";
import { AskStateProvider, SessionContextProvider } from "./SessionContext";
import { SessionBar } from "./SessionBar";
import { SessionFooter } from "./SessionFooter";
import { SessionHeader } from "./SessionHeader";
import { SessionRail } from "./SessionRail";
import styles from "./SessionScreen.module.css";
import { SideDrawer } from "./SideDrawer";
import { SidePane } from "./SidePane";
import { TerminalView } from "./terminal/TerminalView";
import { useTerminalPane } from "./terminal/useTerminalPane";
import { Transcript } from "./Transcript";
import { useSessionPanels } from "./useSessionPanels";
import { ViewSwitch, type SessionView } from "./ViewSwitch";

export function SessionScreen({ id }: { id: string }) {
  const pointer = usePointerUI();
  const panels = useSessionPanels();
  const [measureConversation, narrowHeader] = useNarrowerThan(COMPACT_HEADER_WIDTH);
  const router = useRouter();
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const { toast } = useToast();
  const catalog = useBackendCatalog(host, token, null);
  const stream = useSessionStream(host, token, id, handleAuthFailure);
  const { session, events, connection, pendingQuestions, hasOlder, loadingOlder } = stream;
  const [filesOpen, setFilesOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const reportFailure = useFailureToast();
  // The composer portals its drop overlay here.
  const [conversationNode, setConversationNode] = useState<HTMLElement | null>(null);

  const conversationRef = useCallback(
    (node: HTMLElement | null) => {
      setConversationNode(node);
      return measureConversation(node);
    },
    [measureConversation],
  );

  const [density, setDensity] = useLocalSetting<TranscriptDensity>("waypoint-session-density", TRANSCRIPT_DENSITIES, "summary");
  const [view, setView] = useState<SessionView>("chat");
  // The side pane folds away while the Terminal view shows; its toggle opens it
  // for this visit only.
  const [paneBesideTerminal, setPaneBesideTerminal] = useState(false);
  const switchView = (next: SessionView) => {
    setView(next);
    setPaneBesideTerminal(false);
  };
  const goHome = useCallback(() => router.replace("/"), [router]);
  const { onAction, reattach, dialogs } = useSessionMenuActions({ onRemoved: goHome, onUpdated: stream.setSession });
  // A second reattach would restart the session the first one restored.
  const [reconnecting, setReconnecting] = useState(false);
  const reconnect = async () => {
    setReconnecting(true);
    try {
      await reattach(id);
    } finally {
      setReconnecting(false);
    }
  };

  // A failed first load fills the page; any later failure is a toast.
  const { error, errorCount } = stream;
  const loadFailed = !stream.loaded && error !== null;
  useEffect(() => {
    if (error && !loadFailed) toast({ message: error, tone: "error" });
    // errorCount makes a repeated message a new event.
  }, [error, errorCount, loadFailed, toast]);

  const agent = session ? agentModelLabel(session, catalog) : "";
  const agentName = (session && agentLabel(session, catalog)) || "Agent";
  useEffect(() => {
    if (!session) return;
    const previous = document.title;
    document.title = `${agentName} · ${session.title}`;
    return () => {
      document.title = previous;
    };
  }, [session, agentName]);

  // The chip's state is deferred with the turns: the transcript keeps its place only across a
  // change of turns, so a chip leaving on its own would shift the view.
  const live = useMemo(() => ({ events, hasOlder, loadingOlder }), [events, hasOlder, loadingOlder]);
  const shown = useDeferredValue(live);
  const rendered = shown.events;
  const optimistic = useOptimisticSends(rendered);
  const transcript = useMemo(() => deriveTranscript(rendered, density), [rendered, density]);
  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);
  const askState = useMemo(() => ({ evidence, snapshot: pendingQuestions }), [evidence, pendingQuestions]);
  const cwd = session?.cwd ?? "";

  const terminalOnly = session ? liveTerminal(session.transport, catalog) : false;
  const hasPane = session ? hasTerminalPane(session.transport, catalog) : false;
  const activeView: SessionView = terminalOnly ? "terminal" : hasPane ? view : "chat";
  const structured = session ? fidelityFor(session.transport, catalog) === "structured" : true;
  const full = density === "full";
  const pane = useTerminalPane({ sessionId: id, session, catalog, active: !loadFailed && activeView === "terminal" });
  const terminalView = activeView === "terminal";
  const exited = isExitedSession(session);
  // A terminal-only transport takes its input in the pane, with a compose
  // drawer where the pane is interactive.
  const chatComposer = !loadFailed && !terminalOnly;
  const paneComposer = !loadFailed && terminalOnly && pane.interactive;
  const composing = chatComposer || paneComposer;
  const canOpenFiles = composing && session !== null && supportsAttachments(session.backend, catalog);
  const openFiles = useMemo(() => (canOpenFiles ? () => setFilesOpen(true) : null), [canOpenFiles]);
  const context = useMemo(
    () => ({ host, token, sessionId: id, cwd, agentName, full, structured, openFiles }),
    [host, token, id, cwd, agentName, full, structured, openFiles],
  );

  const asking = feed.askSessionIds.has(id);
  const standing = session ? sessionStanding(session, asking || (pendingQuestions?.questions.length ?? 0) > 0, connection) : null;
  const othersNeedYou = feed.askSessionIds.size - (asking ? 1 : 0);

  const fullHeader = pointer && !narrowHeader;
  const viewSwitch = hasPane || terminalOnly ? <ViewSwitch value={activeView} onChange={switchView} variant={fullHeader ? "text" : "icon"} terminalOnly={terminalOnly} /> : null;
  const inlineDensity = fullHeader && activeView === "chat" ? <DensityToggle variant="inline" value={density} onChange={setDensity} /> : null;
  const menuDensity = !pointer || (narrowHeader && activeView === "chat") ? <DensityToggle variant="menu" value={density} onChange={setDensity} /> : null;
  const focus = useSessionFocus({ host, token, sessionId: id, session, onSession: stream.setSession });
  const dormant = composerAvailability(session, catalog).dormant;
  const reconnectAction = dormant ? { busy: reconnecting, run: () => void reconnect() } : null;
  const resumePane = async () => {
    try {
      await postAction(host, token, id, "resume");
    } catch (failure) {
      reportFailure(failure, "failed to resume the pane");
    }
  };
  const menus = screenMenus({
    reconnect: reconnectAction,
    terminal: terminalView
      ? {
          refresh: pane.connection.refresh,
          resume: !exited && session?.status !== "running" ? () => void resumePane() : null,
          tools: chatComposer
            ? null
            : {
                focus: { on: focus.on, busy: focus.busy, toggle: () => void focus.set(!focus.on) },
                schedule: () => setScheduleOpen(true),
                files: openFiles,
              },
        }
      : null,
  });
  const overflow = session ? (
    <SessionMenu
      session={session}
      subtitle={agent}
      header={menuDensity}
      onAction={onAction}
      extra={menus.session}
    />
  ) : null;
  const terminalMenu = session ? (
    <SessionMenu
      session={session}
      subtitle={agent}
      onAction={onAction}
      extra={menus.terminal}
      scope={{ label: "Terminal actions", title: "Terminal" }}
    />
  ) : null;
  const terminalRef = useRef<HTMLElement>(null);
  const title = session?.title || "Session";
  const columns = panels.mode === "columns";
  const railColumn = columns && panels.rail.open;
  const sidePane: PanelControl =
    columns && terminalView
      ? { open: paneBesideTerminal, toggle: () => setPaneBesideTerminal((open) => !open) }
      : panels.pane;
  const paneColumn = columns && sidePane.open;

  const body = loadFailed ? (
    <EmptyState
      title={stream.errorStatus === 404 ? "Session not found" : "Couldn't load the session"}
      detail={stream.error ?? undefined}
    />
  ) : terminalView ? (
    <TerminalView
      ref={terminalRef}
      pane={pane}
      exited={exited}
      reconnect={reconnectAction}
      lead={
        !chatComposer && focus.on ? (
          <FocusPill busy={focus.busy} onTurnOff={() => focus.set(false)} returnFocusRef={terminalRef} />
        ) : null
      }
      menu={terminalMenu}
    />
  ) : (
    <Transcript
      turns={transcript.turns}
      status={session?.status ?? null}
      loading={stream.loading || rendered !== events}
      hasOlder={shown.hasOlder}
      loadingOlder={shown.loadingOlder}
      onLoadOlder={stream.loadOlder}
      hiddenCount={transcript.hiddenCount}
      onShowAll={() => setDensity("full")}
      pending={optimistic.pending}
    />
  );

  return (
    <SessionContextProvider value={context}>
      <AskStateProvider value={askState}>
        <div className={cx(styles.screen, railColumn && styles.railOpen, paneColumn && styles.paneOpen)}>
          {railColumn ? <SessionRail currentId={id} /> : null}
          <section ref={conversationRef} className={cx(styles.conv, terminalView && styles.terminal)} aria-label="Conversation">
            {pointer ? (
              <SessionHeader
                title={title}
                standing={standing}
                agent={agent}
                onTitle={switcher.open}
                rail={panels.rail}
                pane={sidePane}
                view={viewSwitch}
                density={inlineDensity}
                overflow={overflow}
              />
            ) : (
              <SessionBar
                title={title}
                standing={standing}
                agent={agent}
                othersNeedYou={othersNeedYou}
                onTitle={switcher.open}
                rail={columns ? panels.rail : undefined}
                pane={columns ? sidePane : undefined}
                view={viewSwitch}
                overflow={overflow}
              />
            )}
            {body}
            <SessionFooter
              composer={
                paneComposer ? (
                  <TerminalCompose
                    sessionId={id}
                    session={session}
                    catalog={catalog}
                    connection={pane.connection.state}
                    submit={pane.connection.submit}
                    refocusPane={() => pane.term.current?.focus()}
                    dropRoot={conversationNode}
                    filesOpen={filesOpen}
                    onFilesOpenChange={setFilesOpen}
                    scheduleOpen={scheduleOpen}
                    onScheduleOpenChange={setScheduleOpen}
                  />
                ) : chatComposer ? (
                  <Composer
                    sessionId={id}
                    session={session}
                    catalog={catalog}
                    agentName={agentName}
                    events={events}
                    latestTodo={stream.latestTodo}
                    onSession={stream.setSession}
                    dropRoot={conversationNode}
                    filesOpen={filesOpen}
                    onFilesOpenChange={setFilesOpen}
                    scheduleOpen={scheduleOpen}
                    onScheduleOpenChange={setScheduleOpen}
                    onPending={optimistic.add}
                  />
                ) : null
              }
            />
          </section>
          {paneColumn ? <SidePane /> : null}
          {panels.drawer === "rail" ? (
            <SideDrawer side="left" label="Sessions" onClose={panels.closeDrawer}>
              <SessionRail currentId={id} onClose={panels.closeDrawer} onPick={panels.pickFromDrawer} />
            </SideDrawer>
          ) : null}
          {panels.drawer === "pane" ? (
            <SideDrawer side="right" label="Session details" onClose={panels.closeDrawer}>
              <SidePane onClose={panels.closeDrawer} />
            </SideDrawer>
          ) : null}
        </div>
        {dialogs}
      </AskStateProvider>
    </SessionContextProvider>
  );
}
