"use client";

import {
  CSSProperties,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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

// Versioned so a future shape change can coexist with an old saved value.
const SIZE_STORAGE_KEY = "waypoint.usage-popover-size.v1";
// Below this viewport width the mobile bottom sheet takes over; resize controls
// and saved desktop dimensions are suppressed. Matches the ``.usage-panel``
// media query and the hook's ``deferBelow``.
const DESKTOP_MIN_WIDTH = 541;
// Pixel steps for keyboard resize; Shift takes the larger stride.
const KEY_STEP = 24;
const KEY_STEP_LARGE = 72;
// The 440px CSS default width and the content-driven natural height act as snap
// detents: a resize landing within this many pixels returns that axis to its
// default (clearing the stored dimension).
const DEFAULT_PANEL_WIDTH = 440;
const SNAP_THRESHOLD = 14;

interface UsagePopoverSize {
  width?: number;
  height?: number;
}

function readStoredSize(): UsagePopoverSize {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(SIZE_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") return {};
    const record = parsed as Record<string, unknown>;
    const size: UsagePopoverSize = {};
    if (
      typeof record.width === "number" &&
      Number.isFinite(record.width) &&
      record.width > 0
    ) {
      size.width = record.width;
    }
    if (
      typeof record.height === "number" &&
      Number.isFinite(record.height) &&
      record.height > 0
    ) {
      size.height = record.height;
    }
    return size;
  } catch {
    return {};
  }
}

function writeStoredSize(size: UsagePopoverSize): void {
  if (typeof window === "undefined") return;
  try {
    if (size.width === undefined && size.height === undefined) {
      window.localStorage.removeItem(SIZE_STORAGE_KEY);
    } else {
      window.localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify(size));
    }
  } catch {
    // Storage disabled or over quota: the preference is best-effort only.
  }
}

