"use client";

import { useEffect, useState } from "react";

import { fetchWorkspaceGitStatus } from "../api";
import { summarizeChanges, type ChangesSummary } from "../session/changesSummary";

// The session workspace's changes, read from git status. `refreshKey` reloads them
// when it changes, such as at the end of a turn; so does the window coming back into
// view. Null while loading and wherever git status is unavailable (off, no
// repository, a target that cannot be reached).
export function useWorkspaceGitStatus(host: string, token: string, sessionId: string, refreshKey: string): ChangesSummary | null {
  const [summary, setSummary] = useState<ChangesSummary | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") setFocusTick((tick) => tick + 1);
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, []);

  useEffect(() => {
    // A later request supersedes this one even where it answers first.
    let current = true;
    fetchWorkspaceGitStatus(host, token, sessionId)
      .then((status) => {
        if (current) setSummary(summarizeChanges(status));
      })
      .catch(() => {
        if (current) setSummary(null);
      });
    return () => {
      current = false;
    };
  }, [host, token, sessionId, refreshKey, focusTick]);

  return summary;
}
