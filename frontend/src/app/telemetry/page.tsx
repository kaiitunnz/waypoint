"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/ThemeToggle";
import { ActivityChart } from "@/components/telemetry/ActivityChart";
import { DrilldownPanel } from "@/components/telemetry/DrilldownPanel";
import { HealthPanel } from "@/components/telemetry/HealthPanel";
import { InsightCards } from "@/components/telemetry/InsightCards";
import { InstanceHealthPanel } from "@/components/telemetry/InstanceHealthPanel";
import { NLInsightCard } from "@/components/telemetry/NLInsightCard";
import { OverviewCards } from "@/components/telemetry/OverviewCards";
import { SettingsPanel } from "@/components/telemetry/SettingsPanel";
import { TelemetryFilters } from "@/components/telemetry/TelemetryFilters";
import { TokenChart } from "@/components/telemetry/TokenChart";
import {
  connectSessionsSocket,
  deleteTelemetry,
  dismissTelemetryInsight,
  fetchTelemetryActivity,
  fetchTelemetryDrilldown,
  fetchTelemetryHealth,
  fetchTelemetryInsights,
  fetchTelemetryInstance,
  fetchTelemetryOverview,
  fetchTelemetrySettings,
  fetchTelemetryTokens,
  fetchNLInsight,
  fetchMe,
  generateNLInsight,
  isAuthError,
  isTelemetryDisabledError,
  refreshTelemetryInstance,
} from "@/lib/api";
import { agentTransports, useBackendCatalog } from "@/lib/backends";
import { clearToken, readHost, readToken } from "@/lib/store";
import { formatRangeLabel, readTelemetryQuery, writeTelemetryQuery } from "@/lib/telemetry";
import { useTheme } from "@/lib/theme";
import {
  Insight,
  NLGenerationStatus,
  NLInsightEvidence,
  NLInsightResponse,
  SessionEnvelope,
  TelemetryActivity,
  TelemetryDeleteResponse,
  TelemetryDrilldown,
  TelemetryFactKind,
  TelemetryHealth,
  TelemetryInstance,
  TelemetryOverview,
  TelemetrySettingsResponse,
  TelemetryTokens,
  TokenGroupBy,
} from "@/lib/types";

const DRILLDOWN_PAGE_SIZE = 20;
const REFRESH_DEBOUNCE_MS = 400;

type LoadState = "loading" | "ready" | "error";
// Master telemetry opt-in, resolved from `/api/me` before any telemetry fetch.
// It starts "unknown" so the refreshers and the WS subscription stay dormant
// until the capability is known — a fresh mount must never touch a telemetry
// endpoint while the feature might be disabled.
type TelemetryCapability = "unknown" | "enabled" | "disabled";

// Insight click-throughs carry the aggregate endpoint they were derived from;
// route "View evidence" to the matching section so it lands on the data behind
// the claim. Anything unrecognized (or absent) falls back to the drilldown.
function scrollToEvidenceSection(endpoint: string | undefined): void {
  const target =
    typeof endpoint === "string" && endpoint.includes("/health")
      ? "tm-health-anchor"
      : typeof endpoint === "string" && endpoint.includes("/tokens")
        ? "tm-tokens-anchor"
        : typeof endpoint === "string" && endpoint.includes("/instance")
          ? "tm-instance-anchor"
          : "tm-drilldown-anchor";
  document.getElementById(target)?.scrollIntoView({ behavior: "smooth" });
}

