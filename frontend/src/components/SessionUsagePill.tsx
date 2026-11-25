"use client";

import { CSSProperties, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { UsageBar, UsageReadout } from "@/components/UsageReadout";
import { humaniseBackend, type BackendCatalog } from "@waypoint/web-core/backends";
import { coverageLabel, usagePanelModel } from "@waypoint/web-core/session/usagePanel";
import type { SessionRecord } from "@waypoint/web-core/types";
import { formatRelativeTime, formatTokens } from "@waypoint/web-core/usage";
import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";
import { usePopoverAnchor } from "@waypoint/web-core/hooks/use-popover-anchor";
import { usePopoverDismiss } from "@waypoint/web-core/hooks/use-popover-dismiss";
import { useResizablePopover } from "@waypoint/web-core/hooks/useResizablePopover";
import { USAGE_POPOVER_SIZE_KEY } from "@waypoint/web-core/popoverSize";

type Connection = "idle" | "connecting" | "open" | "reconnecting";

// Below this viewport width the mobile bottom sheet takes over; resize controls
// and saved desktop dimensions are suppressed. Matches the ``.usage-panel``
// media query and the hook's ``deferBelow``.
const DESKTOP_MIN_WIDTH = 541;
// The 440px CSS default width, which a resize snaps back to.
const DEFAULT_PANEL_WIDTH = 440;

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
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const widthSeamRef = useRef<HTMLDivElement | null>(null);

  // Desktop drives resize controls and saved dimensions; the mobile sheet is
  // left untouched.
  const isDesktop = useMediaQuery(`(min-width: ${DESKTOP_MIN_WIDTH}px)`);

  // Below 540px the generic ``.usage-panel`` mobile bottom-sheet rule takes
  // over (deferBelow), so the fixed anchor and bounds only drive wider
  // viewports. The composer placement leaves positioning to CSS but still uses
  // the bounds; the terminal placement is portaled and fixed-positioned.
  const { style: anchorStyle, bounds } = usePopoverAnchor(
    wrapRef,
    open,
    "left",
    { deferBelow: 540 },
    anchored ? "dropdown" : "composer",
  );

  // Composer free edges are top/left (a negative pointer delta grows the
  // panel); terminal free edges are bottom/right (a positive delta grows it).
  const grow = anchored ? 1 : -1;
  const {
    style: sizeStyle,
    resizable: showResizeControls,
    custom: hasCustomSize,
    rendered,
    startResize,
    onResizeKey,
    reset,
  } = useResizablePopover({
    storageKey: USAGE_POPOVER_SIZE_KEY,
    open,
    enabled: isDesktop,
    bounds,
    panelRef,
    bodyRef,
    defaultWidth: DEFAULT_PANEL_WIDTH,
    direction: { width: grow, height: grow },
  });

  const resetSize = () => {
    reset();
    // Reset auto-hides once no preference remains; keep focus in the panel by
    // moving it to a resize seam rather than dropping it to the body.
    widthSeamRef.current?.focus();
  };

  usePopoverDismiss(open, setOpen, wrapRef, panelRef, triggerRef);

  useEffect(() => {
    if (!open) setTotalTipOpen(false);
  }, [open]);

  const base = anchored ? (anchorStyle ?? undefined) : undefined;
  const panelStyle: CSSProperties | undefined = sizeStyle ? { ...base, ...sizeStyle } : base;

  const {
    contextUsage,
    tokenUsage,
    rateLimitUsage,
    hasUsage: showUsagePopover,
    contextPercent: contextUsagePercentDisplay,
    contextTone: contextUsageToneValue,
    contextWindowTokens: contextUsageWindowTokens,
    contextUnreported: contextUsageUnreported,
    contextBreakdown: contextUsageBreakdown,
    rateLimitSourceLabel,
    tone: usageToneValue,
    tokenTotals: tokenUsageTotals,
    hasTokenTotals,
    hasTrackedTurns: tokenUsageHasTotals,
    contextSummary: contextUsageSummary,
    rateLimitSummary: rateLimitUsageSummary,
  } = usagePanelModel(session, catalog);
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
        ref={triggerRef}
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
      {open
        ? renderPanel(
            <div
              ref={panelRef}
              className={`usage-panel usage-panel--${anchored ? "terminal" : "composer"} tone-${usageToneValue}`}
              style={panelStyle}
              role="dialog"
              aria-label="Usage details"
            >
              {showResizeControls && bounds ? (
                <>
                  <div
                    ref={widthSeamRef}
                    className="usage-resize usage-resize-width"
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize width"
                    aria-valuenow={Math.round(
                      rendered?.width ?? bounds.minWidth,
                    )}
                    aria-valuemin={bounds.minWidth}
                    aria-valuemax={bounds.maxWidth}
                    tabIndex={0}
                    onPointerDown={startResize("width")}
                    onKeyDown={onResizeKey("width")}
                  />
                  <div
                    className="usage-resize usage-resize-height"
                    role="separator"
                    aria-orientation="horizontal"
                    aria-label="Resize height"
                    aria-valuenow={Math.round(
                      rendered?.height ?? bounds.minHeight,
                    )}
                    aria-valuemin={bounds.minHeight}
                    aria-valuemax={bounds.maxHeight}
                    tabIndex={0}
                    onPointerDown={startResize("height")}
                    onKeyDown={onResizeKey("height")}
                  />
                  {hasCustomSize ? (
                    <button
                      type="button"
                      className="usage-resize-reset"
                      aria-label="Reset popover size"
                      title="Reset size"
                      onClick={resetSize}
                    >
                      ↺
                    </button>
                  ) : null}
                </>
              ) : null}
              <div ref={bodyRef} className="usage-panel-body">
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
                      <div
                        className={`usage-numeral tone-${contextUsageToneValue}${
                          contextUsageUnreported ? " usage-numeral--muted" : ""
                        }`}
                      >
                        <strong>
                          {contextUsagePercentDisplay !== null
                            ? contextUsagePercentDisplay
                            : "—"}
                        </strong>
                        {contextUsagePercentDisplay !== null ? <em>%</em> : null}
                      </div>
                      <div className="usage-block-stack">
                        {contextUsageUnreported ? (
                          <p className="usage-line">
                            <span>
                              {contextUsageWindowTokens !== null
                                ? formatTokens(contextUsageWindowTokens)
                                : "—"}
                            </span>
                            <em>token window</em>
                          </p>
                        ) : (
                          <p className="usage-line">
                            <span>{formatTokens(contextUsage.used_tokens)}</span>
                            <em>of</em>
                            <span>
                              {contextUsageWindowTokens !== null
                                ? formatTokens(contextUsageWindowTokens)
                                : "—"}
                            </span>
                            <em>tokens</em>
                          </p>
                        )}
                        {contextUsageUnreported ? (
                          <p className="usage-context-note">
                            This endpoint doesn&rsquo;t report context usage
                          </p>
                        ) : (
                          <UsageBar
                            percent={contextUsagePercentDisplay}
                            tone={contextUsageToneValue}
                            disabled={
                              contextUsageWindowTokens === null ||
                              contextUsagePercentDisplay === null
                            }
                          />
                        )}
                        <p className="usage-line-meta">
                          <em>updated</em>
                          <span
                            title={new Date(
                              contextUsage.updated_at,
                            ).toLocaleString()}
                          >
                            {formatRelativeTime(contextUsage.updated_at)}
                          </span>
                        </p>
                      </div>
                    </div>
                    {contextUsageBreakdown.length > 0 ? (
                      <div className="usage-chip-group">
                        <p className="usage-chips-caption">Last turn</p>
                        <ul className="usage-chips">
                          {contextUsageBreakdown.map(([label, value]) => (
                            <li key={label}>
                              <em>{label}</em>
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
                              {tokenUsage.tracked_turns === 1
                                ? "turn"
                                : "turns"}
                            </em>
                            {typeof tokenUsage.display_total_tokens ===
                            "number" ? (
                              <span className="usage-total-work">
                                <span aria-hidden>·</span>
                                <strong>
                                  {formatTokens(
                                    tokenUsage.display_total_tokens,
                                  )}
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
                          {typeof tokenUsage.display_total_tokens ===
                          "number" ? (
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
                            {tokenUsageTotals.map(([label, value]) => (
                              <li key={label}>
                                <em>{label}</em>
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
            </div>,
          )
        : null}
    </div>
  );
}
