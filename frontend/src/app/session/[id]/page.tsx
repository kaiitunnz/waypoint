"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { SessionDetail } from "@/components/SessionDetail";
import { clearToken, readHost, readToken } from "@/lib/store";

export default function SessionPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessionId, setSessionId] = useState("");

  useEffect(() => {
    setSessionId(params.id);
    setHost(readHost());
    setToken(readToken());
  }, [params]);

  return (
    <main className="page-shell">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            W
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · session</p>
            <h1 className="app-bar-title">Live transcript</h1>
          </div>
        </div>
        <Link className="back-link" href="/">
          ← all sessions
        </Link>
      </header>
      {host && token && sessionId ? (
        <SessionDetail
          host={host}
          token={token}
          sessionId={sessionId}
          onAuthFailure={() => {
            clearToken();
            setToken("");
            router.replace("/");
          }}
        />
      ) : null}
    </main>
  );
}
