"use client";

import { useState } from "react";

import { TelemetryDeleteResponse, TelemetrySettingsResponse } from "@/lib/types";

interface SettingsPanelProps {
  settings: TelemetrySettingsResponse | null;
  loading: boolean;
  deleting: boolean;
  deleteResult: TelemetryDeleteResponse | null;
  onDelete: () => void;
}

export function SettingsPanel({
  settings,
  loading,
  deleting,
  deleteResult,
  onDelete,
}: SettingsPanelProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <section className="panel tm-settings-panel">
      <button
        type="button"
        className="tm-filters-toggle tm-settings-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        Privacy, coverage &amp; retention
        <span aria-hidden="true" className="tm-filters-toggle-chevron">
          {expanded ? "▴" : "▾"}
        </span>
      </button>

      {expanded ? (
        loading && !settings ? (
          <p className="muted">Loading settings…</p>
        ) : settings ? (
          <div className="tm-settings-body">
            <p className="tm-settings-statement">{settings.privacy_statement}</p>
            <dl className="tm-stat-list">
              <div className="tm-stat-row">
                <span className="tm-stat-row-label">Fact retention</span>
                <span className="tm-stat-row-value">{settings.retention_days_facts} days</span>
              </div>
              <div className="tm-stat-row">
                <span className="tm-stat-row-label">Rollup retention</span>
                <span className="tm-stat-row-value">
                  {settings.retention_months_rollups} months
                </span>
              </div>
              <div className="tm-stat-row">
                <span className="tm-stat-row-label">Backfill</span>
                <span className="tm-stat-row-value">
                  {settings.coverage.backfill_done ? "Complete" : "In progress"}
                  {settings.coverage.backfill_through
                    ? ` · through ${new Date(settings.coverage.backfill_through).toLocaleDateString()}`
                    : ""}
                </span>
              </div>
              <div className="tm-stat-row">
                <span className="tm-stat-row-label">External export</span>
                <span className="tm-stat-row-value">
                  {settings.external_export ? "Enabled" : "Disabled"}
                </span>
              </div>
              <div className="tm-stat-row">
                <span className="tm-stat-row-label">Content capture</span>
                <span className="tm-stat-row-value">
                  {settings.content_capture ? "Enabled" : "Disabled"}
                </span>
              </div>
            </dl>

            <div className="tm-nl-settings-row">
              <label className="tm-nl-toggle-label">
                <input
                  type="checkbox"
                  className="tm-nl-toggle"
                  checked={settings.nl_enabled}
                  disabled
                  readOnly
                  aria-describedby="tm-nl-settings-caption"
                />
                AI insights (opt-in)
              </label>
              <span className="tm-nl-toggle-state">
                {settings.nl_enabled ? "On" : "Off"}
              </span>
            </div>
            <p id="tm-nl-settings-caption" className="tm-settings-statement">
              When on, a coding agent you configure receives the on-screen
              aggregates plus a bounded set of redacted drilldown rows (session
              id, normalized tool name, timestamp, outcome, model) to produce a
              plain-language digest — never raw prompts, tool arguments, or
              paths. Set with <code>telemetry_nl.enabled</code> in{" "}
              <code>waypoint.yaml</code> (or{" "}
              <code>WAYPOINT_TELEMETRY_NL_ENABLED</code>).
            </p>

            <div className="tm-settings-danger">
              <p className="muted">
                Removes all stored facts and rollups; transcripts are untouched.
              </p>
              <button
                type="button"
                className="board-action board-action-danger"
                disabled={deleting}
                onClick={() => {
                  if (
                    window.confirm(
                      "Delete all retained telemetry (facts + rollups)? Session transcripts are not affected. This cannot be undone.",
                    )
                  ) {
                    onDelete();
                  }
                }}
              >
                {deleting ? "Deleting…" : "Delete all telemetry"}
              </button>
              {deleteResult ? (
                <p className="tm-settings-delete-result" role="status">
                  Removed {deleteResult.removed.facts} facts and {deleteResult.removed.rollups}{" "}
                  rollup rows.
                </p>
              ) : null}
            </div>
          </div>
        ) : (
          <p className="muted">Settings unavailable.</p>
        )
      ) : null}
    </section>
  );
}
