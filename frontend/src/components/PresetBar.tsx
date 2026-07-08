"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { LaunchForm } from "@/components/LaunchFormFields";
import { humaniseBackend } from "@/lib/backends";
import type {
  Backend,
  SessionPresetSpec,
  SessionPresetSummary,
  SessionPresetWriteRequest,
} from "@/lib/types";

// A compact key=value summary of what a preset spec pins, in the badge/mono
// vocabulary: the backend rides an owner-hue badge, the rest are muted chips.
// Used only in the save sheet's captures readout.
function SpecSummary({ spec }: { spec: SessionPresetSummary["spec"] }) {
  const chips: string[] = [];
  if (spec.model) chips.push(spec.model);
  if (spec.effort) chips.push(spec.effort);
  if (spec.permission_mode) chips.push(spec.permission_mode);
  if (spec.account_profile_id) chips.push(spec.account_profile_id);
  const envCount = spec.launch_env_keys?.length ?? 0;
  if (envCount) chips.push(`${envCount} env`);
  const argsCount = spec.args?.length ?? 0;
  if (argsCount) chips.push(`${argsCount} arg${argsCount > 1 ? "s" : ""}`);
  return (
    <span className="preset-summary">
      {spec.backend ? (
        <span className={`badge ${spec.backend}`}>
          {humaniseBackend(spec.backend as Backend)}
        </span>
      ) : null}
      {chips.map((chip, i) => (
        <span key={`${i}-${chip}`} className="preset-summary-chip">
          {i > 0 || spec.backend ? <span className="preset-summary-dot" /> : null}
          {chip}
        </span>
      ))}
    </span>
  );
}

function selectedOf(
  presets: SessionPresetSummary[],
  id: string | null,
): SessionPresetSummary | null {
  return presets.find((p) => p.id === id) ?? null;
}

// A spec snapshot of the current launch form — the payload a save/update
// captures. Excludes cwd and title: those are per-launch, not preset defaults.
function formSpec(
  form: LaunchForm,
  launchTargetId: string | null,
): SessionPresetSpec {
  const { args, configOverrides, launchEnv } = form.collectArgs();
  return {
    backend: form.backend,
    launch_target_id: launchTargetId,
    transport: form.transport || null,
    model: form.model.trim() || null,
    effort: form.effortSupported ? form.effort.trim() || null : null,
    permission_mode: form.permissionMode || null,
    account_profile_id: form.accountProfileId || null,
    args,
    config_overrides: configOverrides,
    launch_env: launchEnv,
  };
}

interface PresetSelectProps {
  presets: SessionPresetSummary[];
  selectedPresetId: string | null;
  onSelectPreset: (id: string | null) => void;
  supportedBackends: Backend[];
  deletePreset: (presetId: string) => Promise<void>;
}

