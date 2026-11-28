"use client";

import { useState } from "react";

import type { SessionSettingsController } from "@waypoint/web-core/hooks/useSessionSettings";
import type { AssistantSettingsSource, Backend, SessionRecord } from "@waypoint/web-core/types";

import { AssistantFields } from "@/components/session/settings/AssistantFields";
import { useShellData } from "@/lib/shell-data";

const THREADS = [
  { id: "thread-1", title: "Personal Assistant", updatedAt: "2026-11-27T09:12:00Z", preview: "Plan the week" },
  { id: "thread-2", title: "Personal Assistant", updatedAt: "2026-11-20T18:40:00Z", preview: null },
];

// The Assistant section of the session settings on stand-in state: the real page supplies
// the live assistant's lifecycle, which no other session has.
export function AssistantDemo() {
  const { catalog } = useShellData();
  const [staged, setStaged] = useState<{ backend: Backend; transport: string | null; thread: string | null }>({
    backend: "claude_code",
    transport: null,
    thread: null,
  });
  const controller = {
    assistantBackend: staged.backend,
    assistantTransport: staged.transport,
    assistantThreadId: staged.thread,
    accountProfileId: null,
    setAssistantBackend: (backend: Backend) => setStaged({ backend, transport: null, thread: null }),
    setAssistantTransport: (transport: string) => setStaged((current) => ({ ...current, transport })),
    setAssistantThreadId: (thread: string | null) => setStaged((current) => ({ ...current, thread })),
  } as unknown as SessionSettingsController;
  const assistant: AssistantSettingsSource = {
    backends: catalog.all(),
    accountProfilesFor: () => [],
    onSwitchBackend: async () => undefined,
    onAttachThread: async () => undefined,
    listThreads: async () => THREADS,
  };
  const session = { backend: "claude_code", transport: "claude_tty" } as SessionRecord;
  return <AssistantFields controller={controller} catalog={catalog} assistant={assistant} session={session} disabled={false} />;
}
