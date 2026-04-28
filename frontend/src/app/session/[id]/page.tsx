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
      <Link className="back-link" href="/">
        Back to sessions
      </Link>
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
