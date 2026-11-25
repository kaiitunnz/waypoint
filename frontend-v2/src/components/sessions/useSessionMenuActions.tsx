"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";

import { useSessionActions } from "@waypoint/web-core/hooks/useSessionActions";
import type { SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { SessionDialogs, type HomeDialog } from "./SessionDialogs";
import type { SessionAction } from "./SessionMenu";

interface SessionMenuActions {
  // A menu's choice: pinning acts at once, the rest open their dialog.
  onAction: (action: SessionAction, session: SessionRecord) => void;
  openDialog: (dialog: HomeDialog) => void;
  // Render once, beside the menus.
  dialogs: ReactNode;
}

interface SessionMenuOptions {
  // Runs after a session is deleted from a dialog.
  onRemoved?: () => void;
  // Receives the session record after a rename or a pin change.
  onUpdated?: (session: SessionRecord) => void;
}

export function useSessionMenuActions({ onRemoved, onUpdated }: SessionMenuOptions = {}): SessionMenuActions {
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const { toast } = useToast();
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const actions = useSessionActions(host, token, feed, reportError, handleAuthFailure);
  const [dialog, setDialog] = useState<HomeDialog | null>(null);

  const confirmed = useMemo(
    () => ({
      ...actions,
      setPinned: (sessionId: string, pinned: boolean) => actions.setPinned(sessionId, pinned, onUpdated),
      rename: (session: SessionRecord, title: string) => actions.rename(session, title, onUpdated),
      remove: (sessionId: string) => actions.remove(sessionId, onRemoved),
    }),
    [actions, onRemoved, onUpdated],
  );
  const onAction = (action: SessionAction, session: SessionRecord) => {
    if (action === "pin" || action === "unpin") void confirmed.setPinned(session.id, action === "pin");
    else setDialog({ kind: action, session });
  };

  return {
    onAction,
    openDialog: setDialog,
    dialogs: <SessionDialogs dialog={dialog} actions={confirmed} onClose={() => setDialog(null)} />,
  };
}
