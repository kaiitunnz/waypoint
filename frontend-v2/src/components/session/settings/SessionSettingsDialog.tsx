"use client";

import type { AssistantSettingsSource, SessionRecord } from "@waypoint/web-core/types";

import { Dialog } from "@/components/ui/Dialog";

import { SessionSettings } from "./SessionSettings";
import styles from "./SessionSettingsDialog.module.css";

interface SessionSettingsDialogProps {
  session: SessionRecord;
  assistant?: AssistantSettingsSource;
  onApplied: (session: SessionRecord) => void;
  onClose: () => void;
}

// A session's settings over the screen: a modal on a pointer client, a sheet on touch.
export function SessionSettingsDialog({ session, assistant, onApplied, onClose }: SessionSettingsDialogProps) {
  return (
    <SessionSettings
      session={session}
      assistant={assistant}
      onApplied={onApplied}
      onDone={onClose}
      layout={({ fields, footer, busy }) => (
        <Dialog
          open
          // Closing while a change is going through would leave its outcome unseen.
          onClose={busy ? () => undefined : onClose}
          title="Settings"
          subtitle={<span className={styles.session}>{session.title || "Session"}</span>}
          size="lg"
          footer={footer}
        >
          {fields}
        </Dialog>
      )}
    />
  );
}
