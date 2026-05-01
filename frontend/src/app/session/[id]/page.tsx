"use client";

import Image from "next/image";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConnectionState, SessionDetail } from "@/components/SessionDetail";
import { clearToken, readHost, readToken } from "@/lib/store";

export default function SessionPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    setSessionId(params.id);
    setHost(readHost());
    setToken(readToken());
  }, [params]);

  // Stable across renders so SessionDetail's WS effect doesn't tear down
  // and reconnect every time `connection` changes — the prop flows into
  // its handleAuthFailure useCallback, which is a dep of the socket
  // useEffect.
  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  const connectionLabel =
    connection === "open"
      ? "live"
      : connection === "reconnecting"
        ? "reconnecting"
        : "connecting";

  return (
    <main className="page-shell has-composer">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            <Image src="/waypoint.svg" alt="" width={38} height={38} priority />
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · session</p>
            <h1 className="app-bar-title">Live transcript</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          {host && token && sessionId ? (
            <span
              className={`app-bar-status ${connection}`}
              title={`Backend socket ${connection}`}
            >
              {connectionLabel}
            </span>
          ) : null}
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
        </div>
      </header>
      {host && token && sessionId ? (
        <SessionDetail
          host={host}
          token={token}
          sessionId={sessionId}
          onAuthFailure={handleAuthFailure}
          onConnectionChange={setConnection}
        />
      ) : null}
    </main>
  );
}
