"use client";

import { useEffect, useState } from "react";

import { fetchBackendModels, isAuthError } from "../api";
import type { BackendModelOption, SessionRecord } from "../types";

export interface SessionModels {
  modelOptions: BackendModelOption[];
  effortLevels: string[];
  defaultModelId: string | null;
  defaultModelLabel: string | null;
  defaultEffort: string | null;
}

const NO_MODELS: SessionModels = {
  modelOptions: [],
  effortLevels: [],
  defaultModelId: null,
  defaultModelLabel: null,
  defaultEffort: null,
};

// `onAuthFailure` must be stable.
export function useSessionModels(
  host: string,
  token: string,
  session: SessionRecord | null,
  onAuthFailure: () => void,
): SessionModels {
  const [models, setModels] = useState<SessionModels>(NO_MODELS);
  // Only the fields the request reads: the record itself changes on every frame.
  const backend = session?.backend;
  const launchTargetId = session?.launch_target_id;
  const accountProfileId = session?.account_profile_id ?? null;

  useEffect(() => {
    if (!backend) return;
    let cancelled = false;
    fetchBackendModels(host, token, backend, { launchTargetId, accountProfileId })
      .then((response) => {
        if (cancelled) return;
        setModels({
          modelOptions: response.models,
          effortLevels: response.effort_levels ?? [],
          defaultModelId: response.default_model_id ?? null,
          defaultModelLabel: response.default_model_label ?? null,
          defaultEffort: response.default_effort ?? null,
        });
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthError(error)) {
          onAuthFailure();
          return;
        }
        // Discovery failure is not fatal: the picker shows the session's own model.
        setModels(NO_MODELS);
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, backend, launchTargetId, accountProfileId, onAuthFailure]);

  return models;
}
