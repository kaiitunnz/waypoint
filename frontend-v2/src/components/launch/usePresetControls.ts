"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import type { LaunchContext } from "@waypoint/web-core/hooks/useLaunchContext";
import type { LaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { loadPresetSpec, presetNeedsFullSpec } from "@waypoint/web-core/launch/presets";
import type { SessionPresetSummary, SessionPresetWriteRequest } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

export interface PresetControls {
  presets: SessionPresetSummary[];
  defaultPresetId: string | null;
  selectedId: string | null;
  selected: SessionPresetSummary | null;
  busy: boolean;
  select: (id: string | null) => void;
  // Overwrites a preset's settings with the form's; its name and description stay.
  update: (preset: SessionPresetSummary) => void;
  setDefault: (preset: SessionPresetSummary) => void;
  askDelete: (preset: SessionPresetSummary) => void;
  pendingDelete: SessionPresetSummary | null;
  confirmDelete: () => Promise<void>;
  cancelDelete: () => void;
  saving: boolean;
  openSave: () => void;
  closeSave: () => void;
  save: (payload: SessionPresetWriteRequest) => Promise<void>;
}

export function usePresetControls(
  launch: LaunchContext,
  form: LaunchForm,
  initialId: string | null,
  launchTargetId: string,
): PresetControls {
  const { toast } = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(initialId);
  // Another target clears the agent's settings, so the form no longer holds the preset.
  const [presetTarget, setPresetTarget] = useState(launchTargetId);
  if (presetTarget !== launchTargetId) {
    setPresetTarget(launchTargetId);
    setSelectedId(null);
  }
  // Only the latest choice on the current target fills the form; a slower fetch
  // for an earlier one is dropped.
  const latest = useRef(0);
  const currentTarget = useRef(launchTargetId);
  useEffect(() => {
    currentTarget.current = launchTargetId;
  });
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<SessionPresetSummary | null>(null);
  const [saving, setSaving] = useState(false);
  const { presets } = launch;

  function fail(error: unknown, fallback: string) {
    if (isAuthError(error)) {
      handleAuthFailure();
      return;
    }
    toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
  }

  async function apply(id: string, request: number, target: string) {
    const summary = presets.find((preset) => preset.id === id);
    if (!summary) return;
    const current = () => request === latest.current && target === currentTarget.current;
    try {
      const spec = await loadPresetSpec(summary, launch.fetchPreset);
      if (current()) form.applyPreset(spec);
    } catch (error) {
      if (!current()) return;
      // Half-filled, an Update would overwrite the preset's variables with none.
      setSelectedId(null);
      fail(error, "failed to load preset");
    }
  }

  // A preset with variables opens once their values arrive.
  const applyInitial = useEffectEvent(() => {
    const summary = presets.find((preset) => preset.id === initialId);
    if (summary && presetNeedsFullSpec(summary)) void apply(summary.id, latest.current, launchTargetId);
  });
  useEffect(() => {
    applyInitial();
  }, []);

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      fail(error, fallback);
    } finally {
      setBusy(false);
    }
  }

  return {
    presets,
    defaultPresetId: launch.defaultPresetId,
    selectedId,
    selected: presets.find((preset) => preset.id === selectedId) ?? null,
    busy,
    select: (id) => {
      const request = ++latest.current;
      setSelectedId(id);
      if (id) void apply(id, request, launchTargetId);
    },
    update: (preset) =>
      void run(async () => {
        await launch.savePreset({ spec: form.presetSpec() }, preset.id);
        toast({ message: `Updated ${preset.name}` });
      }, "failed to update preset"),
    setDefault: (preset) =>
      void run(async () => {
        await launch.setDefaultPreset(preset.id);
        toast({ message: `${preset.name} is the default` });
      }, "failed to set the default"),
    askDelete: setPendingDelete,
    pendingDelete,
    confirmDelete: async () => {
      const preset = pendingDelete;
      if (!preset) return;
      await run(async () => {
        await launch.deletePreset(preset.id);
        if (selectedId === preset.id) setSelectedId(null);
        toast({ message: `Deleted ${preset.name}` });
      }, "failed to delete preset");
    },
    cancelDelete: () => setPendingDelete(null),
    saving,
    openSave: () => setSaving(true),
    closeSave: () => setSaving(false),
    save: (payload) =>
      run(async () => {
        const created = await launch.savePreset(payload, null);
        // The form already holds the new preset's settings, so it is selected as is.
        if (created) setSelectedId(created);
        setSaving(false);
        toast({ message: `Saved ${payload.name ?? "preset"}` });
      }, "failed to save preset"),
  };
}
