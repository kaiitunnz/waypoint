"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { UsageInstrumentPanel } from "@/components/UsageInstrumentPanel";
import { humaniseBackend, useBackendCatalog, type BackendCatalog } from "@/lib/backends";
import {
  fetchUsageDashboard,
  isAuthError,
  refreshUsageDashboard,
} from "@/lib/api";
import { readUsageDashboardOpen, writeUsageDashboardOpen } from "@/lib/store";
import {
  Backend,
  SessionRecord,
  UsageDashboardBucket,
  UsageDashboardResponse,
} from "@/lib/types";
import {
  formatRelativeTime,
  rateLimitUsageTone,
  UsageTone,
} from "@/lib/usage";

interface UsageDashboardSectionProps {
  host: string;
  token: string;
  sessions: SessionRecord[];
  onAuthFailure?: () => void;
}

function deriveBucketsFromSessions(
  sessions: SessionRecord[],
  catalog?: BackendCatalog,
): UsageDashboardBucket[] {
  const buckets = new Map<string, UsageDashboardBucket>();
  for (const session of sessions) {
    const snapshot = session.rate_limit_usage;
    if (!snapshot) continue;
    const { key, label } = accountBucketKey(
      session.id,
      snapshot.source,
      snapshot.notes ?? [],
      catalog,
    );
    const existing = buckets.get(key);
    if (!existing) {
      buckets.set(key, {
        backend: snapshot.source,
        account_key: key,
        account_label: label,
        snapshot,
        session_ids: [session.id],
      });
      continue;
    }
    existing.session_ids.push(session.id);
    if (Date.parse(snapshot.updated_at) > Date.parse(existing.snapshot.updated_at)) {
      existing.snapshot = snapshot;
      existing.account_label = label;
    }
  }
  return Array.from(buckets.values()).sort(orderByToneThenBackend);
}

function accountBucketKey(
  sessionId: string,
  source: Backend,
  notes: string[],
  catalog?: BackendCatalog,
): { key: string; label: string } {
  if (source === "claude_code") {
    const org = findPrefixed(notes, "org: ");
    if (org) {
      const tier = findPrefixed(notes, "org tier: ");
      return { key: `claude_code:${org}`, label: tier ? `${org} · ${tier}` : org };
    }
  } else if (source === "codex") {
    const email = findEmail(notes);
    if (email) {
      const plan = findPrefixed(notes, "plan: ");
      return {
        key: `codex:${email}`,
        label: plan ? `${email} · plan: ${plan}` : email,
      };
    }
  }
  return {
    key: `${source}:session:${sessionId}`,
    label: humaniseBackend(source, catalog),
  };
}

function findPrefixed(notes: string[], prefix: string): string | null {
  for (const note of notes) {
    if (note.startsWith(prefix)) {
      const value = note.slice(prefix.length).trim();
      if (value) return value;
    }
  }
  return null;
}

function findEmail(notes: string[]): string | null {
  for (const note of notes) {
    if (note === "CLI OAuth" || note === "remote OAuth") continue;
    if (note.startsWith("plan: ")) continue;
    if (note.includes("@") && !note.includes(" ")) return note;
  }
  return null;
}

function toneRank(tone: UsageTone): number {
  if (tone === "danger") return 0;
  if (tone === "warn") return 1;
  return 2;
}

function orderByToneThenBackend(
  a: UsageDashboardBucket,
  b: UsageDashboardBucket,
): number {
  const aTone = toneRank(rateLimitUsageTone(a.snapshot));
  const bTone = toneRank(rateLimitUsageTone(b.snapshot));
  if (aTone !== bTone) return aTone - bTone;
  if (a.backend !== b.backend) return a.backend < b.backend ? -1 : 1;
  return Date.parse(b.snapshot.updated_at) - Date.parse(a.snapshot.updated_at);
}

