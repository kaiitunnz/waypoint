"use client";

import { useCallback, useEffect, useState } from "react";

import { UsageReadout } from "@/components/UsageReadout";
import { humaniseBackend } from "@/lib/backends";
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
import { rateLimitUsageTone } from "@/lib/usage";

interface UsageDashboardSectionProps {
  host: string;
  token: string;
  sessions: SessionRecord[];
  onAuthFailure?: () => void;
}

function deriveBucketsFromSessions(
  sessions: SessionRecord[],
): UsageDashboardBucket[] {
  const buckets = new Map<string, UsageDashboardBucket>();
  for (const session of sessions) {
    const snapshot = session.rate_limit_usage;
    if (!snapshot) continue;
    const { key, label } = accountBucketKey(session.id, snapshot.source, snapshot.notes ?? []);
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
  return Array.from(buckets.values()).sort((a, b) => {
    if (a.backend !== b.backend) return a.backend < b.backend ? -1 : 1;
    return Date.parse(b.snapshot.updated_at) - Date.parse(a.snapshot.updated_at);
  });
}

function accountBucketKey(
  sessionId: string,
  source: Backend,
  notes: string[],
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
    label: humaniseBackend(source),
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

function bucketSourceLabel(bucket: UsageDashboardBucket): string {
  const notes = bucket.snapshot.notes ?? [];
  if (notes.length > 0) return notes.join(" · ");
  return humaniseBackend(bucket.backend);
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

  useEffect(() => {
    setOpen(readUsageDashboardOpen());
  }, []);

  // WS sessions stream is the source of truth — re-derive on every change so
  // the dashboard stays in lockstep without an extra round-trip.
  useEffect(() => {
    setBuckets(deriveBucketsFromSessions(sessions));
  }, [sessions]);

  // First-load hydration: hit /api/usage so the buckets show up even before
  // the sessions WS has settled. Same shape comes back so it merges cleanly.
  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    fetchUsageDashboard(host, token)
      .then((response: UsageDashboardResponse) => {
        if (!active) return;
        setBuckets(response.buckets);
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
      setBuckets(response.buckets);
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
        // Refresh-all is the only endpoint we have; it covers every bucket
        // in one shot, so a per-card refresh fires the same call. The WS
        // push merges the result back via `sessions` above.
        const response = await refreshUsageDashboard(host, token);
        setBuckets(response.buckets);
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

  const groupedByBackend = new Map<Backend, UsageDashboardBucket[]>();
  for (const bucket of buckets ?? []) {
    const list = groupedByBackend.get(bucket.backend) ?? [];
    list.push(bucket);
    groupedByBackend.set(bucket.backend, list);
  }

  return (
    <section className={`panel stack usage-dashboard${open ? " open" : ""}`}>
      <button
        type="button"
        className="usage-dashboard-toggle"
        onClick={toggleOpen}
        aria-expanded={open}
      >
        <span className="usage-dashboard-toggle-eyebrow">Usage</span>
        <span className="usage-dashboard-toggle-summary">
          {buckets === null
            ? "Loading…"
            : buckets.length === 0
              ? "No rate-limit data yet"
              : `${buckets.length} account${buckets.length === 1 ? "" : "s"}`}
        </span>
        <span className="usage-dashboard-toggle-chevron" aria-hidden="true" />
      </button>
      {open ? (
        <div className="usage-dashboard-body">
          <div className="usage-dashboard-actions">
            <button
              type="button"
              className="usage-refresh"
              onClick={() => void handleRefreshAll()}
              disabled={refreshing || buckets === null || buckets.length === 0}
              aria-label="Refresh all rate limits"
            >
              <span
                aria-hidden
                className={`usage-refresh-glyph${refreshing ? " is-spinning" : ""}`}
              >
                ↻
              </span>
              {refreshing ? "Refreshing" : "Refresh all"}
            </button>
          </div>
          {error ? (
            <p className="usage-dashboard-error" role="alert">
              {error}
            </p>
          ) : null}
          {buckets === null ? (
            <p className="muted">Loading rate-limit usage…</p>
          ) : buckets.length === 0 ? (
            <p className="muted">
              No rate-limit data yet — start a Claude Code or Codex session.
            </p>
          ) : (
            <div className="usage-dashboard-grid">
              {Array.from(groupedByBackend.entries()).map(([backend, list]) => (
                <div key={backend} className="usage-dashboard-group">
                  <h2 className="usage-dashboard-group-title">
                    {humaniseBackend(backend)}
                  </h2>
                  <div className="usage-dashboard-cards">
                    {list.map((bucket) => {
                      const tone = rateLimitUsageTone(bucket.snapshot);
                      return (
                        <article
                          key={bucket.account_key}
                          className={`usage-dashboard-card usage-panel tone-${tone}`}
                        >
                          <span className="usage-panel-rail" aria-hidden="true" />
                          <UsageReadout
                            usage={bucket.snapshot}
                            headerLabel={bucket.account_label}
                            headerEyebrow={`${bucket.session_ids.length} session${bucket.session_ids.length === 1 ? "" : "s"}`}
                            sourceLabel={bucketSourceLabel(bucket)}
                            onRefresh={() => handleRefreshBucket(bucket)}
                            refreshing={refreshingBucketId === bucket.account_key}
                          />
                        </article>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
