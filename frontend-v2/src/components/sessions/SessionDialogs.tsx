"use client";

import type { SessionActions } from "@waypoint/web-core/hooks/useSessionActions";
import type { SessionRecord } from "@waypoint/web-core/types";

import { SessionSettingsDialog } from "@/components/session/settings/SessionSettingsDialog";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

import { RenameDialog } from "./RenameDialog";

export type HomeDialog =
  | { kind: "rename"; session: SessionRecord }
  | { kind: "settings"; session: SessionRecord }
  | { kind: "terminate"; session: SessionRecord }
  | { kind: "delete"; session: SessionRecord }
  | { kind: "deleteExited" };

function confirmation(dialog: Exclude<HomeDialog, { kind: "rename" | "settings" }>, actions: SessionActions) {
  switch (dialog.kind) {
    case "terminate":
      return {
        title: "Terminate session",
        detail: "Any running command will be stopped.",
        confirmLabel: "Terminate",
        run: () => actions.terminate(dialog.session.id),
      };
    case "delete":
      return {
        title: "Delete session",
        detail: "The session and its transcript are deleted. This cannot be undone.",
        confirmLabel: "Delete",
        run: () => actions.remove(dialog.session.id),
      };
    case "deleteExited":
      return {
        title: "Delete exited sessions",
        detail: "Every exited session and its transcript is deleted. This cannot be undone.",
        confirmLabel: "Delete exited",
        run: () => actions.removeExited(),
      };
  }
}

interface SessionDialogsProps {
  dialog: HomeDialog | null;
  actions: SessionActions;
  onClose: () => void;
}

export function SessionDialogs({ dialog, actions, onClose }: SessionDialogsProps) {
  if (!dialog) return null;
  if (dialog.kind === "rename") {
    return (
      <RenameDialog
        session={dialog.session}
        onSave={(title) => void actions.rename(dialog.session, title)}
        onClose={onClose}
      />
    );
  }
  if (dialog.kind === "settings") {
    return <SessionSettingsDialog session={dialog.session} onApplied={actions.recordUpdate} onClose={onClose} />;
  }
  const { run, ...text } = confirmation(dialog, actions);
  return <ConfirmDialog open {...text} onConfirm={run} onClose={onClose} />;
}
