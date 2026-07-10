"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AssistantMark } from "@/components/AssistantMark";
import { AssistantControls, SessionDetail } from "@/components/SessionDetail";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  attachAssistant,
  fetchBackendThreads,
  fetchMe,
  isAuthError,
  reattachAssistant,
  resetAssistant,
  terminateAssistant,
} from "@/lib/api";
import { buildCatalog, humaniseBackend, transportPresentation } from "@/lib/backends";
import { copyText } from "@/lib/clipboard";
import { useCopied } from "@/lib/use-copied";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import {
  AssistantSummary,
  Backend,
  BackendDescriptor,
  SessionStatus,
  SessionTransport,
} from "@/lib/types";

type LoadState = "loading" | "ready" | "disabled" | "error";

// Thread summaries differ per backend: claude/codex send ISO datetimes, while
// opencode sends epoch milliseconds. Normalise to an ISO string the relative-
// time formatter can parse; return "" when there's no usable timestamp.
function toIsoTimestamp(value: string | number | null | undefined): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    const ms = value < 1e12 ? value * 1000 : value;
    return new Date(ms).toISOString();
  }
  return typeof value === "string" ? value : "";
}

const STATUS_LABELS: Record<SessionStatus, string> = {
  starting: "Starting…",
  idle: "Ready",
  waiting_input: "Waiting on you",
  running: "Working…",
  interrupted: "Interrupted",
  exited: "Stopped",
  error: "Error",
};

function CopyField({ label, value }: { label: string; value: string }) {
  const { copied, markCopied } = useCopied();
  const copy = useCallback(() => {
    void copyText(value).then((ok) => {
      if (ok) markCopied();
    });
  }, [value, markCopied]);
  return (
    <div className="assistant-id">
      <span className="assistant-id-label">{label}</span>
      <button
        className="assistant-id-value"
        onClick={copy}
        title={`Copy ${label}`}
        aria-label={copied ? `${label} copied` : `Copy ${label}`}
        type="button"
      >
        <code>{value}</code>
        <span
          className={`assistant-id-copy${copied ? " is-copied" : ""}`}
          aria-hidden="true"
        >
          {copied ? "copied" : "copy"}
        </span>
      </button>
    </div>
  );
}

