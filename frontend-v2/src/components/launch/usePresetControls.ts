"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import type { LaunchContext } from "@waypoint/web-core/hooks/useLaunchContext";
import type { LaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { deletePresetMessage, presetNeedsFullSpec, specFromSummary } from "@waypoint/web-core/launch/presets";
import type { SessionPresetSpec, SessionPresetSummary, SessionPresetWriteRequest } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

// Which preset a launch opens with: the default, or a chosen one (null is none).
export type PresetSelection = { kind: "default" } | { kind: "preset"; presetId: string | null };

export interface PendingDelete {
  preset: SessionPresetSummary;
  message: string;
}

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
  pendingDelete: PendingDelete | null;
  confirmDelete: () => Promise<void>;
  cancelDelete: () => void;
  // The save form: open, with "make default" checked or not.
  saving: { asDefault: boolean } | null;
  openSave: (asDefault?: boolean) => void;
  closeSave: () => void;
  save: (payload: SessionPresetWriteRequest) => Promise<void>;
}

// The preset a launch opens with, and its spec when the form can take it at once:
// a preset with variables waits for their values.
export function openingPreset(
  launch: LaunchContext,
  initial: PresetSelection,
): { id: string | null; spec: SessionPresetSpec | null } {
  const id = initial.kind === "preset" ? initial.presetId : launch.defaultPresetId;
  const summary = launch.presets.find((preset) => preset.id === id);
  return { id: summary ? id : null, spec: summary && !presetNeedsFullSpec(summary) ? specFromSummary(summary.spec) : null };
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
  // Only the latest choice fills the form; a slower fetch for an earlier one is dropped.
  const latest = useRef(0);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [saving, setSaving] = useState<{ asDefault: boolean } | null>(null);
  const { presets } = launch;

  function fail(error: unknown, fallback: string) {
    if (isAuthError(error)) {
      handleAuthFailure();
      return;
    }
    toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
  }

  // Fills the form from a preset, fetching its variables' values first when it has any.
  async function apply(id: string, request: number) {
    const summary = presets.find((preset) => preset.id === id);
    if (!summary) return;
    try {
      const spec = presetNeedsFullSpec(summary) ? (await launch.fetchPreset(summary.id)).spec : specFromSummary(summary.spec);
      if (request === latest.current) form.applyPreset(spec);
    } catch (error) {
      if (request !== latest.current) return;
      // Half-filled, an Update would overwrite the preset's variables with none.
      setSelectedId(null);
      fail(error, "failed to load preset");
    }
  }

  // A preset with variables opens once their values arrive.
  const applyInitial = useEffectEvent(() => {
    const summary = presets.find((preset) => preset.id === initialId);
    if (!summary || !presetNeedsFullSpec(summary)) return;
    const request = latest.current;
    launch
      .fetchPreset(summary.id)
      .then((full) => {
        if (request === latest.current) form.applyPreset(full.spec);
      })
      .catch((error) => {
        if (request !== latest.current) return;
        setSelectedId(null);
        fail(error, "failed to load preset");
      });
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
      if (id) void apply(id, request);
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
    askDelete: (preset) => setPendingDelete({ preset, message: deletePresetMessage(preset) }),
    pendingDelete,
    confirmDelete: async () => {
      const preset = pendingDelete?.preset;
      if (!preset) return;
      await run(async () => {
        await launch.deletePreset(preset.id);
        if (selectedId === preset.id) setSelectedId(null);
        toast({ message: `Deleted ${preset.name}` });
      }, "failed to delete preset");
    },
    cancelDelete: () => setPendingDelete(null),
    saving,
    openSave: (asDefault = false) => setSaving({ asDefault }),
    closeSave: () => setSaving(null),
    save: async (payload) => {
      setBusy(true);
      try {
        const created = await launch.savePreset(payload, null);
        // The form already holds the new preset's settings, so it is selected as is.
        if (created) setSelectedId(created);
        setSaving(null);
        toast({ message: `Saved ${payload.name ?? "preset"}` });
      } catch (error) {
        fail(error, "failed to save preset");
      } finally {
        setBusy(false);
      }
    },
  };
}
