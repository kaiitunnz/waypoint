"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { SessionDetail } from "@/components/SessionDetail";
import { ThemeToggle } from "@/components/ThemeToggle";
import { fetchMe, isAuthError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import { AssistantSummary } from "@/lib/types";

type LoadState = "loading" | "ready" | "disabled" | "error";

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

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    if (!currentHost || !currentToken) {
      router.replace("/");
      return;
    }
    let active = true;
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
          <section className="assistant-banner" aria-label="Assistant thread details">
            <div className="assistant-banner-lead">
              <span className="assistant-glyph" aria-hidden="true">
                ✦
              </span>
              <div className="assistant-banner-text">
                <p className="assistant-banner-eyebrow">{assistant.backend}</p>
                <p className="assistant-banner-note">
                  One persistent thread. Recover it anytime with the session id
                  below.
                </p>
              </div>
              <span className={`assistant-status assistant-status-${assistant.status}`}>
                {assistant.status.replace("_", " ")}
              </span>
            </div>
            <div className="assistant-ids">
              {/* The backend's own session/thread id (the one you'd pass to
                  `claude --resume` etc.), falling back to the Waypoint id only
                  when the backend exposes none. */}
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
            Add an <code>assistant</code> block to <code>waypoint.yaml</code> and
            restart the backend to bring up your personal assistant.
          </p>
          <pre className="assistant-snippet">
            <code>{`assistant:
  backend: claude_code
  model: opus
  permission_mode: bypassPermissions`}</code>
          </pre>
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
            The backend didn’t respond. Check that Waypoint is running, then{" "}
            <Link className="back-link" href="/assistant">
              retry
            </Link>
            .
          </p>
        </section>
      ) : null}
    </main>
  );
}
