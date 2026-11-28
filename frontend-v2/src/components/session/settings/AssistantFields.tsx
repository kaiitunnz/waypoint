"use client";

import { useCallback, useState } from "react";

import { agentTransports, defaultTransportFor, humaniseBackend, launchableAgents, transportLabel, type BackendCatalog } from "@waypoint/web-core/backends";
import type { SessionSettingsController } from "@waypoint/web-core/hooks/useSessionSettings";
import { formatRelativeTime } from "@waypoint/web-core/usage";
import type { AssistantSettingsSource, AssistantThreadOption, Backend, SessionRecord } from "@waypoint/web-core/types";

import { ChoiceRow, type ChoiceOption } from "@/components/ui/Choice";

import styles from "./SettingsFields.module.css";

interface AssistantFieldsProps {
  controller: SessionSettingsController;
  catalog: BackendCatalog;
  assistant: AssistantSettingsSource;
  session: SessionRecord;
  disabled: boolean;
}

// The Personal Assistant's agent, interface and thread. Changing any of them replaces
// the assistant's conversation, which is kept as a stopped session.
export function AssistantFields({ controller, catalog, assistant, session, disabled }: AssistantFieldsProps) {
  const { assistantBackend, assistantTransport, assistantThreadId, accountProfileId } = controller;
  const backend: Backend = assistantBackend ?? session.backend;
  const agents = launchableAgents(
    assistant.backends.map((descriptor) => descriptor.id),
    catalog,
  );
  const transports = assistantBackend ? agentTransports(assistantBackend, catalog) : [];
  // A thread belongs to one agent and profile, so a list read for another is not used.
  const listKey = `${backend}:${accountProfileId ?? ""}`;
  const [loaded, setLoaded] = useState<{ key: string; threads: AssistantThreadOption[] } | null>(null);
  const threads = loaded?.key === listKey ? loaded.threads : null;
  const loadThreads = useCallback(() => {
    if (threads !== null) return;
    void assistant
      .listThreads(backend, accountProfileId)
      .then((list) => setLoaded({ key: listKey, threads: list }))
      .catch(() => setLoaded({ key: listKey, threads: [] }));
  }, [assistant, backend, accountProfileId, listKey, threads]);

  const agentOptions: ChoiceOption[] = agents.map((id) => ({ value: id, label: humaniseBackend(id, catalog) }));
  const transportOptions: ChoiceOption[] = transports.map((id) => ({ value: id, label: transportLabel(id, catalog) ?? id }));
  const threadOptions: ChoiceOption[] = [
    { value: "", label: "New conversation" },
    ...(threads ?? []).map((thread) => ({
      value: thread.id,
      label: thread.title,
      detail: [thread.updatedAt ? formatRelativeTime(thread.updatedAt) : null, thread.preview].filter(Boolean).join(" · ") || undefined,
    })),
  ];
  const selectedThread = assistantThreadId ?? "";
  // The staged thread shows even before the list has loaded.
  if (selectedThread && !threadOptions.some((option) => option.value === selectedThread)) {
    threadOptions.push({ value: selectedThread, label: selectedThread });
  }

  return (
    <section>
      <h3 className={styles.heading}>Assistant</h3>
      <div className={styles.rows}>
        <ChoiceRow label="Agent" value={backend} options={agentOptions} onChange={(value) => controller.setAssistantBackend(value as Backend)} disabled={disabled} staged={backend !== session.backend} />
        {transports.length > 1 ? (
          <ChoiceRow
            label="Interface"
            value={assistantTransport ?? defaultTransportFor(backend, catalog) ?? ""}
            options={transportOptions}
            onChange={controller.setAssistantTransport}
            disabled={disabled}
            staged={assistantTransport !== null && assistantTransport !== session.transport}
          />
        ) : null}
        <ChoiceRow label="Resume thread" value={selectedThread} options={threadOptions} onChange={(value) => controller.setAssistantThreadId(value || null)} onOpen={loadThreads} disabled={disabled} staged={selectedThread !== ""} />
      </div>
    </section>
  );
}