function clampSize(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max));
}

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
  const widthSeamRef = useRef<HTMLDivElement | null>(null);

  // Desktop drives resize controls and saved dimensions; the mobile sheet is
  // left untouched. Tracked reactively so a viewport crossing 540px re-renders.
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === "undefined"
      ? true
      : window.matchMedia(`(min-width: ${DESKTOP_MIN_WIDTH}px)`).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(`(min-width: ${DESKTOP_MIN_WIDTH}px)`);
    const onChange = () => setIsDesktop(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

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

  // The saved preference is the source of truth; the rendered size is this
  // clamped against the current placement bounds on every measurement, so an
  // oversized preference is retained but shown clamped and expands when space
  // permits.
  const [pref, setPref] = useState<UsagePopoverSize>(() => readStoredSize());
  // Mirrors ``pref`` for the pointer/keyboard handlers, which merge into the
  // latest committed value; updated eagerly in those handlers and reconciled
  // here for any other path.
  const prefRef = useRef(pref);
  useEffect(() => {
    prefRef.current = pref;
  }, [pref]);
  // Actual rendered dimensions, measured after layout, for accurate seam
  // ``aria-valuenow`` (the natural default height is not known until measured).
  const [rendered, setRendered] = useState<{
    width: number;
    height: number;
  } | null>(null);

  const applyResize = useCallback((patch: UsagePopoverSize) => {
    const next = { ...prefRef.current, ...patch };
    prefRef.current = next;
    setPref(next);
    writeStoredSize(next);
  }, []);

  const resetSize = useCallback(() => {
    prefRef.current = {};
    setPref({});
    writeStoredSize({});
    // Reset auto-hides once no preference remains; keep focus in the panel by
    // moving it to a resize seam rather than dropping it to the body.
    widthSeamRef.current?.focus();
  }, []);

  // The default detent a resize snaps to: 440px for width, the content-driven
  // natural height for height. The height is derived from the content plus the
  // panel's vertical chrome, independent of the panel's current height, so the
  // target never chases a panel that has grown past its content (which would
  // otherwise oscillate the snap and flicker). Computed once per gesture.
  const snapTarget = useCallback(
    (axis: "width" | "height"): number | null => {
      if (!bounds) return null;
      if (axis === "width") {
        return clampSize(
          Math.min(DEFAULT_PANEL_WIDTH, bounds.maxWidth),
          bounds.minWidth,
          bounds.maxWidth,
        );
      }
      const panel = panelRef.current;
      const body = panel?.querySelector<HTMLElement>(".usage-panel-body");
      if (!panel || !body) return null;
      const cs = window.getComputedStyle(panel);
      // ``offsetTop`` covers the top border + padding above the body (the sole
      // flow child); the bottom chrome is read from the computed style.
      const chrome =
        body.offsetTop +
        parseFloat(cs.paddingBottom) +
        parseFloat(cs.borderBottomWidth);
      return clampSize(
        chrome + body.scrollHeight,
        bounds.minHeight,
        bounds.maxHeight,
      );
    },
    [bounds],
  );

  // Re-read storage on open so a resize made in the other placement is picked
  // up, then apply within current bounds.
  useEffect(() => {
    if (open) {
      const stored = readStoredSize();
      prefRef.current = stored;
      setPref(stored);
    }
  }, [open]);

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
      // Only pull focus back to the trigger when it was inside the panel, so a
      // click that lands on another control keeps its own focus.
      const restoreFocus = panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (restoreFocus) triggerRef.current?.focus();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) setTotalTipOpen(false);
  }, [open]);

  const startResize = useCallback(
    (axis: "width" | "height") => (event: React.PointerEvent) => {
      if (!bounds) return;
      const panel = panelRef.current;
      if (!panel) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = panel.offsetWidth;
      const startHeight = panel.offsetHeight;
      // Composer free edges are top/left (a negative pointer delta grows the
      // panel); terminal free edges are bottom/right (a positive delta grows it).
      const sign = anchored ? 1 : -1;
      const bodyClass =
        axis === "width" ? "usage-resizing-x" : "usage-resizing-y";
      document.body.classList.add(bodyClass);
      // Captured once so the snap detent is a fixed target for the whole drag.
      const target = snapTarget(axis);
      const onMove = (ev: PointerEvent) => {
        if (axis === "width") {
          const raw = clampSize(
            startWidth + sign * (ev.clientX - startX),
            bounds.minWidth,
            bounds.maxWidth,
          );
          const snap =
            target !== null && Math.abs(raw - target) <= SNAP_THRESHOLD;
          applyResize({ width: snap ? undefined : raw });
        } else {
          const raw = clampSize(
            startHeight + sign * (ev.clientY - startY),
            bounds.minHeight,
            bounds.maxHeight,
          );
          const snap =
            target !== null && Math.abs(raw - target) <= SNAP_THRESHOLD;
          applyResize({ height: snap ? undefined : raw });
        }
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.classList.remove(bodyClass);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [bounds, anchored, applyResize, snapTarget],
  );

  const onResizeKey = useCallback(
    (axis: "width" | "height") => (event: React.KeyboardEvent) => {
      if (!bounds) return;
      const step = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
      const panel = panelRef.current;
      // The arrow toward the placement's free edge grows the panel, matching
      // the pointer drag on the same seam: composer grows up/left, terminal
      // grows down/right. ``sign`` mirrors ``startResize``.
      const sign = anchored ? 1 : -1;
      if (axis === "width") {
        let dir = 0;
        if (event.key === "ArrowRight") dir = 1;
        else if (event.key === "ArrowLeft") dir = -1;
        else return;
        event.preventDefault();
        const current =
          prefRef.current.width ?? panel?.offsetWidth ?? bounds.minWidth;
        const raw = clampSize(
          current + dir * sign * step,
          bounds.minWidth,
          bounds.maxWidth,
        );
        const target = snapTarget("width");
        const snap =
          target !== null && Math.abs(raw - target) <= SNAP_THRESHOLD;
        applyResize({ width: snap ? undefined : raw });
      } else {
        let dir = 0;
        if (event.key === "ArrowDown") dir = 1;
        else if (event.key === "ArrowUp") dir = -1;
        else return;
        event.preventDefault();
        const current =
          prefRef.current.height ?? panel?.offsetHeight ?? bounds.minHeight;
        const raw = clampSize(
          current + dir * sign * step,
          bounds.minHeight,
          bounds.maxHeight,
        );
        const target = snapTarget("height");
        const snap =
          target !== null && Math.abs(raw - target) <= SNAP_THRESHOLD;
        applyResize({ height: snap ? undefined : raw });
      }
    },
    [bounds, anchored, applyResize, snapTarget],
  );

  const showResizeControls = isDesktop && bounds !== null;
  const hasCustomSize = pref.width !== undefined || pref.height !== undefined;

  const panelStyle = useMemo<CSSProperties | undefined>(() => {
    const base = anchored ? (anchorStyle ?? undefined) : undefined;
    if (!isDesktop || !bounds) return base;
    const style: CSSProperties = { ...(base ?? {}) };
    style.maxWidth = bounds.maxWidth;
    style.maxHeight = bounds.maxHeight;
    if (pref.width !== undefined) {
      style.width = clampSize(pref.width, bounds.minWidth, bounds.maxWidth);
    }
    if (pref.height !== undefined) {
      style.height = clampSize(pref.height, bounds.minHeight, bounds.maxHeight);
    }
    return style;
  }, [anchored, anchorStyle, isDesktop, bounds, pref]);

  useLayoutEffect(() => {
    if (!open || !showResizeControls) {
      setRendered(null);
      return;
    }
    const panel = panelRef.current;
    if (panel)
      setRendered({ width: panel.offsetWidth, height: panel.offsetHeight });
  }, [open, showResizeControls, panelStyle]);

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
          .map(
            (window) => `${window.label} ${Math.round(window.used_percent)}%`,
          )
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
    if (
      contextUsageToneValue === "warn" ||
      rateLimitUsageToneValue === "warn"
    ) {
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
              <div className="usage-panel-body">
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
                        className={`usage-numeral tone-${contextUsageToneValue}`}
                      >
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
                            {tokenUsageTotals.map(([key, value]) => (
                              <li key={key}>
                                <em>
                                  {UNIFIED_TOKEN_LABELS[
                                    key as keyof typeof UNIFIED_TOKEN_LABELS
                                  ] ?? key}
                                </em>
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
