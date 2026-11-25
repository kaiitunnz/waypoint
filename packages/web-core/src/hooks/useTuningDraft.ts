"use client";

import { useState } from "react";

import {
  NO_STAGED_PICKS,
  tuningDraft,
  type RestartFields,
  type StagedPicks,
  type TuningDraft,
  type TuningField,
  type TuningInput,
} from "../session/tuning";

// `confirmed` marks a change the restart prompt already confirmed.
type Change = (value: string, confirmed?: boolean) => void | Promise<void>;

export interface TuningDraftControls extends TuningDraft {
  select: (field: TuningField, value: string) => void;
  // Applies the staged picks one after another.
  applyRestart: () => Promise<void>;
}

export function useTuningDraft(
  input: TuningInput,
  restart: RestartFields,
  changes: Record<TuningField, Change>,
): TuningDraftControls {
  const [staged, setStaged] = useState<StagedPicks>(NO_STAGED_PICKS);
  const draft = tuningDraft(input, staged, restart);

  return {
    ...draft,
    select: (field, value) => {
      if (!restart[field]) {
        void changes[field](value);
        return;
      }
      const current = {
        mode: input.permissionMode ?? "default",
        model: input.currentModel ?? "",
        effort: input.currentEffort ?? "",
      }[field];
      setStaged((picks) => ({ ...picks, [field]: value === current ? null : value }));
    },
    applyRestart: async () => {
      const { mode, model, effort } = draft.pending;
      setStaged(NO_STAGED_PICKS);
      if (mode !== null) await changes.mode(mode, true);
      if (model !== null) await changes.model(model, true);
      if (effort !== null) await changes.effort(effort, true);
    },
  };
}
