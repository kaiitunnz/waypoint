"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchBackendModels } from "../api";
import type { BackendModelOption, ScheduledSession } from "../types";

// An agent's model list depends on the launch target and account profile.
const modelsKey = (schedule: ScheduledSession) =>
  `${schedule.backend}::${schedule.launch_target_id ?? "local"}::${schedule.account_profile_id ?? "default"}`;

// The display name of a scheduled launch's model: its id until the list it
// belongs to has loaded, or null when it pins none.
export function useScheduleModels(
  host: string,
  token: string,
  schedules: ScheduledSession[],
): (schedule: ScheduledSession) => string | null {
  const [models, setModels] = useState<Record<string, BackendModelOption[]>>({});
  const requested = useRef(new Set<string>());

  useEffect(() => {
    if (!host || !token) return;
    for (const schedule of schedules) {
      const key = modelsKey(schedule);
      if (!schedule.model || requested.current.has(key)) continue;
      requested.current.add(key);
      fetchBackendModels(host, token, schedule.backend, {
        launchTargetId: schedule.launch_target_id ?? null,
        accountProfileId: schedule.account_profile_id ?? null,
      })
        .then((response) => setModels((current) => ({ ...current, [key]: response.models })))
        // A later change to the schedules tries again.
        .catch(() => requested.current.delete(key));
    }
  }, [host, token, schedules]);

  return useCallback(
    (schedule: ScheduledSession) =>
      schedule.model ? (models[modelsKey(schedule)]?.find((option) => option.id === schedule.model)?.label ?? schedule.model) : null,
    [models],
  );
}
