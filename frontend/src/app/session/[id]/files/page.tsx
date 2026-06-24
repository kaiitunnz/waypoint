"use client";

import Image from "next/image";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import { ThemeToggle } from "@/components/ThemeToggle";
import { resolveWorkspacePath } from "@/lib/api";
import { readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";

export default function SessionFilesPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [initialPath, setInitialPath] = useState<string | undefined>(undefined);
  const [initialDir, setInitialDir] = useState<string | undefined>(undefined);

  useEffect(() => {
    const id = params.id;
    const h = readHost();
    const t = readToken();
    setSessionId(id);
    setHost(h);
    setToken(t);

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
