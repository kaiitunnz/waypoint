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
function SpecSummary({ spec }: { spec: SessionPresetSummary["spec"] }) {
  const chips: string[] = [];
  if (spec.model) chips.push(spec.model);
  if (spec.effort) chips.push(spec.effort);
  if (spec.permission_mode) chips.push(spec.permission_mode);
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

// Build a spec snapshot of the current launch form — the payload a save captures.
function formSpec(
  form: LaunchForm,
  launchTargetId: string | null,
): SessionPresetSpec {
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

interface PresetSelectProps {
  presets: SessionPresetSummary[];
  selectedPresetId: string | null;
  onSelectPreset: (id: string | null) => void;
  supportedBackends: Backend[];
  deletePreset: (presetId: string) => Promise<void>;
}

// Top-of-panel selector: pick a saved launch profile and hydrate the form. The
// default carries the dog-ear fold (default is a pin), the spec rides an
// owner-hue badge + mono summary, and Delete manages the current selection.
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
      {selected ? (
        <div className="preset-bar-detail">
          <SpecSummary spec={selected.spec} />
        </div>
      ) : (
        <p className="preset-bar-hint">
          Reuse a launch profile, or configure the form and save it as one below.
        </p>
      )}
      {backendUnsupported ? (
        <p className="error preset-bar-error" role="alert">
          This preset&apos;s backend ({humaniseBackend(presetBackend as Backend)})
          isn&apos;t available on the current launch target — change the backend or
          target before launching.
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
  ) => Promise<void>;
  setDefaultPreset: (presetId: string) => Promise<void>;
}

// Bottom-of-panel capture actions: these snapshot the fully-configured form, so
// they live next to Launch rather than with the selector. Save opens the sheet;
// Set-default marks the selected preset (or, with none selected, saves the
// current form as a new default).
export function PresetSaveActions({
  form,
  presets,
  selectedPresetId,
  launchTargetId,
  savePreset,
  setDefaultPreset,
}: PresetSaveActionsProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [seedDefault, setSeedDefault] = useState(false);

  const selected = selectedOf(presets, selectedPresetId);
  const isDefault = selected?.is_default ?? false;

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
      <span className="preset-save-label">Save this configuration</span>
      <div className="preset-save-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => {
            setSeedDefault(false);
            setSaveOpen(true);
          }}
        >
          {selected ? "Save preset…" : "Save as preset…"}
        </button>
        <button
          type="button"
          className={`secondary${isDefault ? " preset-default-on" : ""}`}
          disabled={busy || isDefault}
          title={isDefault ? "This preset is the default" : undefined}
          onClick={() => void handleSetDefault()}
        >
          {isDefault ? "✓ Default" : "Set as default"}
        </button>
      </div>
      {error ? (
        <p className="error preset-save-error" role="alert">
          {error}
        </p>
      ) : null}
      {saveOpen ? (
        <PresetSaveModal
          selected={selected}
          seedDefault={seedDefault}
          spec={formSpec(form, launchTargetId)}
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
        />
      ) : null}
    </div>
  );
}

interface PresetSaveModalProps {
  selected: SessionPresetSummary | null;
  seedDefault: boolean;
  spec: SessionPresetSpec;
  onClose: () => void;
  onSave: (
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ) => Promise<void>;
}

// Portaled save sheet, mirroring ScheduleMessageModal's conventions: rendered to
// document.body, Escape to close, focus trap, focus restored on unmount, and a
// body-scroll lock. Leads with a captures readout so the user sees exactly what
// the preset will pin.
function PresetSaveModal({
  selected,
  seedDefault,
  spec,
  onClose,
  onSave,
}: PresetSaveModalProps) {
  const [name, setName] = useState(selected?.name ?? "");
  const [description, setDescription] = useState(selected?.description ?? "");
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
    inputRef.current?.select();
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

  function submit(presetId: string | null) {
    if (presetId === null && !name.trim()) return;
    onSave(
      {
        name: name.trim() || undefined,
        description: description.trim() || null,
        spec,
        ...(presetId === null ? { is_default: asDefault } : {}),
      },
      presetId,
    ).catch(() => {
      /* surfaced by the actions bar */
    });
  }

  function onSubmitForm(event: FormEvent) {
    event.preventDefault();
    submit(null);
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
        aria-label={selected ? "Save preset" : "New preset"}
      >
        <div className="preset-modal-header">
          <span className="preset-modal-title">
            <span className="preset-modal-glyph" aria-hidden="true">
              ƒ
            </span>
            {selected ? "Save preset" : "New preset"}
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
          {!selected ? (
            <label className="preset-modal-check">
              <input
                type="checkbox"
                checked={asDefault}
                onChange={(event) => setAsDefault(event.target.checked)}
              />
              <span>Make this the default preset</span>
            </label>
          ) : null}
          <div className="preset-modal-actions">
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            {selected ? (
              <>
                <button
                  type="button"
                  className="secondary"
                  disabled={!name.trim()}
                  onClick={() => submit(selected.id)}
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
