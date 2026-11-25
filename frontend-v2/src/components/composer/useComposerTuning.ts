"use client";

import { useCallback, useState } from "react";

import { permissionModesFor, type BackendCatalog } from "@waypoint/web-core/backends";
import { useSessionModels } from "@waypoint/web-core/hooks/useSessionModels";
import { useSessionTuning } from "@waypoint/web-core/hooks/useSessionTuning";
import { useTuningDraft } from "@waypoint/web-core/hooks/useTuningDraft";
import { nextModeId, restartFields, tuningOptions, type TuningInput } from "@waypoint/web-core/session/tuning";
import type { SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";

interface Options {
  host: string;
  token: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  onSession: (session: SessionRecord) => void;
  onError: (message: string) => void;
}

// The session's mode, model and effort as the composer changes them, and the
// state of the popover and the interrupt question.
export function useComposerTuning({ host, token, session, catalog, onSession, onError }: Options) {
  // Opened by Shift+Tab, the popover leaves the focus in the field, which keeps
  // cycling.
  const [openedBy, setOpenedBy] = useState<"trigger" | "key" | null>(null);
  // The setting waiting on a yes to cut off the running turn.
  const [interrupt, setInterrupt] = useState<{ field: string; answer: (yes: boolean) => void } | null>(null);
  const models = useSessionModels(host, token, session, handleAuthFailure);
  const confirmInterrupt = useCallback(
    (field: string) => new Promise<boolean>((answer) => setInterrupt({ field, answer })),
    [],
  );
  const actions = useSessionTuning({
    host,
    token,
    session,
    catalog,
    onSession,
    onError,
    onAuthFailure: handleAuthFailure,
    confirmInterrupt,
  });
  const input: TuningInput = {
    modeOptions: session ? permissionModesFor(session.backend, catalog) : [],
    modelOptions: models.modelOptions,
    effortLevels: models.effortLevels,
    defaultModelId: models.defaultModelId,
    defaultModelLabel: models.defaultModelLabel,
    currentModel: session?.model ?? null,
    currentEffort: session?.effort ?? null,
    permissionMode: session?.permission_mode ?? null,
  };
  const restart = restartFields(session, catalog);
  const draft = useTuningDraft(input, restart, {
    mode: actions.changeMode,
    model: actions.changeModel,
    effort: actions.changeEffort,
  });

  return {
    input,
    options: tuningOptions(input),
    draft,
    busy: actions.busy,
    defaultEffort: models.defaultEffort,
    open: openedBy !== null,
    takesFocus: openedBy !== "key",
    setOpen: (open: boolean) => setOpenedBy(open ? "trigger" : null),
    // False when the agent has no modes, so the key keeps its usual meaning.
    cycleMode: () => {
      if (input.modeOptions.length === 0) return false;
      const next = actions.busy.mode ? null : nextModeId(input.modeOptions, draft.shown.mode);
      if (next !== null) {
        draft.select("mode", next);
        if (restart.mode) setOpenedBy("key");
      }
      return true;
    },
    interrupt,
    dismissInterrupt: () => {
      interrupt?.answer(false);
      setInterrupt(null);
    },
  };
}

export type ComposerTuning = ReturnType<typeof useComposerTuning>;