export default function AssistantPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [assistant, setAssistant] = useState<AssistantSummary | null>(null);
  const [backends, setBackends] = useState<BackendDescriptor[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const catalog = useMemo(() => buildCatalog(backends), [backends]);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  const load = useCallback(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    if (!currentHost || !currentToken) {
      router.replace("/");
      return () => {};
    }
    let active = true;
    setState("loading");
    fetchMe(currentHost, currentToken)
      .then((me) => {
        if (!active) return;
        setBackends(me.backends ?? []);
        if (me.assistant) {
          setAssistant(me.assistant);
          setState("ready");
        } else {
          setState("disabled");
        }
      })
      .catch((err) => {
        if (!active) return;
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setState("error");
      });
    return () => {
      active = false;
    };
  }, [router, handleAuthFailure]);

  useEffect(() => load(), [load]);

  // Run a lifecycle action and adopt the returned summary. Switching backend /
  // clearing context changes the session id, which remounts SessionDetail via
  // its key; terminate / reattach keep the id and just refresh the banner.
  const applyControl = useCallback(
    async (run: () => Promise<AssistantSummary>) => {
      try {
        setAssistant(await run());
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        // Surface to the composer's lifecycle footer so a failed switch /
        // terminate / reattach isn't silently dropped.
        throw err;
      }
    },
    [handleAuthFailure],
  );

  // Memoised so its identity is stable across renders — the composer keys a
  // thread-fetch effect on it.
  const controls = useMemo<AssistantControls>(
    () => ({
      backends,
      supportsReattach: assistant?.supports_reattach ?? false,
      accountProfilesFor: (backend: Backend) => catalog.accountProfilesFor(backend),
      onSwitchBackend: (
        backend: Backend,
        transport: SessionTransport,
        accountProfileId: string | null,
      ) =>
        applyControl(() =>
          resetAssistant(host, token, {
            backend,
            transport,
            account_profile_id: accountProfileId,
          }),
        ),
      onAttachThread: (
        backend: Backend,
        threadId: string,
        accountProfileId: string | null,
      ) =>
        applyControl(() =>
          attachAssistant(host, token, {
            backend,
            thread_id: threadId,
            account_profile_id: accountProfileId,
          }),
        ),
      onClearContext: () => applyControl(() => resetAssistant(host, token, {})),
      onTerminate: () => applyControl(() => terminateAssistant(host, token)),
      onReattach: () => applyControl(() => reattachAssistant(host, token)),
      listThreads: async (backend: Backend, accountProfileId: string | null) => {
        try {
          const threads = await fetchBackendThreads<{
            id: string;
            title?: string | null;
            updated_at?: string | number | null;
            preview?: string | null;
          }>(host, token, backend, { accountProfileId });
          return threads.map((thread) => ({
            id: thread.id,
            title: thread.title || thread.id,
            updatedAt: toIsoTimestamp(thread.updated_at),
            preview: thread.preview ?? null,
          }));
        } catch (err) {
          if (isAuthError(err)) handleAuthFailure();
          throw err;
        }
      },
    }),
    [
      backends,
      assistant?.supports_reattach,
      catalog,
      host,
      token,
      applyControl,
      handleAuthFailure,
    ],
  );

  return (
    <main className="page-shell has-composer">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            <Image
              src={theme === "light" ? "/waypoint-light.svg" : "/waypoint.svg"}
              alt=""
              width={38}
              height={38}
              priority
            />
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · assistant</p>
            <h1 className="app-bar-title">Personal assistant</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {state === "ready" && assistant ? (
        <>
          <section className="assistant-banner" aria-label="Assistant details">
            <div className="assistant-banner-lead">
              <span className="assistant-glyph" aria-hidden="true">
                <AssistantMark />
              </span>
              <div className="assistant-banner-text">
                <p className="assistant-banner-eyebrow">
                  {humaniseBackend(assistant.backend, catalog)} ·{" "}
                  {transportPresentation(assistant.transport, catalog).name}
                </p>
                <p className="assistant-banner-note">
                  Your persistent assistant — ask about this host or your running
                  sessions.
                </p>
              </div>
              <span
                className={`assistant-status assistant-status-${assistant.status}`}
              >
                {STATUS_LABELS[assistant.status] ?? assistant.status}
              </span>
            </div>
            {/* The backend's own session/thread id (for `claude --resume` and
                the like) is a recovery fallback, not headline info — keep it as
                a quiet footnote. */}
            <div className="assistant-banner-foot">
              <CopyField
                label="session id"
                value={assistant.native_thread_id ?? assistant.session_id}
              />
            </div>
          </section>
          {host && token ? (
            <SessionDetail
              key={assistant.session_id}
              host={host}
              token={token}
              sessionId={assistant.session_id}
              onAuthFailure={handleAuthFailure}
              assistant
              assistantControls={controls}
            />
          ) : null}
        </>
      ) : null}

      {state === "disabled" ? (
        <section className="panel bordered assistant-empty">
          <span className="assistant-glyph assistant-glyph-lg" aria-hidden="true">
            <AssistantMark />
          </span>
          <h2>No assistant configured</h2>
          <p className="muted">
            Your assistant isn’t set up yet. On the host, add an{" "}
            <code>assistant</code> block to <code>waypoint.yaml</code> and restart
            the backend.
          </p>
          <details className="assistant-snippet-details">
            <summary>Show config example</summary>
            <pre className="assistant-snippet">
              <code>{`assistant:
  backend: claude_code
  model: opus
  permission_mode: bypassPermissions`}</code>
            </pre>
          </details>
        </section>
      ) : null}

      {state === "loading" ? (
        <section className="panel bordered assistant-loading" aria-busy="true">
          <span className="assistant-spinner" aria-hidden="true" />
          <p className="muted">Loading assistant…</p>
        </section>
      ) : null}

      {state === "error" ? (
        <section className="panel bordered assistant-empty">
          <span className="assistant-glyph assistant-glyph-lg" aria-hidden="true">
            <AssistantMark />
          </span>
          <h2>Couldn’t load the assistant</h2>
          <p className="muted">
            The backend didn’t respond. Check that Waypoint is running, then
            retry.
          </p>
          <button type="button" className="primary" onClick={() => load()}>
            Retry
          </button>
        </section>
      ) : null}
    </main>
  );
}
