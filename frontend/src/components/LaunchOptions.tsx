"use client";

import { useState } from "react";

import type { BackendCatalog } from "@/lib/backends";
import { transportPresentation } from "@/lib/backends";
import { LaunchMode, SessionTransport } from "@/lib/types";

export function GearGlyph() {
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
  availableModes?: LaunchMode[];
  // The agent-primary launch flow drives the transport with TransportPicker, so
  // it hides the legacy launch-mode selector here. Resume / schedule keep it.
  showLaunchMode?: boolean;
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
  availableModes,
  showLaunchMode = true,
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

  // Nothing to configure — don't render an empty Advanced toggle.
  if (!showLaunchMode && !showCustomArgs && !showConfigOverrides) {
    return null;
  }

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
          {showLaunchMode ? (
            <LaunchModeField
              value={launchMode}
              onChange={onLaunchModeChange}
              availableModes={availableModes}
            />
          ) : null}
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
  // When provided, only these transport options are offered (capability-gated
  // per agent). Defaults to all three.
  availableModes?: LaunchMode[];
}

// The launch mode is really a transport / fidelity choice: "direct" runs the
// agent's native structured adapter, "tmux_wrapper" a generic terminal pane
// (heuristic transcript), and "auto" lets the backend pick.
const LAUNCH_MODE_OPTIONS: Array<[LaunchMode, string, string]> = [
  ["auto", "Auto", "Let Waypoint pick the best transport for this agent"],
  ["direct", "Direct", "Native structured adapter — full-fidelity transcript"],
  [
    "tmux_wrapper",
    "tmux",
    "Generic tmux pane — live terminal, heuristic transcript",
  ],
];

// Reusable transport selector. Compact (content-width, not row-wide) so it
// fits naturally inside Advanced sections in both LaunchPanel and SchedulePanel.
export function LaunchModeField({
  value,
  onChange,
  availableModes,
}: LaunchModeFieldProps) {
  const options = availableModes
    ? LAUNCH_MODE_OPTIONS.filter(([opt]) => availableModes.includes(opt))
    : LAUNCH_MODE_OPTIONS;
  return (
    <label className="field launch-mode-field">
      <span>Transport</span>
      <div
        className="segmented segmented-quiet launch-mode-segmented"
        role="radiogroup"
        aria-label="Transport"
      >
        {options.map(([opt, label, hint]) => (
          <button
            key={opt}
            type="button"
            role="radio"
            aria-checked={value === opt}
            title={hint}
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

interface TransportPickerProps {
  transports: SessionTransport[];
  value: SessionTransport;
  onChange: (transport: SessionTransport) => void;
  catalog: BackendCatalog;
}

// Agent-primary transport selector. Populated from an agent's
// supported_transports and rendered as a light segmented control: each
// transport gets a distinct icon and its user-facing name, with the selected
// transport's one-line description shown beneath. Collapses to nothing when the
// agent exposes a single transport, since there is nothing to choose.
export function TransportPicker({
  transports,
  value,
  onChange,
  catalog,
}: TransportPickerProps) {
  if (transports.length <= 1) {
    return null;
  }
  const selected = transportPresentation(value, catalog);
  return (
    <div className="field transport-field">
      <span>Interface</span>
      <div
        className="segmented segmented-quiet transport-segmented"
        role="radiogroup"
        aria-label="Interface"
      >
        {transports.map((transport) => {
          const { name, description, kind } = transportPresentation(
            transport,
            catalog,
          );
          const active = value === transport;
          return (
            <button
              key={transport}
              type="button"
              role="radio"
              aria-checked={active}
              title={description}
              className={`segmented-item transport-segment${active ? " active" : ""}`}
              onClick={() => onChange(transport)}
            >
              <span className="transport-segment-icon" aria-hidden="true">
                <TransportGlyph transport={transport} kind={kind} />
              </span>
              {name}
            </button>
          );
        })}
      </div>
      <p className="transport-desc">{selected.description}</p>
    </div>
  );
}

// Distinct glyph per transport: a speech bubble for the structured Chat
// adapter, an app window for the Emulated (real-app) tail, and a terminal
// prompt for the raw Terminal pane. Falls back to the kind for unknown
// transports.
function TransportGlyph({
  transport,
  kind,
}: {
  transport: SessionTransport;
  kind: "chat" | "terminal";
}) {
  if (transport === "claude_tty") return <EmulatedGlyph />;
  if (kind === "terminal") return <TerminalGlyph />;
  return <ChatGlyph />;
}

function ChatGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 2.9h10A1.3 1.3 0 0 1 14.3 4.2v4.9A1.3 1.3 0 0 1 13 10.4H6.7L4 12.8V10.4H3A1.3 1.3 0 0 1 1.7 9.1V4.2A1.3 1.3 0 0 1 3 2.9Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M4.3 5.4h7.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
      <path d="M4.3 7.5h4.8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
    </svg>
  );
}

function EmulatedGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.7"
        y="2.7"
        width="12.6"
        height="10.6"
        rx="1.7"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M1.7 5.5h12.6" stroke="currentColor" strokeWidth="1.2" opacity="0.65" />
      <circle cx="3.8" cy="4.1" r="0.55" fill="currentColor" />
      <circle cx="5.6" cy="4.1" r="0.55" fill="currentColor" opacity="0.6" />
      <path d="M4.4 8h6.2M4.4 10.3h3.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
    </svg>
  );
}

function TerminalGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.6"
        y="2.6"
        width="12.8"
        height="10.8"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="M4.3 6.2l2 1.9-2 1.9"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M7.8 10.2h3.6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}
