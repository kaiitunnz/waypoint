"use client";

import { useMemo, type ReactNode } from "react";

import { useSessionSettings, type AssistantSettingsControls } from "@waypoint/web-core/hooks/useSessionSettings";
import type { AssistantSettingsSource, SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

import { SettingsFields } from "./SettingsFields";
import { SettingsFooter } from "./SettingsFooter";
import styles from "./SessionSettings.module.css";

interface SessionSettingsProps {
  session: SessionRecord;
  // Present only for the Personal Assistant.
  assistant?: AssistantSettingsSource;
  // Each step that went through, with the session record it returned.
  onApplied: (session: SessionRecord) => void;
  // The editor is finished, applied or not.
  onDone: () => void;
  // Places the form's parts: the side pane, or a dialog with its own footer.
  layout: (parts: { fields: ReactNode; footer: ReactNode; busy: boolean }) => ReactNode;
}

// The settings of one session, drafted in a form until applied. It reads the session as it
// is when it opens, so a pane that stays open does not refresh it under an edit.
export function SessionSettings({ session, assistant, onApplied, onDone, layout }: SessionSettingsProps) {
  const { host, token, catalog } = useShellData();
  const assistantControls = useMemo<AssistantSettingsControls | undefined>(
    () =>
      assistant && {
        onSwitchBackend: async (backend, transport, profileId) => {
          await assistant.onSwitchBackend(backend, transport, profileId);
        },
        onAttachThread: async (backend, threadId, profileId) => {
          await assistant.onAttachThread(backend, threadId, profileId);
        },
      },
    [assistant],
  );
  const controller = useSessionSettings({
    host,
    token,
    open: true,
    session,
    catalog,
    onApplied,
    onAuthFailure: handleAuthFailure,
    isAssistant: Boolean(assistant),
    assistantControls,
  });
  const { loading, loadError } = controller;

  const fields = loading ? (
    <p className={styles.note}>Loading settings…</p>
  ) : loadError ? (
    <p className={styles.error} role="alert">
      {loadError}
    </p>
  ) : (
    <SettingsFields controller={controller} catalog={catalog} session={session} assistant={assistant} />
  );
  return layout({
    fields,
    footer: <SettingsFooter controller={controller} onCancel={onDone} onApplied={onDone} />,
    busy: controller.applying,
  });
}
