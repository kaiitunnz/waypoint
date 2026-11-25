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

// `onRemoved` runs after a session is deleted from a dialog.
export function useSessionMenuActions(onRemoved?: () => void): SessionMenuActions {
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const { toast } = useToast();
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const actions = useSessionActions(host, token, feed, reportError, handleAuthFailure);
  const [dialog, setDialog] = useState<HomeDialog | null>(null);

  const confirmed = useMemo(
    () => (onRemoved ? { ...actions, remove: (sessionId: string) => actions.remove(sessionId, onRemoved) } : actions),
    [actions, onRemoved],
  );
  const onAction = (action: SessionAction, session: SessionRecord) => {
    if (action === "pin" || action === "unpin") void actions.setPinned(session.id, action === "pin");
    else setDialog({ kind: action, session });
  };

  return {
    onAction,
    openDialog: setDialog,
    dialogs: <SessionDialogs dialog={dialog} actions={confirmed} onClose={() => setDialog(null)} />,
  };
}
