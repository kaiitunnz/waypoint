"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { ApiError, createMessageSchedule, isAuthError, postAction } from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import type { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import type { OptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { canFork, composerAvailability, deliverInput, FollowUpError } from "@waypoint/web-core/session/send";
import type { SendMode } from "@waypoint/web-core/session/sendModes";
import type { SessionCommandInvocation, SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

interface Payload {
  mode: SendMode;
  text: string;
  command?: SessionCommandInvocation;
  attachmentIds: string[];
}

export interface SendFailure {
  message: string;
  // The HTTP status behind it, when it came from one.
  status?: number;
  // A control command's new session, whose follow-up send failed.
  openSessionId?: string;
  payload: Payload;
  // The field and tray as they were left: editing either dismisses the notice.
  draft: string;
  ids: string;
  // What the field keeps once the payload is sent.
  rest: string;
}

interface Options {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  draft: string;
  setDraft: (update: string | ((current: string) => string)) => void;
  attachments: ReturnType<typeof useAttachments>;
  commandInvocation: (text: string) => SessionCommandInvocation | undefined;
  resetCompletions: () => void;
  onPending: OptimisticSends["add"];
  onOpenSession: (sessionId: string) => void;
}

const IDLE_QUEUED: Record<Exclude<SendMode, "now">, string> = {
  with_previous: "Queued for when idle",
  next_cycle: "Queued for the next idle cycle",
};

export function useComposerSend({
  host,
  token,
  sessionId,
  session,
  catalog,
  draft,
  setDraft,
  attachments,
  commandInvocation,
  resetCompletions,
  onPending,
  onOpenSession,
}: Options) {
  const { toast } = useToast();
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [failure, setFailure] = useState<SendFailure | null>(null);
  const ids = attachments.readyIds.join(",");
  // One send at a time, whatever starts it.
  const inFlight = useRef(false);
  const latest = useRef({ draft, ids });
  useLayoutEffect(() => {
    latest.current = { draft, ids };
  });

  if (failure && (failure.draft !== draft || failure.ids !== ids)) setFailure(null);

  const canSend =
    !composerAvailability(session, catalog).disabled &&
    !sending &&
    !attachments.uploading &&
    (draft.trim() !== "" || attachments.readyIds.length > 0);

  async function run(payload: Payload, origin: { draft: string; rest: string }) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setFailure(null);
    const immediate = payload.mode === "now";
    const sent = attachments.items.flatMap((item) =>
      item.spec && payload.attachmentIds.includes(item.spec.id) ? [item.spec] : [],
    );
    let settle: (() => void) | null = null;
    if (immediate) {
      setDraft(origin.rest);
      resetCompletions();
      settle = onPending(payload.text, sent);
    }
    try {
      if (payload.mode === "now") {
        const { openSessionId } = await deliverInput({
          host,
          token,
          sessionId,
          session,
          catalog,
          text: payload.text,
          command: payload.command,
          attachments: payload.attachmentIds.length ? payload.attachmentIds : undefined,
          allowFork: canFork(session, catalog),
          reattachFirst: true,
        });
        attachments.release(payload.attachmentIds);
        if (openSessionId) onOpenSession(openSessionId);
      } else {
        await createMessageSchedule(host, token, sessionId, payload.text, {
          submit: true,
          trigger: "idle",
          idleBatchMode: payload.mode,
          command: payload.command ?? null,
          attachments: payload.attachmentIds,
        });
        setDraft((current) => (current === origin.draft ? origin.rest : current));
        resetCompletions();
        attachments.release(payload.attachmentIds);
        toast({ message: IDLE_QUEUED[payload.mode] });
      }
    } catch (error) {
      if (isAuthError(error)) {
        handleAuthFailure();
        return;
      }
      // The message goes back in the field, ahead of anything typed since.
      const current = latest.current.draft;
      const untouched = current === (immediate ? origin.rest : origin.draft);
      const restored = !immediate ? current : untouched ? origin.draft : `${payload.text}\n${current}`;
      if (restored !== current) setDraft(restored);
      setFailure({
        message: error instanceof Error ? error.message : "failed to send input",
        status: error instanceof ApiError || error instanceof FollowUpError ? error.status : undefined,
        openSessionId: error instanceof FollowUpError ? error.sessionId : undefined,
        payload,
        draft: restored,
        ids: latest.current.ids,
        rest: untouched ? origin.rest : current,
      });
    } finally {
      settle?.();
      inFlight.current = false;
      setSending(false);
    }
  }

  return {
    canSend,
    sending,
    stopping,
    failure,
    send: (mode: SendMode) => {
      const text = draft.trim();
      const attachmentIds = attachments.readyIds;
      if (!canSend) return;
      const payload = { mode, text, command: commandInvocation(text), attachmentIds };
      void run(payload, { draft, rest: "" });
    },
    retry: () => {
      if (!failure || failure.openSessionId) return;
      void run(failure.payload, { draft: failure.draft, rest: failure.rest });
    },
    dismiss: () => setFailure(null),
    stop: async () => {
      if (stopping) return;
      setStopping(true);
      try {
        await postAction(host, token, sessionId, "interrupt");
      } catch (error) {
        if (isAuthError(error)) {
          handleAuthFailure();
          return;
        }
        toast({ message: error instanceof Error ? error.message : "failed to interrupt", tone: "error" });
      } finally {
        setStopping(false);
      }
    },
  };
}
