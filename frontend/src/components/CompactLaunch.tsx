"use client";

import type { Backend, SessionPresetSummary } from "@/lib/types";

// Compact launch action that replaces the always-open form on the homepage: a
// row of preset quick-launch chips plus a "New session" button, both of which
// open the full launch form in a glass sheet. A preset chip opens the sheet on
// the New tab with that preset preselected; Resume / Schedule are dashed chips
// that open their tab.
export type LaunchSheetMode = "new" | "resume" | "attach" | "schedule";

// Keep the quick-launch row to a scannable size; the rest live in the sheet's
// full preset picker, reached via the "+N more" overflow chip.
const MAX_PRESET_CHIPS = 3;

interface CompactLaunchProps {
  targetLabel: string;
  presets: SessionPresetSummary[];
  defaultPresetId: string | null;
  defaultBackend: Backend;
  onOpenPreset: (preset: SessionPresetSummary | null) => void;
  onOpenSheet: (mode: LaunchSheetMode) => void;
}

export function CompactLaunch({
  targetLabel,
  presets,
  defaultPresetId,
  defaultBackend,
  onOpenPreset,
  onOpenSheet,
}: CompactLaunchProps) {
  // Lead with the default preset, then the rest by recency-of-list order.
  const ordered = [...presets].sort((a, b) => {
    if (a.id === defaultPresetId) return -1;
    if (b.id === defaultPresetId) return 1;
    return 0;
  });
  const visible = ordered.slice(0, MAX_PRESET_CHIPS);
  const overflow = ordered.length - visible.length;

  return (
    <section className="launch-deck" aria-label="Start a session">
      <div className="launch-deck-head">
        <span className="launch-deck-label">
          Start a session · <b>{targetLabel}</b>
        </span>
        <button
          type="button"
          className="launch-deck-new"
          onClick={() => onOpenSheet("new")}
        >
          + New session
        </button>
      </div>
      <div className="launch-deck-chips">
        {visible.length > 0 ? (
          visible.map((preset) => {
            const backend = (preset.spec.backend ?? defaultBackend) as Backend;
            return (
              <button
                key={preset.id}
                type="button"
                className="launch-chip"
                onClick={() => onOpenPreset(preset)}
                title={`Launch ${preset.name}…`}
              >
                <span className="launch-chip-dot" data-owner={backend} />
                {preset.name}
              </button>
            );
          })
        ) : (
          <button
            type="button"
            className="launch-chip"
            onClick={() => onOpenPreset(null)}
            title="Launch with defaults…"
          >
            <span className="launch-chip-dot" data-owner={defaultBackend} />
            Default
          </button>
        )}
        {overflow > 0 ? (
          <button
            type="button"
            className="launch-chip is-ghost"
            onClick={() => onOpenSheet("new")}
            title="All presets"
          >
            +{overflow} more
          </button>
        ) : null}
        <button
          type="button"
          className="launch-chip is-ghost"
          onClick={() => onOpenSheet("resume")}
        >
          Resume thread…
        </button>
        <button
          type="button"
          className="launch-chip is-ghost"
          onClick={() => onOpenSheet("schedule")}
        >
          Schedule…
        </button>
      </div>
    </section>
  );
}
