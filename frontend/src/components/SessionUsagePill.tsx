"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { UsageBar, UsageReadout } from "@/components/UsageReadout";
import { humaniseBackend } from "@/lib/backends";
import type { SessionContextUsage, SessionRecord } from "@/lib/types";
import {
  clampPercent,
  formatRelativeTime,
  formatTokens,
  rateLimitUsageTone,
} from "@/lib/usage";

type Connection = "idle" | "connecting" | "open" | "reconnecting";

interface SessionUsagePillProps {
  session: SessionRecord | null;
  connection: Connection;
  onRateLimitRefresh: () => void | Promise<void>;
  rateLimitRefreshBusy: boolean;
  // When provided, the popover panel is portaled into this element
  // instead of rendering as a sibling of the trigger button. Used by
  // the tmux quick-compose drawer where the trigger lives inside an
  // ``overflow: hidden`` ancestor — portaling escapes the clip so the
  // panel can float above the drawer.
  popoverContainer?: HTMLElement | null;
}

export function SessionUsagePill({
  session,
  connection,
  onRateLimitRefresh,
  rateLimitRefreshBusy,
  popoverContainer,
}: SessionUsagePillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(event: MouseEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (wrapRef.current?.contains(target)) return;
      // Once portaled, the panel is no longer a descendant of the
      // wrapper — check it separately so clicks inside the panel
      // don't dismiss it.
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const contextUsage = session?.context_usage ?? null;
  const rateLimitUsage = session?.rate_limit_usage ?? null;
  const contextUsagePercentValue = contextUsage
    ? contextUsagePercent(contextUsage)
    : null;
  const contextUsagePercentDisplay = clampPercent(contextUsagePercentValue);
  const contextUsageToneValue = contextUsage
    ? contextUsageTone(contextUsagePercentValue)
    : "good";
  const rateLimitUsageToneValue = rateLimitUsageTone(rateLimitUsage);
  const contextUsageBreakdown = contextUsage
    ? Object.entries(contextUsage.breakdown ?? {})
    : [];
  const contextUsageHasWindow =
    contextUsage !== null &&
    typeof contextUsage.context_window_tokens === "number" &&
    contextUsage.context_window_tokens > 0;
  const contextUsageWindowTokens = contextUsageHasWindow
    ? contextUsage.context_window_tokens
    : null;
  const contextUsageWindowDisplay = contextUsageWindowTokens ?? 0;
  const contextUsageSummary = contextUsage
    ? contextUsageWindowTokens !== null && contextUsagePercentDisplay !== null
      ? `${formatTokens(contextUsage.used_tokens)} / ${formatTokens(contextUsageWindowDisplay)} (${contextUsagePercentDisplay}%)`
      : formatTokens(contextUsage.used_tokens)
    : null;
  const rateLimitUsageSummary = rateLimitUsage
    ? rateLimitUsage.windows.length > 0
      ? rateLimitUsage.windows
          .map((window) => `${window.label} ${Math.round(window.used_percent)}%`)
          .join(" · ")
      : rateLimitUsage.notes?.length
        ? rateLimitUsage.notes.join(" · ")
        : null
    : null;
  const rateLimitSourceLabel = rateLimitUsage
    ? rateLimitUsage.notes?.length
      ? rateLimitUsage.notes.join(" · ")
      : humaniseBackend(rateLimitUsage.source)
    : "Unavailable";
  const usageToneValue = (() => {
    if (contextUsage === null) return rateLimitUsageToneValue;
    if (rateLimitUsage === null) return contextUsageToneValue;
    if (
      contextUsageToneValue === "danger" ||
      rateLimitUsageToneValue === "danger"
    ) {
      return "danger";
    }
    if (contextUsageToneValue === "warn" || rateLimitUsageToneValue === "warn") {
      return "warn";
    }
    return "good";
  })();
  const showUsagePopover = contextUsage !== null || rateLimitUsage !== null;
  const usagePopoverTitle = [
    contextUsageSummary ? `Context ${contextUsageSummary}` : null,
    rateLimitUsageSummary ? `Rate limits ${rateLimitUsageSummary}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  if (!showUsagePopover) {
    return (
      <span
        className={`composer-connection ${connection}`}
        title={`Backend socket ${connection}`}
        role="status"
        aria-live="polite"
      >
        {connection === "open"
          ? "live"
          : connection === "reconnecting"
            ? "reconnecting"
            : "connecting"}
      </span>
    );
  }

  const renderPanel = (node: React.ReactNode): React.ReactNode =>
    popoverContainer ? createPortal(node, popoverContainer) : node;

  return (
    <div className="composer-context" ref={wrapRef}>
      <button
        type="button"
        className={`composer-connection composer-context-trigger tone-${usageToneValue} ${connection} ${open ? "open" : ""}`}
        title={
          usagePopoverTitle
            ? `Backend socket ${connection}. ${usagePopoverTitle}`
            : `Backend socket ${connection}. Click for usage details`
        }
        aria-live="polite"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Backend socket ${connection}. Usage details`}
        onClick={() => setOpen((value) => !value)}
      >
        {connection === "open"
          ? "live"
          : connection === "reconnecting"
            ? "reconnecting"
            : "connecting"}
      </button>
      {open ? renderPanel(
        <div
          ref={panelRef}
          className={`usage-panel tone-${usageToneValue}`}
          role="dialog"
          aria-label="Usage details"
        >
          <span className="usage-panel-rail" aria-hidden="true" />

          {contextUsage ? (
            <section className="usage-block">
              <header className="usage-block-head">
                <h3 className="usage-block-eyebrow">
                  <span aria-hidden className="usage-block-mark">
                    ◆
                  </span>
                  Context
                </h3>
                <span className="usage-block-tag">
                  {humaniseBackend(contextUsage.source)}
                </span>
              </header>
              <div className="usage-block-body">
                <div className={`usage-numeral tone-${contextUsageToneValue}`}>
                  <strong>
                    {contextUsagePercentDisplay !== null
                      ? contextUsagePercentDisplay
                      : "—"}
                  </strong>
                  <em>%</em>
                </div>
                <div className="usage-block-stack">
                  <p className="usage-line">
                    <span>{formatTokens(contextUsage.used_tokens)}</span>
                    <em>of</em>
                    <span>
                      {contextUsageWindowTokens !== null
                        ? formatTokens(contextUsageWindowDisplay)
                        : "—"}
                    </span>
                    <em>tokens</em>
                  </p>
                  <UsageBar
                    percent={contextUsagePercentDisplay}
                    tone={contextUsageToneValue}
                    disabled={
                      !contextUsageHasWindow ||
                      contextUsagePercentDisplay === null
                    }
                  />
                  <p className="usage-line-meta">
                    <em>updated</em>
                    <span title={new Date(contextUsage.updated_at).toLocaleString()}>
                      {formatRelativeTime(contextUsage.updated_at)}
                    </span>
                  </p>
                </div>
              </div>
              {contextUsageBreakdown.length > 0 ? (
                <ul className="usage-chips">
                  {contextUsageBreakdown.map(([key, value]) => (
                    <li key={key}>
                      <em>{contextUsageLabel(key)}</em>
                      <strong>{formatTokens(value)}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          {contextUsage && rateLimitUsage ? (
            <hr className="usage-divider" aria-hidden="true" />
          ) : null}

          {rateLimitUsage ? (
            <UsageReadout
              usage={rateLimitUsage}
              sourceLabel={rateLimitSourceLabel}
              onRefresh={onRateLimitRefresh}
              refreshing={rateLimitRefreshBusy}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function contextUsagePercent(usage: SessionContextUsage): number | null {
  const windowTokens = usage.context_window_tokens;
  if (!windowTokens || windowTokens <= 0) {
    return null;
  }
  return Math.round((usage.used_tokens / windowTokens) * 100);
}

function contextUsageTone(percent: number | null): "good" | "warn" | "danger" {
  if (percent === null) return "good";
  if (percent >= 90) return "danger";
  if (percent >= 70) return "warn";
  return "good";
}

function contextUsageLabel(key: string): string {
  switch (key) {
    case "input_tokens":
      return "Input";
    case "cached_input_tokens":
      return "Cached input";
    case "output_tokens":
      return "Output";
    case "reasoning_output_tokens":
      return "Reasoning";
    case "reasoning_tokens":
      return "Reasoning";
    case "cache_read_tokens":
      return "Cache read";
    case "cache_write_tokens":
      return "Cache write";
    default:
      return key.replaceAll("_", " ");
  }
}
