"use client";

import { SessionRateLimitUsage } from "@/lib/types";
import {
  formatRateLimitWindowReset,
  formatRateLimitWindowTokens,
  formatRelativeTime,
  rateLimitWindowPercent,
  usageTone,
  UsageTone,
} from "@/lib/usage";

const USAGE_BAR_SEGMENTS = 14;

export function UsageBar({
  percent,
  tone,
  disabled,
}: {
  percent: number | null;
  tone: UsageTone;
  disabled?: boolean;
}) {
  const filled =
    !disabled && percent !== null
      ? Math.min(
          USAGE_BAR_SEGMENTS,
          Math.max(0, Math.round((percent / 100) * USAGE_BAR_SEGMENTS)),
        )
      : 0;
  return (
    <div
      className={`usage-bar tone-${tone}${disabled ? " is-disabled" : ""}`}
      role="presentation"
    >
      {Array.from({ length: USAGE_BAR_SEGMENTS }, (_, index) => (
        <span
          key={index}
          className={index < filled ? "is-filled" : ""}
          style={{ animationDelay: `${index * 18}ms` }}
        />
      ))}
    </div>
  );
}

interface UsageReadoutProps {
  usage: SessionRateLimitUsage;
  headerLabel?: string;
  headerEyebrow?: string;
  sourceLabel: string;
  onRefresh?: () => void | Promise<void>;
  refreshing?: boolean;
}

export function UsageReadout({
  usage,
  headerLabel = "Rate limits",
  headerEyebrow,
  sourceLabel,
  onRefresh,
  refreshing = false,
}: UsageReadoutProps) {
  const windows = usage.windows;
  return (
    <section className="usage-block">
      <header className="usage-block-head">
        <h3 className="usage-block-eyebrow">
          <span aria-hidden className="usage-block-mark">
            ◇
          </span>
          {headerLabel}
        </h3>
        {headerEyebrow ? (
          <span className="usage-block-tag">{headerEyebrow}</span>
        ) : null}
        {onRefresh ? (
          <button
            type="button"
            className="usage-refresh"
            onClick={() => void onRefresh()}
            disabled={refreshing}
            aria-label={`Refresh ${headerLabel.toLowerCase()}`}
          >
            <span
              aria-hidden
              className={`usage-refresh-glyph${refreshing ? " is-spinning" : ""}`}
            >
              ↻
            </span>
            {refreshing ? "Refreshing" : "Refresh"}
          </button>
        ) : null}
      </header>

      {windows.length > 0 ? (
        <ul className="usage-windows">
          {windows.map((window) => {
            const percent = rateLimitWindowPercent(window);
            const tone = usageTone(percent);
            const resetText = formatRateLimitWindowReset(window);
            const tokenSummary = formatRateLimitWindowTokens(window);
            return (
              <li key={window.id} className={`usage-window tone-${tone}`}>
                <div className="usage-window-head">
                  <span className="usage-window-label">{window.label}</span>
                  {resetText ? (
                    <span className="usage-window-reset">
                      <span aria-hidden className="usage-window-reset-glyph">
                        ◷
                      </span>
                      {resetText}
                    </span>
                  ) : null}
                </div>
                <div className="usage-window-body">
                  <div
                    className={`usage-numeral usage-numeral--sm tone-${tone}`}
                  >
                    <strong>{percent !== null ? percent : "—"}</strong>
                    <em>%</em>
                  </div>
                  <UsageBar
                    percent={percent}
                    tone={tone}
                    disabled={percent === null}
                  />
                </div>
                {tokenSummary ? (
                  <p className="usage-window-tokens">{tokenSummary}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="usage-empty">No quota tracked on this plan.</p>
      )}

      <footer className="usage-meta">
        <span className="usage-meta-cell">
          <em>updated</em>
          <span title={new Date(usage.updated_at).toLocaleString()}>
            {formatRelativeTime(usage.updated_at)}
          </span>
        </span>
        <i aria-hidden className="usage-meta-bullet">
          ·
        </i>
        <span className="usage-meta-cell">
          <em>source</em>
          <span>{sourceLabel}</span>
        </span>
        {usage.credits_remaining !== null &&
        usage.credits_remaining !== undefined ? (
          <>
            <i aria-hidden className="usage-meta-bullet">
              ·
            </i>
            <span className="usage-meta-cell">
              <em>credits</em>
              <span>
                {`${usage.credits_currency ?? "credits"} ${usage.credits_remaining.toFixed(2)}`}
              </span>
            </span>
          </>
        ) : null}
      </footer>
    </section>
  );
}
