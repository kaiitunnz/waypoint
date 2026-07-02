"use client";

import { UsageDial } from "@/components/UsageDial";
import { humaniseBackend, type BackendCatalog } from "@/lib/backends";
import { UsageDashboardBucket } from "@/lib/types";
import {
  formatRateLimitWindowReset,
  formatRelativeTime,
  rateLimitUsageTone,
  rateLimitWindowPercent,
  usageTone,
} from "@/lib/usage";

interface UsageInstrumentPanelProps {
  bucket: UsageDashboardBucket;
  catalog?: BackendCatalog;
  emphasis?: "primary" | "secondary";
  onRefresh?: () => void | Promise<void>;
  refreshing?: boolean;
  index?: number;
}

function bucketDetailNotes(bucket: UsageDashboardBucket): string[] {
  const notes = bucket.snapshot.notes ?? [];
  return notes.filter(
    (note) => note !== "CLI OAuth" && note !== "remote OAuth",
  );
}

export function UsageInstrumentPanel({
  bucket,
  catalog,
  emphasis = "secondary",
  onRefresh,
  refreshing = false,
  index = 0,
}: UsageInstrumentPanelProps) {
  const tone = rateLimitUsageTone(bucket.snapshot);
  const windows = bucket.snapshot.windows;
  const sessionCount = bucket.session_ids.length;
  const detailNotes = bucketDetailNotes(bucket);
  const updatedLabel = formatRelativeTime(bucket.snapshot.updated_at);
  const credits = bucket.snapshot.credits_remaining;
  const currency = bucket.snapshot.credits_currency ?? "credits";

  return (
    <article
      className={`usage-instrument tone-${tone} emphasis-${emphasis}`}
      style={{
        // Stagger reveal on mount — `--enter-delay` is consumed by the
        // CSS animation defined alongside the panel.
        ["--enter-delay" as string]: `${index * 80}ms`,
      }}
    >
      <header className="usage-instrument-head">
        <div className="usage-instrument-plaque">
          <span className="usage-instrument-eyebrow">
            {humaniseBackend(bucket.backend, catalog)}
          </span>
          <h3 className="usage-instrument-title">{bucket.account_label}</h3>
        </div>
        <div className="usage-instrument-meta">
          <span className={`usage-instrument-status tone-${tone}`}>
            <span aria-hidden className="usage-instrument-status-dot" />
            {tone === "danger"
              ? "Critical"
              : tone === "warn"
                ? "Approaching"
                : "Nominal"}
          </span>
          {onRefresh ? (
            <button
              type="button"
              className="usage-instrument-refresh"
              onClick={() => void onRefresh()}
              disabled={refreshing}
              aria-label={`Refresh ${bucket.account_label}`}
              title="Refresh"
            >
              <span
                aria-hidden
                className={`usage-instrument-refresh-glyph${refreshing ? " is-spinning" : ""}`}
              >
                ↻
              </span>
            </button>
          ) : null}
        </div>
      </header>

      <div className="usage-instrument-body">
        {windows.length > 0 ? (
          <div
            className="usage-instrument-dials"
            data-count={windows.length}
          >
            {windows.map((window, i) => {
              const percent = rateLimitWindowPercent(window);
              const windowTone = usageTone(percent);
              const resetText = formatRateLimitWindowReset(window);
              return (
                <UsageDial
                  key={`${window.id}-${i}`}
                  percent={percent}
                  tone={windowTone}
                  label={window.label}
                  caption={resetText ? `resets ${resetText}` : null}
                  size={emphasis === "primary" ? "lg" : "md"}
                />
              );
            })}
          </div>
        ) : (
          <div className="usage-instrument-empty">
            <span className="usage-instrument-empty-mark">∅</span>
            <p>No quota tracked on this plan.</p>
          </div>
        )}
      </div>

      <footer className="usage-instrument-footer">
        <div className="usage-instrument-footer-row">
          <span className="usage-instrument-footer-cell">
            <em>Sessions</em>
            <strong>{sessionCount}</strong>
          </span>
          <span className="usage-instrument-footer-cell">
            <em>Sweep</em>
            <strong title={new Date(bucket.snapshot.updated_at).toLocaleString()}>
              {updatedLabel}
            </strong>
          </span>
          {credits !== null && credits !== undefined ? (
            <span className="usage-instrument-footer-cell">
              <em>Credits</em>
              <strong>{`${currency} ${credits.toFixed(2)}`}</strong>
            </span>
          ) : null}
        </div>
        {detailNotes.length > 0 ? (
          <p className="usage-instrument-notes">
            {detailNotes.map((note, i) => (
              <span key={i} className="usage-instrument-note-chip">
                {note}
              </span>
            ))}
          </p>
        ) : null}
      </footer>
    </article>
  );
}
