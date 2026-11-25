"use client";

import { useRouter } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";

import { liveTerminal, hasTerminalPane, fidelityFor, supportsAttachments, useBackendCatalog } from "@waypoint/web-core/backends";
import { useOptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { useSessionStream } from "@waypoint/web-core/hooks/useSessionStream";
import { COMPACT_HEADER_WIDTH } from "@waypoint/web-core/layoutRules";
import { indexAskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import { composerAvailability } from "@waypoint/web-core/session/send";
import { deriveTranscript, TRANSCRIPT_DENSITIES, type TranscriptDensity } from "@waypoint/web-core/session/transcriptView";
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
  const { session, events, connection, pendingQuestions } = stream;
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

  const rendered = useDeferredValue(events);
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
  // A terminal-only transport takes its input in the pane.
  const composing = !loadFailed && !terminalOnly;
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
  const viewSwitch = hasPane || terminalOnly ? <ViewSwitch value={activeView} onChange={setView} variant={fullHeader ? "text" : "icon"} terminalOnly={terminalOnly} /> : null;
  const inlineDensity = fullHeader && activeView === "chat" ? <DensityToggle variant="inline" value={density} onChange={setDensity} /> : null;
  const menuDensity = !pointer || (narrowHeader && activeView === "chat") ? <DensityToggle variant="menu" value={density} onChange={setDensity} /> : null;
  const sessionActions = [
    { id: "refresh", label: "Refresh", run: () => window.location.reload() },
    ...(composerAvailability(session, catalog).dormant
      ? [{ id: "reconnect", label: "Reconnect session", run: () => void reattach(id) }]
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
      hasOlder={stream.hasOlder}
      loadingOlder={stream.loadingOlder}
      onLoadOlder={stream.loadOlder}
      hiddenCount={transcript.hiddenCount}
      onShowAll={() => setDensity("full")}
      pending={optimistic.pending}
      override={activeView === "terminal" ? <EmptyState title="Not built yet" detail="Terminal" /> : undefined}
    />
  );

  return (
    <SessionContextProvider value={context}>
      <AskStateProvider value={askState}>
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
