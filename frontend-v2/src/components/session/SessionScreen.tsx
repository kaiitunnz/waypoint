"use client";

import { useRouter } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";

import { liveTerminal, hasTerminalPane, fidelityFor, useBackendCatalog } from "@waypoint/web-core/backends";
import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";
import { useSessionActions } from "@waypoint/web-core/hooks/useSessionActions";
import { useSessionStream } from "@waypoint/web-core/hooks/useSessionStream";
import { indexAskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import { connectionLabel } from "@waypoint/web-core/session/connection";
import { deriveTranscript } from "@waypoint/web-core/session/transcriptView";
import { agentModelLabel, lampWord, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import type { SessionRecord } from "@waypoint/web-core/types";

import { runSessionAction, SessionDialogs, type HomeDialog } from "@/components/sessions/SessionDialogs";
import type { SessionAction } from "@/components/sessions/SessionMenu";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";
import { useLocalSetting, useOverride } from "@/lib/local-setting";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { DENSITIES, DensityToggle, type Density } from "./DensityToggle";
import { AskStateProvider, SessionContextProvider } from "./SessionContext";
import { SessionBar, type SessionStatus } from "./SessionBar";
import { SessionFooter } from "./SessionFooter";
import { SessionHeader } from "./SessionHeader";
import { SessionOverflow } from "./SessionOverflow";
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

  const [density, setDensity] = useLocalSetting<Density>("waypoint-session-density", DENSITIES, "summary");
  const [view, setView] = useState<SessionView>("chat");
  const [dialog, setDialog] = useState<HomeDialog | null>(null);
  const [railOverride, setRailOverride] = useOverride("waypoint-session-rail");
  const [paneOverride, setPaneOverride] = useOverride("waypoint-session-pane");
  const railAuto = useMediaQuery(RAIL_QUERY);
  const paneAuto = useMediaQuery(PANE_QUERY);
  const railOpen = railOverride ?? railAuto;
  const paneOpen = paneOverride ?? paneAuto;

  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const actions = useSessionActions(host, token, feed, reportError, handleAuthFailure);
  const sessionActions = useMemo(
    () => ({
      ...actions,
      remove: async (sessionId: string) => {
        await actions.remove(sessionId);
        router.replace("/");
      },
    }),
    [actions, router],
  );

  // A failed first load fills the page; any later failure is a toast.
  const loadFailed = !stream.loaded && stream.error !== null;
  useEffect(() => {
    if (stream.error && !loadFailed) toast({ message: stream.error, tone: "error" });
  }, [stream.error, loadFailed, toast]);

  const agent = session ? agentModelLabel(session, catalog) : "";
  useEffect(() => {
    if (!session) return;
    const previous = document.title;
    document.title = `${agent.split(",")[0]} · ${session.title}`;
    return () => {
      document.title = previous;
    };
  }, [session, agent]);

  const rendered = useDeferredValue(events);
  const transcript = useMemo(() => deriveTranscript(rendered, { density }), [rendered, density]);
  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);
  const askState = useMemo(() => ({ evidence, snapshot: pendingQuestions }), [evidence, pendingQuestions]);
  const cwd = session?.cwd ?? "";
  const context = useMemo(() => ({ host, token, sessionId: id, modelOptions: [], cwd }), [host, token, id, cwd]);

  const terminalOnly = session ? liveTerminal(session.transport, catalog) : false;
  const hasPane = session ? hasTerminalPane(session.transport, catalog) : false;
  const activeView: SessionView = terminalOnly ? "terminal" : hasPane ? view : "chat";
  const structured = session ? fidelityFor(session.transport, catalog) === "structured" : true;

  const status: SessionStatus | null = useMemo(() => {
    if (!session) return null;
    const hasAsk = feed.askSessionIds.has(id) || (pendingQuestions?.questions.length ?? 0) > 0;
    const lamp = sessionLamp(session, hasAsk);
    const word = connection === "open" ? lampWord(lamp, session.status) : connectionLabel(connection, session.status);
    return { lamp, word, agent };
  }, [session, feed.askSessionIds, id, pendingQuestions, connection, agent]);

  const othersNeedYou = useMemo(() => [...feed.askSessionIds].filter((sessionId) => sessionId !== id).length, [feed.askSessionIds, id]);

  const onAction = (action: SessionAction, target: SessionRecord) => runSessionAction(action, target, actions, setDialog);

  const viewSwitch = hasPane || terminalOnly ? <ViewSwitch value={activeView} onChange={setView} variant={desktop ? "text" : "icon"} terminalOnly={terminalOnly} /> : null;
  const overflow = session ? <SessionOverflow session={session} subtitle={agent} density={density} onDensity={setDensity} onAction={onAction} /> : null;
  const title = session?.title ?? "";

  const body = loadFailed ? (
    <EmptyState
      title={stream.errorStatus === 404 ? "Session not found" : "Couldn't load the session"}
      detail={stream.error ?? undefined}
    />
  ) : (
    <Transcript
      turns={transcript.turns}
      full={density === "full"}
      structured={structured}
      status={session?.status ?? null}
      loading={stream.loading}
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
        <div
          className={cx(styles.screen, railOpen && styles.railOpen, paneOpen && styles.paneOpen)}
        >
          {desktop && railOpen ? (
            <div className={styles.rail}>
              <SessionRail currentId={id} />
            </div>
          ) : null}
          <section className={styles.conv} aria-label="Conversation">
            {desktop ? (
              <SessionHeader
                title={title}
                status={status}
                onTitle={switcher.open}
                railOpen={railOpen}
                onToggleRail={() => setRailOverride(!railOpen)}
                paneOpen={paneOpen}
                onTogglePane={() => setPaneOverride(!paneOpen)}
                view={viewSwitch}
                density={activeView === "chat" ? <DensityToggle variant="text" value={density} onChange={setDensity} /> : null}
                overflow={overflow}
              />
            ) : (
              <SessionBar title={title} status={status} othersNeedYou={othersNeedYou} onTitle={switcher.open} switcher={viewSwitch} overflow={overflow} />
            )}
            {body}
            <SessionFooter />
          </section>
          {desktop && paneOpen ? (
            <div className={styles.pane}>
              <SidePane />
            </div>
          ) : null}
        </div>
        <SessionDialogs dialog={dialog} actions={sessionActions} onClose={() => setDialog(null)} />
      </AskStateProvider>
    </SessionContextProvider>
  );
}
