"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { LaunchForm } from "@/components/LaunchFormFields";
import type {
  Backend,
  SessionPresetSpec,
  SessionPresetSummary,
  SessionPresetWriteRequest,
} from "@/lib/types";

interface PresetBarProps {
  form: LaunchForm;
  presets: SessionPresetSummary[];
  selectedPresetId: string | null;
  // Controlled by the panel so selection + hydration survive mode switches.
  onSelectPreset: (id: string | null) => void;
  supportedBackends: Backend[];
  launchTargetId: string | null;
  savePreset: (
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ) => Promise<void>;
  setDefaultPreset: (presetId: string) => Promise<void>;
  deletePreset: (presetId: string) => Promise<void>;
}

// Flat, in-flow preset control that sits above the shared launch form: a
// selector that hydrates the form from a preset, plus save / set-default /
// delete actions. Only the save dialog floats (glass modal); the bar itself is
// flat token chrome to match the launch card.
export function PresetBar({
  form,
  presets,
  selectedPresetId,
  onSelectPreset,
  supportedBackends,
  launchTargetId,
  savePreset,
  setDefaultPreset,
  deletePreset,
}: PresetBarProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);

  const selected = presets.find((p) => p.id === selectedPresetId) ?? null;
  const presetBackend = selected?.spec.backend ?? null;
  const backendUnsupported =
    !!presetBackend && !supportedBackends.includes(presetBackend as Backend);

  function currentSpec(): SessionPresetSpec {
    const { args, configOverrides, launchEnv } = form.collectArgs();
    return {
      backend: form.backend,
      cwd: form.cwd || null,
      launch_target_id: launchTargetId,
      transport: form.transport || null,
      title: form.title.trim() || null,
      model: form.model.trim() || null,
      effort: form.effortSupported ? form.effort.trim() || null : null,
      permission_mode: form.permissionMode || null,
      args,
      config_overrides: configOverrides,
      launch_env: launchEnv,
    };
  }

  async function handleSetDefault(): Promise<void> {
    setError(null);
    if (!selectedPresetId) {
      // Nothing selected — offer to save the current form as a default preset.
      setSaveOpen(true);
      return;
    }
    setBusy(true);
    try {
      await setDefaultPreset(selectedPresetId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to set default");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(): Promise<void> {
    if (!selected) return;
    if (
      selected.is_default &&
      !window.confirm(
        `Delete the default preset "${selected.name}"? There will be no default afterwards.`,
      )
    ) {
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await deletePreset(selected.id);
      onSelectPreset(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete preset");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="preset-bar">
      <label className="preset-bar-select field">
        <span>Preset</span>
        <select
          value={selectedPresetId ?? ""}
          disabled={busy}
          onChange={(event) => onSelectPreset(event.target.value || null)}
        >
          <option value="">No preset</option>
          {presets.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
              {preset.is_default ? " · Default" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="preset-bar-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => setSaveOpen(true)}
        >
          Save…
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void handleSetDefault()}
        >
          {selected?.is_default ? "Default ✓" : "Set default"}
        </button>
        <button
          type="button"
          className="danger"
          disabled={busy || !selected}
          onClick={() => void handleDelete()}
        >
          Delete
        </button>
      </div>
      {backendUnsupported ? (
        <p className="error preset-bar-error">
          This preset&apos;s backend ({presetBackend}) isn&apos;t available on the
          current launch target. Change the backend or target before launching.
        </p>
      ) : null}
      {error ? <p className="error preset-bar-error">{error}</p> : null}
      {saveOpen ? (
        <PresetSaveModal
          selected={selected}
          onClose={() => setSaveOpen(false)}
          onSave={async (payload, presetId) => {
            setBusy(true);
            setError(null);
            try {
              await savePreset(payload, presetId);
              setSaveOpen(false);
            } catch (err) {
              setError(err instanceof Error ? err.message : "failed to save preset");
            } finally {
              setBusy(false);
            }
          }}
          currentSpec={currentSpec}
        />
      ) : null}
    </div>
  );
}

interface PresetSaveModalProps {
  selected: SessionPresetSummary | null;
  onClose: () => void;
  onSave: (
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ) => Promise<void>;
  currentSpec: () => SessionPresetSpec;
}

function PresetSaveModal({
  selected,
  onClose,
  onSave,
  currentSpec,
}: PresetSaveModalProps) {
  const [name, setName] = useState(selected?.name ?? "");
  const [description, setDescription] = useState(selected?.description ?? "");
  const [asDefault, setAsDefault] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function submitNew(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    void onSave(
      {
        name: name.trim(),
        description: description.trim() || null,
        spec: currentSpec(),
        is_default: asDefault,
      },
      null,
    );
  }

  function submitUpdate() {
    if (!selected) return;
    void onSave(
      {
        name: name.trim() || undefined,
        description: description.trim() || null,
        spec: currentSpec(),
      },
      selected.id,
    );
  }

  return createPortal(
    <div
      className="preset-modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="preset-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Save preset"
      >
        <form onSubmit={submitNew}>
          <h3>{selected ? "Save preset" : "New preset"}</h3>
          <label className="field">
            <span>Name</span>
            <input
              ref={inputRef}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Claude worker"
            />
          </label>
          <label className="field">
            <span>Description</span>
            <input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional"
            />
          </label>
          <label className="preset-modal-check">
            <input
              type="checkbox"
              checked={asDefault}
              onChange={(event) => setAsDefault(event.target.checked)}
            />
            <span>Make this the default preset</span>
          </label>
          <div className="preset-modal-actions">
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            {selected ? (
              <>
                <button
                  type="button"
                  className="secondary"
                  onClick={submitUpdate}
                  disabled={!!selected && !name.trim()}
                >
                  Update &quot;{selected.name}&quot;
                </button>
                <button type="submit" className="primary" disabled={!name.trim()}>
                  Save as new
                </button>
              </>
            ) : (
              <button type="submit" className="primary" disabled={!name.trim()}>
                Create preset
              </button>
            )}
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