export default function TelemetryPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [telemetryCap, setTelemetryCap] = useState<TelemetryCapability>("unknown");

  const [range, setRange] = useState(() => readTelemetryQuery().range);
  const [filters, setFilters] = useState(() => readTelemetryQuery().filters);

  const [overview, setOverview] = useState<TelemetryOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [tokens, setTokens] = useState<TelemetryTokens | null>(null);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokenGroupBy, setTokenGroupBy] = useState<TokenGroupBy>("time");
  const [activity, setActivity] = useState<TelemetryActivity | null>(null);
  const [activityLoading, setActivityLoading] = useState(true);
  const [health, setHealth] = useState<TelemetryHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [insightsLoading, setInsightsLoading] = useState(true);
  const [dismissingSignature, setDismissingSignature] = useState<string | null>(null);
  const [settings, setSettings] = useState<TelemetrySettingsResponse | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<TelemetryDeleteResponse | null>(null);

  const [drilldownKind, setDrilldownKind] = useState<TelemetryFactKind>("tool_call");
  const [drilldownPage, setDrilldownPage] = useState(1);
  const [drilldown, setDrilldown] = useState<TelemetryDrilldown | null>(null);
  const [drilldownLoading, setDrilldownLoading] = useState(true);

  const [nlResponse, setNlResponse] = useState<NLInsightResponse | null>(null);
  const [nlLoading, setNlLoading] = useState(true);
  // Server-owned regeneration status: seeded from the GET, updated by the
  // nl_insight_status WebSocket frame, and set optimistically on click — so a
  // reload or a second tab reflects "Regenerating…"/failed it didn't initiate.
  const [nlGeneration, setNlGeneration] = useState<NLGenerationStatus | null>(null);

  const [instance, setInstance] = useState<TelemetryInstance | null>(null);
  const [instanceLoading, setInstanceLoading] = useState(true);
  const [instanceRefreshing, setInstanceRefreshing] = useState(false);

  const catalog = useBackendCatalog(host || null, token || null, null);

  // A custom range with only one bound picked is a normal mid-edit state, not
  // an error — the backend 400s on it ("custom range requires both start and
  // end"), so every fetch below no-ops instead of firing while it's incomplete.
  const customRangeIncomplete = range.preset === "custom" && (!range.start || !range.end);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    if (!currentHost || !currentToken) {
      router.replace("/");
    }
  }, [router]);

  // Resolve the telemetry capability before any telemetry fetch fires. Until
  // this settles, `telemetryCap` stays "unknown" and every refresher + the WS
  // subscription short-circuit, so a disabled backend is never probed.
  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    fetchMe(host, token)
      .then((me) => {
        if (!active) return;
        setTelemetryCap(me.telemetry_enabled ? "enabled" : "disabled");
      })
      .catch((err) => {
        if (!active) return;
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setState("error");
        setError(err instanceof Error ? err.message : "failed to load telemetry");
      });
    return () => {
      active = false;
    };
  }, [host, token, handleAuthFailure]);

  // Persist range/filters (skip the very first render, which is what we just read).
  const skipPersist = useRef(true);
  useEffect(() => {
    if (skipPersist.current) {
      skipPersist.current = false;
      return;
    }
    writeTelemetryQuery(range, filters);
    setDrilldownPage(1);
  }, [range, filters]);

  // Monotonic request ids guard each refresher against stale responses: a
  // slower earlier fetch must not overwrite state from a newer one (a preset /
  // filter change, or a telemetry_update firing mid-edit). Each call captures
  // its id and only applies state + clears loading while it is still the latest.
  const overviewReqRef = useRef(0);
  const tokensReqRef = useRef(0);
  const drilldownReqRef = useRef(0);

  const refreshOverviewGroup = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    const requestId = ++overviewReqRef.current;
    if (customRangeIncomplete) {
      setOverviewLoading(false);
      setActivityLoading(false);
      setHealthLoading(false);
      setInsightsLoading(false);
      return;
    }
    setOverviewLoading(true);
    setActivityLoading(true);
    setHealthLoading(true);
    setInsightsLoading(true);
    try {
      const [overviewRes, activityRes, healthRes, insightsRes] = await Promise.all([
        fetchTelemetryOverview(host, token, range, filters),
        fetchTelemetryActivity(host, token, range, filters),
        fetchTelemetryHealth(host, token, range, filters),
        fetchTelemetryInsights(host, token, range, filters),
      ]);
      if (requestId !== overviewReqRef.current) return;
      setOverview(overviewRes);
      setActivity(activityRes);
      setHealth(healthRes);
      setInsights(insightsRes);
      setState("ready");
      setError("");
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      if (requestId !== overviewReqRef.current) return;
      setState((current) => (current === "ready" ? current : "error"));
      setError(err instanceof Error ? err.message : "failed to load telemetry");
    } finally {
      if (requestId === overviewReqRef.current) {
        setOverviewLoading(false);
        setActivityLoading(false);
        setHealthLoading(false);
        setInsightsLoading(false);
      }
    }
  }, [host, token, range, filters, customRangeIncomplete, telemetryCap, handleAuthFailure]);

  const refreshTokens = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    const requestId = ++tokensReqRef.current;
    if (customRangeIncomplete) {
      setTokensLoading(false);
      return;
    }
    setTokensLoading(true);
    try {
      const res = await fetchTelemetryTokens(host, token, range, filters, tokenGroupBy);
      if (requestId !== tokensReqRef.current) return;
      setTokens(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      if (requestId !== tokensReqRef.current) return;
      setError(err instanceof Error ? err.message : "failed to load token usage");
    } finally {
      if (requestId === tokensReqRef.current) {
        setTokensLoading(false);
      }
    }
  }, [host, token, range, filters, tokenGroupBy, customRangeIncomplete, telemetryCap, handleAuthFailure]);

  const refreshDrilldown = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    const requestId = ++drilldownReqRef.current;
    if (customRangeIncomplete) {
      setDrilldownLoading(false);
      return;
    }
    setDrilldownLoading(true);
    try {
      const res = await fetchTelemetryDrilldown(
        host,
        token,
        range,
        filters,
        drilldownKind,
        drilldownPage,
        DRILLDOWN_PAGE_SIZE,
      );
      if (requestId !== drilldownReqRef.current) return;
      setDrilldown(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      if (requestId !== drilldownReqRef.current) return;
      setError(err instanceof Error ? err.message : "failed to load drilldown");
    } finally {
      if (requestId === drilldownReqRef.current) {
        setDrilldownLoading(false);
      }
    }
  }, [
    host,
    token,
    range,
    filters,
    drilldownKind,
    drilldownPage,
    customRangeIncomplete,
    telemetryCap,
    handleAuthFailure,
  ]);

  const refreshSettings = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    setSettingsLoading(true);
    try {
      const res = await fetchTelemetrySettings(host, token);
      setSettings(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load telemetry settings");
    } finally {
      setSettingsLoading(false);
    }
  }, [host, token, telemetryCap, handleAuthFailure]);

  useEffect(() => {
    void refreshOverviewGroup();
  }, [refreshOverviewGroup]);

  useEffect(() => {
    void refreshTokens();
  }, [refreshTokens]);

  useEffect(() => {
    void refreshDrilldown();
  }, [refreshDrilldown]);

  useEffect(() => {
    void refreshSettings();
  }, [refreshSettings]);

  // The stored NL digest is independent of the page's active range/filter —
  // it's whatever the last weekly/on-demand generation covered — so this
  // doesn't depend on [range, filters]. A 404/409 (feature off or not yet
  // deployed) resolves to `null`, not an error (CONTRACT-NL.md §4).
  const refreshNLInsight = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    setNlLoading(true);
    try {
      const res = await fetchNLInsight(host, token);
      setNlResponse(res);
      // Reconcile the button/failed state to the server on every load.
      setNlGeneration(res?.generation ?? null);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      // Non-blocking: the NL card degrades to its empty state on failure.
    } finally {
      setNlLoading(false);
    }
  }, [host, token, telemetryCap, handleAuthFailure]);

  useEffect(() => {
    void refreshNLInsight();
  }, [refreshNLInsight]);

  // The instance snapshot is instance-wide, not range/filter-scoped, so this
  // does not depend on [range, filters]. The GET serves a cached snapshot and
  // revalidates off the request path; a telemetry_update frame refetches it.
  const refreshInstance = useCallback(async () => {
    if (!host || !token || telemetryCap !== "enabled") return;
    setInstanceLoading(true);
    try {
      const res = await fetchTelemetryInstance(host, token);
      setInstance(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      if (isTelemetryDisabledError(err)) {
        setTelemetryCap("disabled");
        return;
      }
      // Non-blocking: the panel degrades to its unavailable state on failure.
    } finally {
      setInstanceLoading(false);
    }
  }, [host, token, telemetryCap, handleAuthFailure]);

  useEffect(() => {
    void refreshInstance();
  }, [refreshInstance]);

  const handleRefreshInstance = useCallback(async () => {
    if (!host || !token) return;
    setInstanceRefreshing(true);
    try {
      const res = await refreshTelemetryInstance(host, token);
      setInstance(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to refresh instance health");
    } finally {
      setInstanceRefreshing(false);
    }
  }, [host, token, handleAuthFailure]);

  const handleDismissInstanceInsight = useCallback(
    async (insight: Insight) => {
      if (!host || !token) return;
      setDismissingSignature(insight.signature);
      try {
        await dismissTelemetryInsight(host, token, range, filters, insight.signature);
        setInstance((prev) =>
          prev
            ? {
                ...prev,
                insights: prev.insights.filter((i) => i.signature !== insight.signature),
              }
            : prev,
        );
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to dismiss recommendation");
      } finally {
        setDismissingSignature(null);
      }
    },
    [host, token, range, filters, handleAuthFailure],
  );

  const handleInstanceInsightFocus = useCallback(() => {
    document.getElementById("tm-instance-anchor")?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // Live refresh: re-fetch whenever the runtime reports telemetry facts
  // changed, debounced so a burst of ingested facts triggers one refetch.
  const latestRefreshersRef = useRef({
    overview: refreshOverviewGroup,
    tokens: refreshTokens,
    drilldown: refreshDrilldown,
    instance: refreshInstance,
  });
  useEffect(() => {
    latestRefreshersRef.current = {
      overview: refreshOverviewGroup,
      tokens: refreshTokens,
      drilldown: refreshDrilldown,
      instance: refreshInstance,
    };
  }, [refreshOverviewGroup, refreshTokens, refreshDrilldown, refreshInstance]);

  // The NL card is intentionally excluded from the generic telemetry_update
  // fan-out (its digest changes only on regeneration, not fact ingestion); it
  // rides the dedicated nl_insight_status frame instead. A ref keeps the WS
  // effect stable while pointing at the latest refresher.
  const refreshNLInsightRef = useRef(refreshNLInsight);
  useEffect(() => {
    refreshNLInsightRef.current = refreshNLInsight;
  }, [refreshNLInsight]);

  useEffect(() => {
    if (!host || !token || telemetryCap !== "enabled") return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function scheduleRefresh() {
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        if (!active) return;
        void latestRefreshersRef.current.overview();
        void latestRefreshersRef.current.tokens();
        void latestRefreshersRef.current.drilldown();
        void latestRefreshersRef.current.instance();
      }, REFRESH_DEBOUNCE_MS);
    }

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "telemetry_update") scheduleRefresh();
          if (message.type === "nl_insight_status") {
            const status = message.payload as unknown as NLGenerationStatus;
            setNlGeneration(status);
            // Settle-success persisted a new digest — refetch it (the card is
            // out of the generic refresher). Failure leaves the prior digest.
            if (status.status === "idle") void refreshNLInsightRef.current();
          }
          if (message.type === "auth_revoked") handleAuthFailure();
        },
        () => {
          if (active) handleAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
          },
          onClose: () => {
            if (!active) return;
            const delay = Math.min(15000, 500 * 2 ** attempt);
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }
    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      socket?.close();
    };
  }, [host, token, telemetryCap, handleAuthFailure]);

  const handleDismissInsight = useCallback(
    async (insight: Insight) => {
      if (!host || !token) return;
      setDismissingSignature(insight.signature);
      try {
        await dismissTelemetryInsight(host, token, range, filters, insight.signature);
        setInsights((prev) => prev.filter((item) => item.signature !== insight.signature));
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to dismiss insight");
      } finally {
        setDismissingSignature(null);
      }
    },
    [host, token, range, filters, handleAuthFailure],
  );

  const handleInsightClickThrough = useCallback((insight: Insight) => {
    const kindParam = insight.click_through.params?.kind;
    if (typeof kindParam === "string") {
      setDrilldownKind(kindParam as TelemetryFactKind);
      setDrilldownPage(1);
    }
    scrollToEvidenceSection(insight.click_through.endpoint);
  }, []);

  const handleGenerateNLInsight = useCallback(async () => {
    if (!host || !token) return;
    // Optimistic: reflect "Regenerating…" immediately, then reconcile to the
    // server's authoritative status (the run itself is detached — the settle
    // arrives over the WebSocket). The button stays server-driven on reload.
    setNlGeneration((prev) => ({
      status: "generating",
      generation_id: prev?.generation_id ?? null,
      requested_at: prev?.requested_at ?? null,
      range: prev?.range ?? null,
      filters: prev?.filters ?? null,
      error: null,
      settled_at: null,
    }));
    try {
      const ack = await generateNLInsight(host, token, range, filters);
      if (!ack) {
        setError("AI insights are off, or generation is not available yet.");
        setNlGeneration((prev) => (prev?.status === "generating" ? null : prev));
        return;
      }
      setNlGeneration(ack.generation);
      if (ack.requested_range_differs) {
        setError(
          "A regeneration over a different range is already running — " +
            "try again when it finishes.",
        );
      }
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to generate AI insight");
      setNlGeneration((prev) => (prev?.status === "generating" ? null : prev));
    }
  }, [host, token, range, filters, handleAuthFailure]);

  // `click_through` is a whitelisted-but-loosely-typed dict on the backend
  // (nl.py); read the same `{kind}` / `{params:{kind}}` shapes the
  // deterministic insights use rather than assuming one fixed layout.
  const handleNLEvidenceClick = useCallback((evidence: NLInsightEvidence) => {
    const clickThrough = evidence.click_through;
    const nestedParams = clickThrough.params;
    const kindParam =
      clickThrough.kind ??
      (nestedParams && typeof nestedParams === "object"
        ? (nestedParams as Record<string, unknown>).kind
        : undefined);
    if (typeof kindParam === "string") {
      setDrilldownKind(kindParam as TelemetryFactKind);
      setDrilldownPage(1);
    }
    const endpoint = typeof clickThrough.endpoint === "string" ? clickThrough.endpoint : undefined;
    scrollToEvidenceSection(endpoint);
  }, []);

  const handleDeleteTelemetry = useCallback(async () => {
    if (!host || !token) return;
    setDeleting(true);
    setError("");
    try {
      const result = await deleteTelemetry(host, token);
      setDeleteResult(result);
      await Promise.all([refreshOverviewGroup(), refreshTokens(), refreshDrilldown(), refreshSettings()]);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to delete telemetry");
    } finally {
      setDeleting(false);
    }
  }, [host, token, refreshOverviewGroup, refreshTokens, refreshDrilldown, refreshSettings, handleAuthFailure]);

  const backendOptions = catalog.ids().map((id) => ({ id, label: catalog.labelFor(id) }));
  const transportOptions = Array.from(
    new Set(catalog.ids().flatMap((id) => agentTransports(id, catalog))),
  );
  const effectiveRangeLabel = overview ? formatRangeLabel(overview.range) : null;

  return (
    <main className="page-shell">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            <Image
              src={theme === "light" ? "/waypoint-light.svg" : "/waypoint.svg"}
              alt=""
              width={38}
              height={38}
              priority
            />
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · telemetry</p>
            <h1 className="app-bar-title">Telemetry</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button className="error-banner-dismiss" onClick={() => setError("")} aria-label="Dismiss">
            ×
          </button>
        </div>
      ) : null}

      {telemetryCap === "unknown" ? (
        <section className="panel bordered board-empty">
          <h2>Loading telemetry…</h2>
        </section>
      ) : telemetryCap === "disabled" ? (
        <section className="panel bordered board-empty telemetry-disabled">
          <h2>Telemetry is disabled</h2>
          <p className="muted">
            The usage telemetry dashboard is opt-in. No usage facts are collected while it is off.
          </p>
          <p className="muted">
            To turn it on, add this to <code>backend/waypoint.yaml</code> and restart the backend:
          </p>
          <pre className="telemetry-disabled-snippet">
            <code>telemetry_enabled: true</code>
          </pre>
          <p className="muted">
            Live collection starts after the restart. It does not import earlier history by default —
            add <code>telemetry_backfill: true</code> for a one-time import of sessions that predate
            activation.
          </p>
          <div className="telemetry-disabled-actions">
            {deleteResult ? (
              <p className="muted" role="status">
                Removed {deleteResult.removed.facts} facts and {deleteResult.removed.rollups} rollups.
                Transcripts were not affected.
              </p>
            ) : (
              <button
                type="button"
                className="danger"
                disabled={deleting}
                onClick={() => void handleDeleteTelemetry()}
              >
                {deleting ? "Deleting…" : "Delete retained telemetry"}
              </button>
            )}
          </div>
        </section>
      ) : state === "error" && !overview ? (
        <section className="panel bordered board-empty">
          <h2>Couldn’t load telemetry</h2>
          <p className="muted">The backend didn’t respond. Check that Waypoint is running, then retry.</p>
          <button type="button" className="primary" onClick={() => void refreshOverviewGroup()}>
            Retry
          </button>
        </section>
      ) : (
        <>
          <TelemetryFilters
            range={range}
            filters={filters}
            onRangeChange={setRange}
            onFiltersChange={setFilters}
            backendOptions={backendOptions}
            transportOptions={transportOptions}
            effectiveRangeLabel={effectiveRangeLabel}
          />

          <OverviewCards overview={overview} loading={overviewLoading} />

          <NLInsightCard
            nlEnabled={settings?.nl_enabled ?? false}
            response={nlResponse}
            loading={nlLoading}
            generation={nlGeneration}
            onGenerate={() => void handleGenerateNLInsight()}
            onEvidenceClick={handleNLEvidenceClick}
          />

          <InsightCards
            insights={insights}
            loading={insightsLoading}
            dismissingSignature={dismissingSignature}
            onDismiss={(insight) => void handleDismissInsight(insight)}
            onClickThrough={handleInsightClickThrough}
          />

          <TokenChart
            tokens={tokens}
            loading={tokensLoading}
            groupBy={tokenGroupBy}
            onGroupByChange={setTokenGroupBy}
          />

          <ActivityChart activity={activity} loading={activityLoading} />

          <HealthPanel health={health} loading={healthLoading} />

          <InstanceHealthPanel
            instance={instance}
            loading={instanceLoading}
            refreshing={instanceRefreshing}
            onRefresh={() => void handleRefreshInstance()}
            dismissingSignature={dismissingSignature}
            onDismiss={(insight) => void handleDismissInstanceInsight(insight)}
            onInsightFocus={handleInstanceInsightFocus}
          />

          <div id="tm-drilldown-anchor" />
          <DrilldownPanel
            drilldown={drilldown}
            loading={drilldownLoading}
            kind={drilldownKind}
            onKindChange={(kind) => {
              setDrilldownKind(kind);
              setDrilldownPage(1);
            }}
            page={drilldownPage}
            onPageChange={setDrilldownPage}
            pageSize={DRILLDOWN_PAGE_SIZE}
          />

          <SettingsPanel
            settings={settings}
            loading={settingsLoading}
            deleting={deleting}
            deleteResult={deleteResult}
            onDelete={() => void handleDeleteTelemetry()}
          />
        </>
      )}
    </main>
  );
}
