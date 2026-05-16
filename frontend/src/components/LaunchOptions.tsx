"use client";

import { useState } from "react";

import { LaunchMode } from "@/lib/types";

// Inline gear icon — matches the one in SchedulePanel/LaunchPanel's pre-redesign
// Advanced disclosure so the visual language stays uniform across forms.
function GearGlyph() {
  return (
    <svg
      className="advanced-toggle-gear"
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 7.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z"
        fill="currentColor"
        opacity="0.9"
      />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M4.95.75h2.1l.3 1.2a3.75 3.75 0 0 1 .87.5l1.17-.39.75 1.3-1 .77v.87l1 .76-.75 1.3-1.17-.39a3.75 3.75 0 0 1-.87.5l-.3 1.2H4.95l-.3-1.2a3.75 3.75 0 0 1-.87-.5l-1.17.39-.75-1.3 1-.76V5.1l-1-.77.75-1.3 1.17.39a3.75 3.75 0 0 1 .87-.5l.3-1.17ZM6 4.125A1.875 1.875 0 1 0 6 7.876 1.875 1.875 0 0 0 6 4.124Z"
        fill="currentColor"
        opacity="0.55"
      />
    </svg>
  );
}

interface LaunchOptionsDetailsProps {
  mode: "new" | "resume";
  launchMode: LaunchMode;
  onLaunchModeChange: (mode: LaunchMode) => void;
  supportsCustomArgs?: boolean;
  supportsConfigOverrides?: boolean;
  customArgsText?: string;
  onCustomArgsChange?: (value: string) => void;
  configOverridesText?: string;
  onConfigOverridesChange?: (value: string) => void;
  formBusy?: boolean;
}

export function LaunchOptionsDetails({
  mode,
  launchMode,
  onLaunchModeChange,
  supportsCustomArgs,
  supportsConfigOverrides,
  customArgsText,
  onCustomArgsChange,
  configOverridesText,
  onConfigOverridesChange,
  formBusy,
}: LaunchOptionsDetailsProps) {
  const [open, setOpen] = useState(false);
  const showCustomArgs = mode === "new" && Boolean(supportsCustomArgs);
  const showConfigOverrides = mode === "new" && Boolean(supportsConfigOverrides);
  const showWarning = showCustomArgs || showConfigOverrides;

  return (
    <div className={`advanced-section${open ? " open" : ""}`}>
      <button
        type="button"
        className="advanced-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <GearGlyph />
        <span className="advanced-toggle-label">Advanced</span>
        <span className="advanced-toggle-chevron" aria-hidden="true" />
      </button>
      <div className="advanced-body">
        <div className="advanced-body-inner">
          <LaunchModeField value={launchMode} onChange={onLaunchModeChange} />
          {showCustomArgs ? (
            <label className="field advanced-args-field">
              <span>Custom CLI args</span>
              <textarea
                rows={3}
                value={customArgsText ?? ""}
                onChange={(e) => onCustomArgsChange?.(e.target.value)}
                placeholder={"One flag per line, e.g.\n--dangerously-skip-permissions"}
                disabled={formBusy}
                spellCheck={false}
                autoCapitalize="none"
                autoComplete="off"
                autoCorrect="off"
              />
            </label>
          ) : null}
          {showConfigOverrides ? (
            <label className="field advanced-args-field">
              <span>Config overrides (key=value)</span>
              <textarea
                rows={3}
                value={configOverridesText ?? ""}
                onChange={(e) => onConfigOverridesChange?.(e.target.value)}
                placeholder={"One per line, e.g.\nmodel_reasoning_effort=\"high\""}
                disabled={formBusy}
                spellCheck={false}
                autoCapitalize="none"
                autoComplete="off"
                autoCorrect="off"
              />
            </label>
          ) : null}
          {showWarning ? (
            <p className="advanced-warning">
              Passed directly to the CLI binary — use with caution.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

interface LaunchModeFieldProps {
  value: LaunchMode;
  onChange: (mode: LaunchMode) => void;
}

// Reusable launch-mode segmented control. Compact (content-width, not row-
// wide) so it fits naturally inside Advanced sections in both LaunchPanel
// and SchedulePanel.
export function LaunchModeField({ value, onChange }: LaunchModeFieldProps) {
  return (
    <label className="field launch-mode-field">
      <span>Launch mode</span>
      <div
        className="segmented segmented-quiet launch-mode-segmented"
        role="radiogroup"
        aria-label="Launch mode"
      >
        {(
          [
            ["auto", "Auto"],
            ["direct", "Direct"],
            ["tmux_wrapper", "tmux"],
          ] as const
        ).map(([opt, label]) => (
          <button
            key={opt}
            type="button"
            role="radio"
            aria-checked={value === opt}
            className={`segmented-item ${value === opt ? "active" : ""}`}
            onClick={() => onChange(opt)}
          >
            {label}
          </button>
        ))}
      </div>
    </label>
  );
}
