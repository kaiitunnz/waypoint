"use client";

import type { AssistantSettingsSource, SessionRecord } from "@waypoint/web-core/types";

import { SessionSettings } from "./SessionSettings";
import styles from "./SessionSettingsPane.module.css";

interface SessionSettingsPaneProps {
  session: SessionRecord;
  assistant?: AssistantSettingsSource;
  onApplied: (session: SessionRecord) => void;
  onDone: () => void;
}

// A session's settings pushed into the side pane: the fields scroll above the footer.
export function SessionSettingsPane({ session, assistant, onApplied, onDone }: SessionSettingsPaneProps) {
  return (
    <SessionSettings
      session={session}
      assistant={assistant}
      onApplied={onApplied}
      onDone={onDone}
      layout={({ fields, footer }) => (
        <div className={styles.pane}>
          <div className={styles.fields}>{fields}</div>
          <div className={styles.footer}>{footer}</div>
        </div>
      )}
    />
  );
}
