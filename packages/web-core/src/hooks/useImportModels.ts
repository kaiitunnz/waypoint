"use client";

import { useEffect, useState } from "react";

import { fetchBackendModels } from "../api";
import type { Backend, BackendModelOption } from "../types";

export interface ImportModels {
  options: BackendModelOption[];
  selected: string | null;
  setSelected: (id: string | null) => void;
}

// The models an imported thread can resume with, preselecting the agent's
// default. Empty while `enabled` is false.
export function useImportModels(
  host: string,
  token: string,
  agent: Backend,
  launchTargetId: string | null,
  accountProfileId: string | null,
  enabled: boolean,
): ImportModels {
  const [options, setOptions] = useState<BackendModelOption[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !token) {
      setOptions([]);
      setSelected(null);
      return;
    }
    let cancelled = false;
    fetchBackendModels(host, token, agent, { launchTargetId, accountProfileId })
      .then((response) => {
        if (cancelled) return;
        setOptions(response.models);
        setSelected(
          response.default_model_id ??
            response.models.find((option) => option.is_default)?.id ??
            response.models[0]?.id ??
            null,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setOptions([]);
          setSelected(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, host, token, agent, launchTargetId, accountProfileId]);

  return { options, selected, setSelected };
}
