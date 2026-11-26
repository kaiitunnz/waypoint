"use client";

import { useState } from "react";

import type { Backend, BackendModelListResponse, BackendModelOption } from "../types";
import { useLaunchModels } from "./useLaunchModels";

export interface ImportModels {
  options: BackendModelOption[];
  selected: string | null;
  setSelected: (id: string | null) => void;
}

const NO_MODELS: BackendModelOption[] = [];

const preselected = (response: BackendModelListResponse | null) =>
  response?.default_model_id ?? response?.models.find((option) => option.is_default)?.id ?? response?.models[0]?.id ?? null;

// The models an imported thread can resume with, preselecting the agent's
// default. Empty while `enabled` is false. `onAuthFailure` must be stable.
export function useImportModels(
  host: string,
  token: string,
  agent: Backend,
  launchTargetId: string | null,
  accountProfileId: string | null,
  enabled: boolean,
  onAuthFailure: () => void,
): ImportModels {
  const response = useLaunchModels(host, token, agent, launchTargetId, accountProfileId, onAuthFailure, enabled);
  const [chosen, setChosen] = useState<{ response: BackendModelListResponse | null; id: string | null }>({
    response: null,
    id: null,
  });
  const selected = chosen.response === response ? chosen.id : preselected(response);
  return {
    options: response?.models ?? NO_MODELS,
    selected,
    setSelected: (id) => setChosen({ response, id }),
  };
}
