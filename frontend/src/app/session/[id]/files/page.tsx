"use client";

import Image from "next/image";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import { ThemeToggle } from "@/components/ThemeToggle";
import { fetchSession, resolveWorkspacePath } from "@/lib/api";
import { humaniseBackend, useBackendCatalog } from "@/lib/backends";
import { readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import type { SessionRecord } from "@/lib/types";

export default function SessionFilesPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [initialPath, setInitialPath] = useState<string | undefined>(undefined);
  const [initialDir, setInitialDir] = useState<string | undefined>(undefined);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const catalog = useBackendCatalog(host || null, token || null, null);

  useEffect(() => {
    const id = params.id;
    const h = readHost();
    const t = readToken();
    setSessionId(id);
    setHost(h);
    setToken(t);

    if (h && t && id) {
      fetchSession(h, t, id)
        .then((s) => setSession(s))
        .catch(() => {
          // Title is decorative here; ignore failures.
        });
    }

    const rawPath = searchParams.get("path");
    if (rawPath && h && t && id) {
      resolveWorkspacePath(h, t, id, rawPath)
        .then((resolved) => {
          if (resolved.kind === "file") {
            setInitialPath(resolved.path);
          } else {
            setInitialDir(resolved.path);
          }
        })
        .catch(() => {
          // If resolve fails, open without a seeded path.
        });
    }
  }, [params, searchParams]);

  // Match the session page's tab-title format (`<backend> · <title>`), with a
  // "· Files" marker so the two tabs stay distinguishable. Restore the previous
  // title on unmount, as SessionDetail does.
  useEffect(() => {
    if (!session) return;
    const prev = document.title;
    document.title = `${humaniseBackend(session.backend, catalog)} · ${session.title} · Files`;
    return () => {
      document.title = prev;
    };
  }, [session, catalog]);

  return (
    <div className="wp-page">
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
            <p className="app-bar-eyebrow">Waypoint · files</p>
            <h1 className="app-bar-title">{session?.title || "Workspace files"}</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          {sessionId ? (
            <Link className="back-link" href={`/session/${sessionId}`}>
              ← back to session
            </Link>
          ) : null}
          <ThemeToggle />
        </div>
      </header>
      {host && token && sessionId ? (
        <WorkspaceExplorer
          host={host}
          token={token}
          sessionId={sessionId}
          recentPaths={[]}
          initialPath={initialPath}
          initialDir={initialDir}
        />
      ) : null}
    </div>
  );
}
