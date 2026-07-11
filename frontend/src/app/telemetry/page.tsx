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
  fetchTelemetryOverview,
  fetchTelemetrySettings,
  fetchTelemetryTokens,
  isAuthError,
} from "@/lib/api";
import { agentTransports, useBackendCatalog } from "@/lib/backends";
import { clearToken, readHost, readToken } from "@/lib/store";
import { formatRangeLabel, readTelemetryQuery, writeTelemetryQuery } from "@/lib/telemetry";
import { useTheme } from "@/lib/theme";
import {
  Insight,
  SessionEnvelope,
  TelemetryActivity,
  TelemetryDeleteResponse,
  TelemetryDrilldown,
  TelemetryFactKind,
  TelemetryHealth,
  TelemetryOverview,
  TelemetrySettingsResponse,
  TelemetryTokens,
  TokenGroupBy,
} from "@/lib/types";

const DRILLDOWN_PAGE_SIZE = 25;
const REFRESH_DEBOUNCE_MS = 400;

type LoadState = "loading" | "ready" | "error";

export default function TelemetryPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");

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

  const catalog = useBackendCatalog(host || null, token || null, null);

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

  const refreshOverviewGroup = useCallback(async () => {
    if (!host || !token) return;
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
      setState((current) => (current === "ready" ? current : "error"));
      setError(err instanceof Error ? err.message : "failed to load telemetry");
    } finally {
      setOverviewLoading(false);
      setActivityLoading(false);
      setHealthLoading(false);
      setInsightsLoading(false);
    }
  }, [host, token, range, filters, handleAuthFailure]);

  const refreshTokens = useCallback(async () => {
    if (!host || !token) return;
    setTokensLoading(true);
    try {
      const res = await fetchTelemetryTokens(host, token, range, filters, tokenGroupBy);
      setTokens(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load token usage");
    } finally {
      setTokensLoading(false);
    }
  }, [host, token, range, filters, tokenGroupBy, handleAuthFailure]);

  const refreshDrilldown = useCallback(async () => {
    if (!host || !token) return;
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
      setDrilldown(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load drilldown");
    } finally {
      setDrilldownLoading(false);
    }
  }, [host, token, range, filters, drilldownKind, drilldownPage, handleAuthFailure]);

  const refreshSettings = useCallback(async () => {
    if (!host || !token) return;
    setSettingsLoading(true);
    try {
      const res = await fetchTelemetrySettings(host, token);
      setSettings(res);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load telemetry settings");
    } finally {
      setSettingsLoading(false);
    }
  }, [host, token, handleAuthFailure]);

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

  // Live refresh: re-fetch whenever the runtime reports telemetry facts
  // changed, debounced so a burst of ingested facts triggers one refetch.
  const latestRefreshersRef = useRef({
    overview: refreshOverviewGroup,
    tokens: refreshTokens,
    drilldown: refreshDrilldown,
  });
  useEffect(() => {
    latestRefreshersRef.current = {
      overview: refreshOverviewGroup,
      tokens: refreshTokens,
      drilldown: refreshDrilldown,
    };
  }, [refreshOverviewGroup, refreshTokens, refreshDrilldown]);

  useEffect(() => {
    if (!host || !token) return;
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
      }, REFRESH_DEBOUNCE_MS);
    }

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "telemetry_update") scheduleRefresh();
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
  }, [host, token, handleAuthFailure]);

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
    document.getElementById("tm-drilldown-anchor")?.scrollIntoView({ behavior: "smooth" });
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

      {state === "error" && !overview ? (
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
