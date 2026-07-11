"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { UsageBar, UsageReadout } from "@/components/UsageReadout";
import { humaniseBackend, type BackendCatalog } from "@/lib/backends";
import { UNIFIED_TOKEN_LABELS, unifyTokens } from "@/lib/tokens";
import type {
  SessionContextUsage,
  SessionRecord,
  SessionTokenUsage,
} from "@/lib/types";
import {
  clampPercent,
  formatRelativeTime,
  formatTokens,
  rateLimitUsageTone,
} from "@/lib/usage";
import { usePopoverAnchor } from "@/lib/use-popover-anchor";

type Connection = "idle" | "connecting" | "open" | "reconnecting";

interface SessionUsagePillProps {
  session: SessionRecord | null;
  connection: Connection;
  catalog?: BackendCatalog;
  onRateLimitRefresh: () => void | Promise<void>;
  rateLimitRefreshBusy: boolean;
  // When set, the panel is portaled to ``document.body`` and positioned
  // ``fixed`` below the trigger instead of rendering as a sibling. The
  // term-bar trigger lives inside ``.session-terminal``'s ``overflow:
  // hidden`` box, which would otherwise clip the dropped-down panel to the
  // pane; portaling escapes the clip.
  anchored?: boolean;
}

export function SessionUsagePill({
  session,
  connection,
  catalog,
  onRateLimitRefresh,
  rateLimitRefreshBusy,
  anchored = false,
}: SessionUsagePillProps) {
  const [open, setOpen] = useState(false);
  // Tap-to-reveal state for the cumulative-tokens tooltip (desktop also shows
  // it on hover via CSS); reset whenever the panel closes.
  const [totalTipOpen, setTotalTipOpen] = useState(false);
  const totalTipId = useId();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Below 540px the generic ``.usage-panel`` mobile bottom-sheet rule takes
  // over (deferBelow), so the inline fixed anchor only drives wider viewports.
  const anchorStyle = usePopoverAnchor(wrapRef, anchored && open, "left", {
    deferBelow: 540,
  });

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

  useEffect(() => {
    if (!open) setTotalTipOpen(false);
  }, [open]);

  const contextUsage = session?.context_usage ?? null;
  const tokenUsage = session?.session_token_usage ?? null;
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

  // Raw per-backend ledger totals overlap (Codex/OpenCode totals already
  // include cached/reasoning tokens); unify onto the 5 disjoint buckets
  // before display so the chip list never double-counts.
  const hasTokenTotals =
    tokenUsage !== null && Object.keys(tokenUsage.totals ?? {}).length > 0;
  const tokenUsageTotals = tokenUsage
    ? Object.entries(unifyTokens(tokenUsage.source, tokenUsage.totals ?? {}))
    : [];
  // A partial disclosure with nothing tracked (e.g. Codex tmux) carries only a
  // coverage note; the per-turn totals are genuinely unavailable there.
  const tokenUsageHasTotals =
    tokenUsage !== null && tokenUsage.tracked_turns > 0;

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
      : humaniseBackend(rateLimitUsage.source, catalog)
    : "Unavailable";
  // The trigger only ever wears the context-pressure or rate-limit tone; the
  // cumulative total never raises an alarm colour.
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
  const showUsagePopover =
    contextUsage !== null || tokenUsage !== null || rateLimitUsage !== null;
  const usagePopoverTitle = [
    contextUsageSummary ? `Current context ${contextUsageSummary}` : null,
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
    anchored && typeof document !== "undefined"
      ? createPortal(node, document.body)
      : node;

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
          style={anchored ? anchorStyle ?? undefined : undefined}
          role="dialog"
          aria-label="Usage details"
        >

          {rateLimitUsage ? (
            <UsageReadout
              usage={rateLimitUsage}
              sourceLabel={rateLimitSourceLabel}
              onRefresh={onRateLimitRefresh}
              refreshing={rateLimitRefreshBusy}
            />
          ) : null}

          {rateLimitUsage && (contextUsage || tokenUsage) ? (
            <hr className="usage-divider" aria-hidden="true" />
          ) : null}

          {contextUsage ? (
            <section className="usage-block">
              <header className="usage-block-head">
                <h3 className="usage-block-eyebrow">
                  <span aria-hidden className="usage-block-mark">
                    ◆
                  </span>
                  Current context window
                </h3>
                <span className="usage-block-tag">
                  {humaniseBackend(contextUsage.source, catalog)}
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
                <div className="usage-chip-group">
                  <p className="usage-chips-caption">Last turn</p>
                  <ul className="usage-chips">
                    {contextUsageBreakdown.map(([key, value]) => (
                      <li key={key}>
                        <em>{tokenCategoryLabel(key)}</em>
                        <strong>{formatTokens(value)}</strong>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </section>
          ) : null}

          {contextUsage && tokenUsage ? (
            <hr className="usage-divider" aria-hidden="true" />
          ) : null}

          {tokenUsage ? (
            <section className="usage-block">
              <header className="usage-block-head">
                <h3 className="usage-block-eyebrow">
                  <span aria-hidden className="usage-block-mark">
                    ◆
                  </span>
                  Tracked session total
                </h3>
                <span className="usage-block-tag">
                  {humaniseBackend(tokenUsage.source, catalog)}
                </span>
              </header>
              {tokenUsageHasTotals ? (
                <>
                  <div className="usage-total-explain">
                    <p className="usage-total-line">
                      <span className="usage-total-count">
                        {tokenUsage.tracked_turns}
                      </span>
                      <em>
                        {tokenUsage.tracked_turns === 1 ? "turn" : "turns"}
                      </em>
                      {typeof tokenUsage.display_total_tokens === "number" ? (
                        <span className="usage-total-work">
                          <span aria-hidden>·</span>
                          <strong>
                            {formatTokens(tokenUsage.display_total_tokens)}
                          </strong>
                          cumulative tokens
                          <button
                            type="button"
                            className="usage-info"
                            aria-label="About cumulative tokens"
                            aria-describedby={totalTipId}
                            aria-expanded={totalTipOpen}
                            onClick={(event) => {
                              event.stopPropagation();
                              setTotalTipOpen((value) => !value);
                            }}
                          >
                            ⓘ
                          </button>
                        </span>
                      ) : null}
                    </p>
                    {typeof tokenUsage.display_total_tokens === "number" ? (
                      <span
                        id={totalTipId}
                        role="tooltip"
                        className={`usage-tip${totalTipOpen ? " usage-tip--open" : ""}`}
                      >
                        Counts the whole conversation, re-read every turn.
                      </span>
                    ) : null}
                  </div>
                  {hasTokenTotals ? (
                    <ul className="usage-chips">
                      {tokenUsageTotals.map(([key, value]) => (
                        <li key={key}>
                          <em>{UNIFIED_TOKEN_LABELS[key as keyof typeof UNIFIED_TOKEN_LABELS] ?? key}</em>
                          <strong>{formatTokens(value)}</strong>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </>
              ) : null}
              <p
                className={`usage-coverage${
                  tokenUsage.coverage === "entire_waypoint_session"
                    ? ""
                    : " usage-coverage--partial"
                }`}
              >
                <span aria-hidden className="usage-coverage-dot" />
                {coverageLabel(tokenUsage)}
              </p>
            </section>
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

function coverageLabel(usage: SessionTokenUsage): string {
  switch (usage.coverage) {
    case "entire_waypoint_session":
      return "Entire Waypoint session";
    case "tracked_since":
      return `Tracked since ${formatRelativeTime(usage.observed_from)}`;
    case "partial":
      return usage.coverage_note ?? "Partial coverage";
    default:
      return usage.coverage_note ?? "Partial coverage";
  }
}

function tokenCategoryLabel(key: string): string {
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
    case "cache_creation_tokens":
      return "Cache write";
    case "cache_write_tokens":
      return "Cache write";
    default:
      return key.replaceAll("_", " ");
  }
}
