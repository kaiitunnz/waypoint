"use client";

import { useEffect, useMemo, useState } from "react";

import { fetchDirectorySuggestions } from "../api";
import { FOLDER_SUGGEST_DELAY_MS, folderOptions, isCompletable, type FolderSuggestions } from "../launch/folders";

// Recent folders plus the server's directory completions for what is typed so far,
// on this host or the selected launch target.
export function useFolderSuggestions(
  host: string,
  token: string,
  cwd: string,
  launchTargetId: string | null,
  recentCwds: string[],
): string[] {
  const [suggested, setSuggested] = useState<FolderSuggestions>({ targetId: null, dirs: [] });
  const completable = isCompletable(cwd);

  useEffect(() => {
    if (!completable || !host || !token) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchDirectorySuggestions(host, token, cwd, launchTargetId, controller.signal)
        .then((dirs) => {
          if (!controller.signal.aborted) {
            setSuggested({ targetId: launchTargetId, dirs });
          }
        })
        .catch(() => undefined);
    }, FOLDER_SUGGEST_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [host, token, cwd, launchTargetId, completable]);

  return useMemo(
    () => folderOptions(recentCwds, suggested, cwd, launchTargetId),
    [recentCwds, suggested, cwd, launchTargetId],
  );
}
