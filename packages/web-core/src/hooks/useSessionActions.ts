"use client";

import { useMemo } from "react";

import {
  deleteExitedSessions,
  deleteSession,
  isAuthError,
  postAction,
  setSessionPinned,
  setSessionTitle,
} from "../api";
import { isExited, renameTarget, revertTitle, withTitle } from "../sessionActions";
import {
  applySessionState,
  forgetHistorySession,
  invalidateSessionHistory,
} from "../sessionHistory";
import type { SessionRecord } from "../types";
import type { SessionsFeed } from "./useSessionsFeed";

export interface SessionActions {
  setPinned: (sessionId: string, pinned: boolean) => Promise<void>;
  rename: (session: SessionRecord, draft: string) => Promise<void>;
  terminate: (sessionId: string) => Promise<void>;
  remove: (sessionId: string) => Promise<void>;
  removeExited: () => Promise<void>;
}

type FeedMutations = Pick<SessionsFeed, "upsertSession" | "removeSessions" | "patchSession">;

// Row actions on the session list. A failure goes to `onError` as a message; an
// expired token goes to `onAuthFailure`. Each action updates the live list and
// the history cache right away, ahead of the server's next list frame.
export function useSessionActions(
  host: string,
  token: string,
  feed: FeedMutations,
  onError: (message: string) => void,
  onAuthFailure?: () => void,
): SessionActions {
  const { upsertSession, removeSessions, patchSession } = feed;
  return useMemo(() => {
    async function attempt(run: () => Promise<void>, fallback: string, onFail?: () => void) {
      try {
        await run();
      } catch (error) {
        onFail?.();
        if (isAuthError(error)) {
          onAuthFailure?.();
          return;
        }
        onError(error instanceof Error ? error.message : fallback);
      }
    }
    return {
      setPinned: (sessionId, pinned) =>
        attempt(async () => {
          const updated = await setSessionPinned(host, token, sessionId, pinned);
          upsertSession(updated);
          applySessionState(host, updated);
        }, "failed to update pin"),
      rename: (session, draft) => {
        const title = renameTarget(session, draft);
        if (title === null) return Promise.resolve();
        patchSession(session.id, (current) => withTitle(current, title));
        return attempt(
          async () => {
            const updated = await setSessionTitle(host, token, session.id, title);
            patchSession(session.id, () => updated);
            applySessionState(host, updated);
          },
          "failed to update title",
          () => patchSession(session.id, (current) => revertTitle(current, title, session.title)),
        );
      },
      terminate: (sessionId) =>
        attempt(() => postAction(host, token, sessionId, "terminate"), "failed to terminate"),
      remove: (sessionId) =>
        attempt(async () => {
          await deleteSession(host, token, sessionId);
          removeSessions((session) => session.id === sessionId);
          forgetHistorySession(host, sessionId);
        }, "failed to delete session"),
      removeExited: () =>
        attempt(async () => {
          await deleteExitedSessions(host, token);
          // The server deletes in the background; each deletion's list frame moves
          // the counts, and this drops the rows already on screen.
          removeSessions(isExited);
          invalidateSessionHistory(host);
        }, "failed to delete exited sessions"),
    };
  }, [host, token, upsertSession, removeSessions, patchSession, onError, onAuthFailure]);
}