function describeStatus(buckets: UsageDashboardBucket[]): {
  headline: string;
  detail: string;
  tone: UsageTone;
  freshest: string | null;
} {
  if (buckets.length === 0) {
    return {
      headline: "Standing by",
      detail: "No active accounts",
      tone: "good",
      freshest: null,
    };
  }
  let danger = 0;
  let warn = 0;
  let freshest: number | null = null;
  for (const bucket of buckets) {
    const tone = rateLimitUsageTone(bucket.snapshot);
    if (tone === "danger") danger += 1;
    else if (tone === "warn") warn += 1;
    const ts = Date.parse(bucket.snapshot.updated_at);
    if (Number.isFinite(ts) && (freshest === null || ts > freshest)) {
      freshest = ts;
    }
  }
  const freshestIso =
    freshest !== null ? new Date(freshest).toISOString() : null;
  if (danger > 0) {
    return {
      headline: "Quota critical",
      detail: `${danger} account${danger === 1 ? "" : "s"} above 90%`,
      tone: "danger",
      freshest: freshestIso,
    };
  }
  if (warn > 0) {
    return {
      headline: "Approaching limit",
      detail: `${warn} account${warn === 1 ? "" : "s"} above 70%`,
      tone: "warn",
      freshest: freshestIso,
    };
  }
  return {
    headline: "All clear",
    detail: `${buckets.length} account${buckets.length === 1 ? "" : "s"} nominal`,
    tone: "good",
    freshest: freshestIso,
  };
}

