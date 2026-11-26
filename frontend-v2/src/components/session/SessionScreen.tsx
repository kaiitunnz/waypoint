"use client";

import { useRouter } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import {
  fidelityFor,
  hasTerminalPane,
  liveTerminal,
  supportsAttachments,
  supportsPlanApproval,
  useBackendCatalog,
} from "@waypoint/web-core/backends";
import { planForEvent } from "@waypoint/web-core/events";
import { useAskQuestionState } from "@waypoint/web-core/hooks/useAskQuestionState";
import { useOptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { useSessionScheduledMessages } from "@waypoint/web-core/hooks/useSessionScheduledMessages";
import { useSessionStream } from "@waypoint/web-core/hooks/useSessionStream";
import { COMPACT_HEADER_WIDTH } from "@waypoint/web-core/layoutRules";
import { indexAskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import { composerAvailability } from "@waypoint/web-core/session/send";
import {
  deriveTranscript,
  dockedPlanEvent,
  TRANSCRIPT_DENSITIES,
  type TranscriptDensity,
} from "@waypoint/web-core/session/transcriptView";
import { agentLabel, agentModelLabel, sessionStanding } from "@waypoint/web-core/sessionPresentation";

import { Composer } from "@/components/composer/Composer";
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

import { AskControllerProvider } from "./attention/AskController";
import { AttentionStack } from "./attention/AttentionStack";
import { useSessionNotices } from "./attention/useSessionNotices";
import { DensityToggle } from "./DensityToggle";
import { AskStateProvider, SessionContextProvider } from "./SessionContext";
import { SessionBar } from "./SessionBar";
import { SessionFooter } from "./SessionFooter";
import { SessionHeader } from "./SessionHeader";
import { SessionRail } from "./SessionRail";
import styles from "./SessionScreen.module.css";
import { SideDrawer } from "./SideDrawer";
import { SidePane } from "./SidePane";
import { Transcript } from "./Transcript";
import { useSessionPanels } from "./useSessionPanels";
import { ViewSwitch, type SessionView } from "./ViewSwitch";

// How many older pages Show in transcript loads looking for a question.
const REVEAL_PAGE_LIMIT = 50;

export function SessionScreen({ id }: { id: string }) {
  const pointer = usePointerUI();
  const panels = useSessionPanels();
  const [measureConversation, narrowHeader] = useNarrowerThan(COMPACT_HEADER_WIDTH);
  const router = useRouter();
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const { toast, dismiss } = useToast();
  const catalog = useBackendCatalog(host, token, null);
  const stream = useSessionStream(host, token, id, handleAuthFailure);
  const { session, events, connection, pendingQuestions, hasOlder, loadingOlder, loadOlder } = stream;
  const scheduled = useSessionScheduledMessages(host, token, id);
  const [filesOpen, setFilesOpen] = useState(false);
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
  const goHome = useCallback(() => router.replace("/"), [router]);
  const { onAction, reattach, dialogs } = useSessionMenuActions({ onRemoved: goHome, onUpdated: stream.setSession });
  // A second reattach would restart the session the first one restored.
  // The menu and the question form both reattach; the ref stops a second tap before a render.
  const [reconnecting, setReconnecting] = useState(false);
  const reconnectingRef = useRef(false);
  const reconnect = useCallback(async () => {
    if (reconnectingRef.current) return;
    reconnectingRef.current = true;
    setReconnecting(true);
    try {
      await reattach(id);
    } finally {
      reconnectingRef.current = false;
      setReconnecting(false);
    }
  }, [reattach, id]);

  // A failed first load fills the page; any later failure is a toast.
  const { error, errorCount } = stream;
  const loadFailed = !stream.loaded && error !== null;
  useEffect(() => {
    if (error && !loadFailed) toast({ message: error, tone: "error", key: "session-error" });
    // errorCount makes a repeated message a new event.
  }, [error, errorCount, loadFailed, toast]);
  useEffect(() => () => dismiss("session-error"), [dismiss]);

  useSessionNotices({ host, token, session, modelNotice: stream.modelNotice, clipboard: stream.clipboard });

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
  // The plan waiting for approval sits above the composer instead of in the transcript.
  const planMode = session?.permission_mode === "plan" && supportsPlanApproval(session.backend, catalog);
  const planEvent = useMemo(() => (planMode ? dockedPlanEvent(events) : null), [planMode, events]);
  const plan = useMemo(() => (planEvent ? planForEvent(planEvent) : null), [planEvent]);
  const dockedPlanSequence = plan && planEvent ? planEvent.sequence : null;
  const transcript = useMemo(
    () => deriveTranscript(rendered, density, dockedPlanSequence),
    [rendered, density, dockedPlanSequence],
  );
  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);
  const askState = useMemo(() => ({ evidence, snapshot: pendingQuestions }), [evidence, pendingQuestions]);
  const cwd = session?.cwd ?? "";

  const terminalOnly = session ? liveTerminal(session.transport, catalog) : false;
  const hasPane = session ? hasTerminalPane(session.transport, catalog) : false;
  const activeView: SessionView = terminalOnly ? "terminal" : hasPane ? view : "chat";
  const structured = session ? fidelityFor(session.transport, catalog) === "structured" : true;
  const full = density === "full";
  // A terminal-only transport takes its input in the pane.
  const composing = !loadFailed && !terminalOnly;
  const canOpenFiles = composing && session !== null && supportsAttachments(session.backend, catalog);
  const openFiles = useMemo(() => (canOpenFiles ? () => setFilesOpen(true) : null), [canOpenFiles]);
  const context = useMemo(
    () => ({ host, token, sessionId: id, cwd, agentName, full, structured, openFiles }),
    [host, token, id, cwd, agentName, full, structured, openFiles],
  );

  const reportAskError = useCallback(
    (requestError: unknown, fallback: string) => {
      if (isAuthError(requestError)) handleAuthFailure();
      else toast({ message: requestError instanceof Error ? requestError.message : fallback, tone: "error" });
    },
    [toast],
  );
  const dormant = composerAvailability(session, catalog).dormant;
  const reattachForAsk = useCallback(() => void reconnect(), [reconnect]);
  const askController = useAskQuestionState({
    host,
    token,
    sessionId: id,
    snapshot: pendingQuestions,
    events,
    canReattach: dormant,
    reattach: reattachForAsk,
    onRequestError: reportAskError,
    retry: true,
  });

  // Show in transcript: page older events until the question's card is loaded, then
  // let the transcript scroll to it.
  const [revealing, setRevealing] = useState<{ toolUseId: string; seq: number } | null>(null);
  const revealPages = useRef(0);
  const revealQuestion = useCallback((toolUseId: string) => {
    setView("chat");
    revealPages.current = REVEAL_PAGE_LIMIT;
    setRevealing((current) => ({ toolUseId, seq: (current?.seq ?? 0) + 1 }));
  }, []);
  const revealLoaded =
    revealing !== null &&
    events.some((event) => event.kind === "tool_call" && event.metadata?.tool_use_id === revealing.toolUseId);
  useEffect(() => {
    if (!revealing || revealLoaded || loadingOlder || !hasOlder || revealPages.current <= 0) return;
    revealPages.current -= 1;
    loadOlder();
  }, [revealing, revealLoaded, loadingOlder, hasOlder, loadOlder]);
  const onRevealed = useCallback(
    (found: boolean) => {
      // Summary may leave the card out; Full shows every event.
      if (!found && density === "summary") {
        setDensity("full");
        setRevealing((current) => (current ? { ...current, seq: current.seq + 1 } : null));
        return;
      }
      setRevealing(null);
    },
    [density, setDensity],
  );
  // The deferred turns must have caught up before the card can be on the page.
  const revealReady = revealing !== null && revealLoaded && rendered === events;
  // One object per request, so the transcript's search is not restarted by an unrelated render.
  const reveal = useMemo(() => (revealReady ? revealing : null), [revealReady, revealing]);

  const asking = feed.askSessionIds.has(id);
  const standing = session ? sessionStanding(session, asking || (pendingQuestions?.questions.length ?? 0) > 0, connection) : null;
  const othersNeedYou = feed.askSessionIds.size - (asking ? 1 : 0);

  const fullHeader = pointer && !narrowHeader;
  const viewSwitch = hasPane || terminalOnly ? <ViewSwitch value={activeView} onChange={setView} variant={fullHeader ? "text" : "icon"} terminalOnly={terminalOnly} /> : null;
  const inlineDensity = fullHeader && activeView === "chat" ? <DensityToggle variant="inline" value={density} onChange={setDensity} /> : null;
  const menuDensity = !pointer || (narrowHeader && activeView === "chat") ? <DensityToggle variant="menu" value={density} onChange={setDensity} /> : null;
  const sessionActions = [
    { id: "refresh", label: "Refresh", run: () => window.location.reload() },
    ...(dormant
      ? [
          {
            id: "reconnect",
            label: reconnecting ? "Reconnecting…" : "Reconnect session",
            disabled: reconnecting,
            run: () => void reconnect(),
          },
        ]
      : []),
  ];
  const overflow = session ? (
    <SessionMenu
      session={session}
      subtitle={agent}
      header={menuDensity}
      onAction={onAction}
      extra={sessionActions}
    />
  ) : null;
  const title = session?.title || "Session";
  const columns = panels.mode === "columns";
  const railColumn = columns && panels.rail.open;
  const paneColumn = columns && panels.pane.open;

  const body = loadFailed ? (
    <EmptyState
      title={stream.errorStatus === 404 ? "Session not found" : "Couldn't load the session"}
      detail={stream.error ?? undefined}
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
      override={activeView === "terminal" ? <EmptyState title="Not built yet" detail="Terminal" /> : undefined}
      reveal={reveal}
      onRevealed={onRevealed}
    />
  );

  return (
    <SessionContextProvider value={context}>
      <AskStateProvider value={askState}>
        <AskControllerProvider value={askController}>
        <div className={cx(styles.screen, railColumn && styles.railOpen, paneColumn && styles.paneOpen)}>
          {railColumn ? <SessionRail currentId={id} /> : null}
          <section ref={conversationRef} className={styles.conv} aria-label="Conversation">
            {pointer ? (
              <SessionHeader
                title={title}
                standing={standing}
                agent={agent}
                onTitle={switcher.open}
                rail={panels.rail}
                pane={panels.pane}
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
                pane={columns ? panels.pane : undefined}
                view={viewSwitch}
                overflow={overflow}
              />
            )}
            {body}
            <SessionFooter
              dock={
                composing && session ? (
                  <AttentionStack
                    session={session}
                    catalog={catalog}
                    events={events}
                    plan={plan}
                    pendingQuestions={pendingQuestions}
                    heldMessages={stream.heldMessages}
                    scheduled={scheduled}
                    sideQuestions={stream.sideQuestions}
                    sideQuestionArrivals={stream.sideQuestionArrivals}
                    onSession={stream.setSession}
                    onRevealQuestion={revealQuestion}
                  />
                ) : null
              }
              composer={
                composing ? (
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
                    onPending={optimistic.add}
                    onScheduled={scheduled.refresh}
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
        </AskControllerProvider>
      </AskStateProvider>
    </SessionContextProvider>
  );
}
