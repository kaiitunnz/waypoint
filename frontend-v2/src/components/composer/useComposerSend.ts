"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { ApiError, createMessageSchedule, isAuthError, postAction } from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import type { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import type { OptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { composerAvailability, draftAfterFailedSend } from "@waypoint/web-core/session/send";
import type { SendMode } from "@waypoint/web-core/session/sendModes";
import { deliverInput, FollowUpError } from "@waypoint/web-core/sessionDelivery";
import type { SessionCommandInvocation, SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { useFailureToast } from "@/lib/useFailureToast";
import { useMountedRef } from "@/lib/useMountedRef";

interface Payload {
  mode: SendMode;
  text: string;
  command?: SessionCommandInvocation;
  attachmentIds: string[];
}

export interface SendFailure {
  message: string;
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
  onScheduled: () => void;
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
  onScheduled,
}: Options) {
  const { toast } = useToast();
  const reportFailure = useFailureToast();
  const mounted = useMountedRef();
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [failure, setFailure] = useState<SendFailure | null>(null);
  // The tray's files, uploaded or not: adding or removing one dismisses a failure.
  const ids = attachments.items.map((item) => item.localId).join(",");
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
    let settle: ((echoes: boolean) => void) | null = null;
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
        });
        attachments.release(payload.attachmentIds);
        // A control command's text lands in the other session, not this one.
        settle?.(!openSessionId);
        if (openSessionId) {
          if (mounted.current) onOpenSession(openSessionId);
          // The person has moved on: offer the new session instead of taking them there.
          else {
            toast({
              message: `New session from ${session?.title ?? "the session you left"}`,
              action: { label: "Open session", onSelect: () => onOpenSession(openSessionId) },
            });
          }
        }
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
        onScheduled();
      }
    } catch (error) {
      settle?.(false);
      if (isAuthError(error)) {
        handleAuthFailure();
        return;
      }
      const message = error instanceof Error ? error.message : "failed to send input";
      if (!mounted.current) {
        // The person has left this session: name where the message was going.
        const opened = error instanceof FollowUpError ? error.sessionId : undefined;
        toast({
          message: `Not sent to ${opened ? "the new session" : (session?.title ?? "the session")}: ${message}`,
          tone: "error",
          action: opened ? { label: "Open session", onSelect: () => onOpenSession(opened) } : undefined,
        });
        return;
      }
      const current = latest.current.draft;
      const after = draftAfterFailedSend({
        immediate,
        sent: payload.text,
        current,
        before: origin.draft,
        kept: origin.rest,
      });
      if (after.draft !== current) setDraft(after.draft);
      setFailure({
        message,
        status: error instanceof ApiError || error instanceof FollowUpError ? error.status : undefined,
        openSessionId: error instanceof FollowUpError ? error.sessionId : undefined,
        payload,
        draft: after.draft,
        ids: latest.current.ids,
        rest: after.rest,
      });
    } finally {
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
        reportFailure(error, "failed to interrupt");
      } finally {
        setStopping(false);
      }
    },
  };
}