export function UsageDashboardSection({
  host,
  token,
  sessions,
  onAuthFailure,
}: UsageDashboardSectionProps) {
  const [open, setOpen] = useState(false);
  const [buckets, setBuckets] = useState<UsageDashboardBucket[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshingBucketId, setRefreshingBucketId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const catalog = useBackendCatalog(host || null, token || null, null);

  useEffect(() => {
    setOpen(readUsageDashboardOpen());
  }, []);

  useEffect(() => {
    setBuckets(deriveBucketsFromSessions(sessions, catalog));
  }, [sessions, catalog]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    fetchUsageDashboard(host, token)
      .then((response: UsageDashboardResponse) => {
        if (!active) return;
        const ordered = [...response.buckets].sort(orderByToneThenBackend);
        setBuckets(ordered);
      })
      .catch((fetchError) => {
        if (!active) return;
        if (isAuthError(fetchError)) {
          onAuthFailure?.();
          return;
        }
        setError(
          fetchError instanceof Error
            ? fetchError.message
            : "failed to fetch usage dashboard",
        );
      });
    return () => {
      active = false;
    };
  }, [host, token, onAuthFailure]);

  const toggleOpen = useCallback(() => {
    setOpen((current) => {
      const next = !current;
      writeUsageDashboardOpen(next);
      return next;
    });
  }, []);

  const handleRefreshAll = useCallback(async () => {
    setRefreshing(true);
    setError("");
    try {
      const response = await refreshUsageDashboard(host, token);
      setBuckets([...response.buckets].sort(orderByToneThenBackend));
    } catch (refreshError) {
      if (isAuthError(refreshError)) {
        onAuthFailure?.();
        return;
      }
      setError(
        refreshError instanceof Error
          ? refreshError.message
          : "failed to refresh usage",
      );
    } finally {
      setRefreshing(false);
    }
  }, [host, token, onAuthFailure]);

  const handleRefreshBucket = useCallback(
    async (bucket: UsageDashboardBucket) => {
      setRefreshingBucketId(bucket.account_key);
      setError("");
      try {
        const response = await refreshUsageDashboard(host, token);
        setBuckets([...response.buckets].sort(orderByToneThenBackend));
      } catch (refreshError) {
        if (isAuthError(refreshError)) {
          onAuthFailure?.();
          return;
        }
        setError(
          refreshError instanceof Error
            ? refreshError.message
            : "failed to refresh usage",
        );
      } finally {
        setRefreshingBucketId(null);
      }
    },
    [host, token, onAuthFailure],
  );

  const status = useMemo(() => describeStatus(buckets ?? []), [buckets]);

  const summaryLine = useMemo(() => {
    if (buckets === null) return "Loading…";
    if (buckets.length === 0) return "Standing by";
    const pieces = [`${buckets.length} account${buckets.length === 1 ? "" : "s"}`];
    if (status.tone !== "good") pieces.push(status.detail.toLowerCase());
    return pieces.join(" · ");
  }, [buckets, status]);

  return (
    <section
      className={`panel stack usage-deck tone-${status.tone}${open ? " is-open" : ""}`}
      aria-label="Telemetry"
    >
      <button
        type="button"
        className="usage-deck-toggle"
        onClick={toggleOpen}
        aria-expanded={open}
      >
        <span className="usage-deck-toggle-mark" aria-hidden>
          <span className="usage-deck-toggle-needle" />
        </span>
        <span className="usage-deck-toggle-titles">
          <span className="usage-deck-toggle-eyebrow">Telemetry</span>
          <span className="usage-deck-toggle-title">{status.headline}</span>
        </span>
        <span className="usage-deck-toggle-summary">{summaryLine}</span>
        <span className="usage-deck-toggle-pips" aria-hidden>
          {(buckets ?? []).slice(0, 6).map((bucket) => (
            <span
              key={bucket.account_key}
              className={`usage-deck-pip tone-${rateLimitUsageTone(bucket.snapshot)}`}
            />
          ))}
          {(buckets?.length ?? 0) > 6 ? (
            <span className="usage-deck-pip-extra">+{(buckets?.length ?? 0) - 6}</span>
          ) : null}
        </span>
        <span className="usage-deck-toggle-chevron" aria-hidden />
      </button>

      {open ? (
        <div className="usage-deck-body">
          <div className="usage-deck-status">
            <span className="usage-deck-status-rivet" aria-hidden />
            <div className="usage-deck-status-text">
              <span className={`usage-deck-status-headline tone-${status.tone}`}>
                {status.headline}
              </span>
              <span className="usage-deck-status-detail">{status.detail}</span>
            </div>
            <div className="usage-deck-status-actions">
              {status.freshest ? (
                <span
                  className="usage-deck-status-sweep"
                  title={new Date(status.freshest).toLocaleString()}
                >
                  Last sweep · {formatRelativeTime(status.freshest)}
                </span>
              ) : null}
              <button
                type="button"
                className="usage-deck-refresh-all"
                onClick={() => void handleRefreshAll()}
                disabled={
                  refreshing || buckets === null || buckets.length === 0
                }
              >
                <span
                  aria-hidden
                  className={`usage-deck-refresh-all-glyph${refreshing ? " is-spinning" : ""}`}
                >
                  ↻
                </span>
                {refreshing ? "Sweeping" : "Sweep all"}
              </button>
            </div>
          </div>

          {error ? (
            <p className="usage-deck-error" role="alert">
              {error}
            </p>
          ) : null}

          {buckets === null ? (
            <div className="usage-deck-loading">
              <span className="usage-deck-loading-bar" aria-hidden />
              <span className="muted">Calibrating instruments…</span>
            </div>
          ) : buckets.length === 0 ? (
            <div className="usage-deck-empty">
              <span className="usage-deck-empty-mark" aria-hidden>
                ⌖
              </span>
              <p className="usage-deck-empty-title">No accounts in range</p>
              <p className="usage-deck-empty-sub">
                Start a Claude Code or Codex session to begin telemetry.
              </p>
            </div>
          ) : (
            <div className="usage-deck-grid" data-bucket-count={buckets.length}>
              {buckets.map((bucket, i) => (
                <UsageInstrumentPanel
                  key={bucket.account_key}
                  bucket={bucket}
                  catalog={catalog}
                  emphasis={i === 0 && buckets.length > 1 ? "primary" : "secondary"}
                  index={i}
                  onRefresh={() => handleRefreshBucket(bucket)}
                  refreshing={refreshingBucketId === bucket.account_key}
                />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
