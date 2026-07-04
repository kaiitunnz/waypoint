"use client";

import Image from "next/image";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { InboxItemPane } from "@/components/InboxItemPane";
import { ThemeToggle } from "@/components/ThemeToggle";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";

export default function InboxItemPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [itemId, setItemId] = useState("");

  useEffect(() => {
    setItemId(params.id);
    setHost(readHost());
    setToken(readToken());
  }, [params]);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  const handleDeleted = useCallback(() => {
    router.replace("/inbox");
  }, [router]);

  return (
    <main className="page-shell inbox-shell">
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
            <p className="app-bar-eyebrow">Waypoint · inbox</p>
            <h1 className="app-bar-title">Inbox item</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <Link className="back-link" href="/inbox">
            ← inbox
          </Link>
          <ThemeToggle />
        </div>
      </header>
      {host && token && itemId ? (
        <div className="inbox-single-pane">
          <InboxItemPane
            host={host}
            token={token}
            itemId={itemId}
            onAuthFailure={handleAuthFailure}
            onDeleted={handleDeleted}
          />
        </div>
      ) : null}
    </main>
  );
}