// Top-of-panel selector: pick a saved launch profile and hydrate the form.
// Minimal by design — just the picker, a Delete affordance for the current
// selection, and the dog-ear fold marking the default (default is a pin).
export function PresetSelect({
  presets,
  selectedPresetId,
  onSelectPreset,
  supportedBackends,
  deletePreset,
}: PresetSelectProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = selectedOf(presets, selectedPresetId);
  const isDefault = selected?.is_default ?? false;
  const presetBackend = selected?.spec.backend ?? null;
  const backendUnsupported =
    !!presetBackend && !supportedBackends.includes(presetBackend as Backend);

  async function handleDelete(): Promise<void> {
    if (!selected) return;
    const message = selected.is_default
      ? `Delete the default preset "${selected.name}"? There will be no default afterwards. This cannot be undone.`
      : `Delete the preset "${selected.name}"? This cannot be undone.`;
    if (!window.confirm(message)) return;
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
    <div className={`preset-bar${isDefault ? " is-default" : ""}`}>
      <div className="preset-bar-row">
        <span className="preset-bar-label">Preset</span>
        <div className="preset-bar-picker">
          <select
            className="preset-select"
            value={selectedPresetId ?? ""}
            disabled={busy}
            aria-label="Session preset"
            onChange={(event) => onSelectPreset(event.target.value || null)}
          >
            <option value="">No preset</option>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.name}
                {preset.is_default ? "  ·  default" : ""}
              </option>
            ))}
          </select>
        </div>
        {selected ? (
          <button
            type="button"
            className="link-button action-chip danger-link"
            disabled={busy}
            onClick={() => void handleDelete()}
          >
            Delete
          </button>
        ) : null}
      </div>
      {backendUnsupported ? (
        <p className="error preset-bar-error" role="alert">
          This preset&apos;s backend ({humaniseBackend(presetBackend as Backend)})
          isn&apos;t available here — change the backend or launch target.
        </p>
      ) : null}
      {error ? (
        <p className="error preset-bar-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface PresetSaveActionsProps {
  form: LaunchForm;
  presets: SessionPresetSummary[];
  selectedPresetId: string | null;
  launchTargetId: string | null;
  savePreset: (
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ) => Promise<string | null>;
  setDefaultPreset: (presetId: string) => Promise<void>;
  onSelectPreset: (id: string | null) => void;
}

// Bottom-of-panel capture actions: these snapshot the fully-configured form, so
// they sit next to Launch. Update overwrites the selected preset in place; Save
// as new opens the create sheet; Set default marks the selection (or, with none
// selected, saves the current form as a new default).
export function PresetSaveActions({
  form,
  presets,
  selectedPresetId,
  launchTargetId,
  savePreset,
  setDefaultPreset,
  onSelectPreset,
}: PresetSaveActionsProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [seedDefault, setSeedDefault] = useState(false);
  const [flashed, setFlashed] = useState(false);

  const selected = selectedOf(presets, selectedPresetId);
  const isDefault = selected?.is_default ?? false;

  useEffect(() => {
    if (!flashed) return;
    const t = window.setTimeout(() => setFlashed(false), 1600);
    return () => window.clearTimeout(t);
  }, [flashed]);

  async function handleUpdate(): Promise<void> {
    if (!selected) return;
    setError(null);
    setBusy(true);
    try {
      // PATCH the spec only; name/description (and tags) are preserved server-side.
      await savePreset({ spec: formSpec(form, launchTargetId) }, selected.id);
      setFlashed(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to update preset");
    } finally {
      setBusy(false);
    }
  }

  async function handleSetDefault(): Promise<void> {
    setError(null);
    if (!selectedPresetId) {
      setSeedDefault(true);
      setSaveOpen(true);
      return;
    }
    if (isDefault) return;
    setBusy(true);
    try {
      await setDefaultPreset(selectedPresetId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to set default");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="preset-save">
      <span className="preset-save-label">Preset</span>
      <div className="preset-save-actions">
        {selected ? (
          <>
            <button
              type="button"
              className={`preset-act${flashed ? " preset-flash" : ""}`}
              disabled={busy}
              onClick={() => void handleUpdate()}
            >
              {flashed ? "Updated ✓" : "Update"}
            </button>
            <button
              type="button"
              className="preset-act"
              disabled={busy}
              onClick={() => {
                setSeedDefault(false);
                setSaveOpen(true);
              }}
            >
              Save as new…
            </button>
          </>
        ) : (
          <button
            type="button"
            className="preset-act"
            disabled={busy}
            onClick={() => {
              setSeedDefault(false);
              setSaveOpen(true);
            }}
          >
            Save as preset…
          </button>
        )}
        <button
          type="button"
          className={`preset-act${isDefault ? " preset-default-on" : ""}`}
          disabled={busy || isDefault}
          title={isDefault ? "This preset is the default" : undefined}
          onClick={() => void handleSetDefault()}
        >
          {isDefault ? "✓ Default" : "Set default"}
        </button>
      </div>
      {error ? (
        <p className="error preset-save-error" role="alert">
          {error}
        </p>
      ) : null}
      {saveOpen ? (
        <PresetSaveModal
          seedDefault={seedDefault}
          spec={formSpec(form, launchTargetId)}
          onClose={() => setSaveOpen(false)}
          onSave={async (payload) => {
            setBusy(true);
            setError(null);
            try {
              // Select the freshly created preset. The form already holds its
              // spec, so switch the selection without re-hydrating.
              const createdId = await savePreset(payload, null);
              if (createdId) onSelectPreset(createdId);
              setSaveOpen(false);
            } catch (err) {
              setError(err instanceof Error ? err.message : "failed to save preset");
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}
    </div>
  );
}

interface PresetSaveModalProps {
  seedDefault: boolean;
  spec: SessionPresetSpec;
  onClose: () => void;
  onSave: (payload: SessionPresetWriteRequest) => Promise<void>;
}

// Portaled create sheet, mirroring ScheduleMessageModal's conventions: rendered
// to document.body, Escape to close, focus trap, focus restored on unmount, and
// a body-scroll lock. Leads with a captures readout so the user sees exactly
// what the new preset will pin.
function PresetSaveModal({
  seedDefault,
  spec,
  onClose,
  onSave,
}: PresetSaveModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [asDefault, setAsDefault] = useState(seedDefault);
  const modalRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const summarySpec: SessionPresetSummary["spec"] = {
    backend: spec.backend,
    model: spec.model,
    effort: spec.effort,
    permission_mode: spec.permission_mode,
    launch_env_keys: Object.keys(spec.launch_env ?? {}),
    args: spec.args,
  };

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, []);

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const root = modalRef.current;
      if (!root) return;
      const focusable = root.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !root.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function onSubmitForm(event: FormEvent) {
    event.preventDefault();
    // This dialog is portaled to <body> but instantiated inside the launch/
    // schedule form; React bubbles synthetic events through the component tree,
    // so without this the save would also fire the enclosing form's submit and
    // launch a session.
    event.stopPropagation();
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      description: description.trim() || null,
      spec,
      is_default: asDefault,
    }).catch(() => {
      /* surfaced by the actions bar */
    });
  }

  return createPortal(
    <div
      className="preset-modal-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={modalRef}
        className="preset-modal"
        role="dialog"
        aria-modal="true"
        aria-label="New preset"
      >
        <div className="preset-modal-header">
          <span className="preset-modal-title">
            <span className="preset-modal-glyph" aria-hidden="true">
              ƒ
            </span>
            New preset
          </span>
          <button
            type="button"
            className="preset-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <form onSubmit={onSubmitForm}>
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
          <div className="preset-modal-captures">
            <span className="preset-modal-captures-label">Captures</span>
            <SpecSummary spec={summarySpec} />
          </div>
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
            <button type="submit" className="primary" disabled={!name.trim()}>
              Create preset
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
