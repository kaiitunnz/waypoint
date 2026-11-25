"use client";

import { useRouter } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";

import { liveTerminal, hasTerminalPane, fidelityFor, useBackendCatalog } from "@waypoint/web-core/backends";
import { useSessionStream } from "@waypoint/web-core/hooks/useSessionStream";
import { indexAskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import { deriveTranscript, TRANSCRIPT_DENSITIES, type TranscriptDensity } from "@waypoint/web-core/session/transcriptView";
import { agentLabel, agentModelLabel, sessionStanding } from "@waypoint/web-core/sessionPresentation";

import { SessionMenu } from "@/components/sessions/SessionMenu";
import { useSessionMenuActions } from "@/components/sessions/useSessionMenuActions";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";
import { useLocalSetting, usePanelOpen } from "@/lib/local-setting";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { DensityToggle } from "./DensityToggle";
import { AskStateProvider, SessionContextProvider } from "./SessionContext";
import { SessionBar } from "./SessionBar";
import { SessionFooter } from "./SessionFooter";
import { SessionHeader } from "./SessionHeader";
import { SessionRail } from "./SessionRail";
import styles from "./SessionScreen.module.css";
import { SidePane } from "./SidePane";
import { Transcript } from "./Transcript";
import { ViewSwitch, type SessionView } from "./ViewSwitch";

// The side pane and rail follow the width until the person decides.
const PANE_QUERY = "(min-width: 1280px)";
const RAIL_QUERY = "(min-width: 1100px)";

export function SessionScreen({ id }: { id: string }) {
  const desktop = useDesktop();
  const router = useRouter();
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const { toast } = useToast();
  const catalog = useBackendCatalog(host, token, null);
  const stream = useSessionStream(host, token, id, handleAuthFailure);
  const { session, events, connection, pendingQuestions } = stream;

  const [density, setDensity] = useLocalSetting<TranscriptDensity>("waypoint-session-density", TRANSCRIPT_DENSITIES, "summary");
  const [view, setView] = useState<SessionView>("chat");
  const [railOpen, setRailOpen] = usePanelOpen("waypoint-session-rail", RAIL_QUERY);
  const [paneOpen, setPaneOpen] = usePanelOpen("waypoint-session-pane", PANE_QUERY);
  const goHome = useCallback(() => router.replace("/"), [router]);
  const { onAction, dialogs } = useSessionMenuActions({ onRemoved: goHome, onUpdated: stream.setSession });

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
  const transcript = useMemo(() => deriveTranscript(rendered, density), [rendered, density]);
  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);
  const askState = useMemo(() => ({ evidence, snapshot: pendingQuestions }), [evidence, pendingQuestions]);
  const cwd = session?.cwd ?? "";

  const terminalOnly = session ? liveTerminal(session.transport, catalog) : false;
  const hasPane = session ? hasTerminalPane(session.transport, catalog) : false;
  const activeView: SessionView = terminalOnly ? "terminal" : hasPane ? view : "chat";
  const structured = session ? fidelityFor(session.transport, catalog) === "structured" : true;
  const full = density === "full";
  const context = useMemo(
    () => ({ host, token, sessionId: id, cwd, agentName, full, structured }),
    [host, token, id, cwd, agentName, full, structured],
  );

  const asking = feed.askSessionIds.has(id);
  const standing = session ? sessionStanding(session, asking || (pendingQuestions?.questions.length ?? 0) > 0, connection) : null;
  const othersNeedYou = feed.askSessionIds.size - (asking ? 1 : 0);

  const viewSwitch = hasPane || terminalOnly ? <ViewSwitch value={activeView} onChange={setView} variant={desktop ? "text" : "icon"} terminalOnly={terminalOnly} /> : null;
  const overflow = session ? (
    <SessionMenu
      session={session}
      subtitle={agent}
      size="sm"
      header={desktop ? null : <DensityToggle variant="segmented" value={density} onChange={setDensity} />}
      onAction={onAction}
    />
  ) : null;
  const title = session?.title || "Session";

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
      override={activeView === "terminal" ? <EmptyState title="Not built yet" detail="Terminal" /> : undefined}
    />
  );

  return (
    <SessionContextProvider value={context}>
      <AskStateProvider value={askState}>
        <div className={cx(styles.screen, railOpen && styles.railOpen, paneOpen && styles.paneOpen)}>
          {desktop && railOpen ? <SessionRail currentId={id} /> : null}
          <section className={styles.conv} aria-label="Conversation">
            {desktop ? (
              <SessionHeader
                title={title}
                standing={standing}
                agent={agent}
                onTitle={switcher.open}
                railOpen={railOpen}
                onToggleRail={() => setRailOpen(!railOpen)}
                paneOpen={paneOpen}
                onTogglePane={() => setPaneOpen(!paneOpen)}
                view={viewSwitch}
                density={activeView === "chat" ? <DensityToggle variant="text" value={density} onChange={setDensity} /> : null}
                overflow={overflow}
              />
            ) : (
              <SessionBar title={title} standing={standing} agent={agent} othersNeedYou={othersNeedYou} onTitle={switcher.open} view={viewSwitch} overflow={overflow} />
            )}
            {body}
            <SessionFooter />
          </section>
          {desktop && paneOpen ? <SidePane /> : null}
        </div>
        {dialogs}
      </AskStateProvider>
    </SessionContextProvider>
  );
}
