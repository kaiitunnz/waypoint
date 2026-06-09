"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { SessionDetail } from "@/components/SessionDetail";
import { ThemeToggle } from "@/components/ThemeToggle";
import { fetchMe, isAuthError } from "@/lib/api";
import { humaniseBackend } from "@/lib/backends";
import { copyText } from "@/lib/clipboard";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import { AssistantSummary, SessionStatus } from "@/lib/types";

type LoadState = "loading" | "ready" | "disabled" | "error";

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
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    void copyText(value).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  }, [value]);
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
  const [state, setState] = useState<LoadState>("loading");

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
                ✦
              </span>
              <div className="assistant-banner-text">
                <p className="assistant-banner-eyebrow">
                  {humaniseBackend(assistant.backend)}
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
              host={host}
              token={token}
              sessionId={assistant.session_id}
              onAuthFailure={handleAuthFailure}
              assistant
            />
          ) : null}
        </>
      ) : null}

      {state === "disabled" ? (
        <section className="panel bordered assistant-empty">
          <span className="assistant-glyph assistant-glyph-lg" aria-hidden="true">
            ✦
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
            ✦
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
