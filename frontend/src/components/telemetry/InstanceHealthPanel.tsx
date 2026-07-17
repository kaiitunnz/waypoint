"use client";

import { Fragment, useCallback, useMemo, useState } from "react";

import {
  DatabaseContentModel,
  DatabaseCountRow,
  deriveDatabaseContent,
  formatBytes,
  formatExactBytes,
  INSTANCE_CATEGORY_ORDER,
  instanceCategoryColor,
  instanceCategoryLabel,
  insightSeverityTone,
} from "@/lib/telemetry";
import {
  CategoryFootprint,
  DatabaseReclaim,
  Insight,
  InstanceDataQuality,
  InstanceHistoryPoint,
  InstanceSnapshot,
  TelemetryInstance,
} from "@/lib/types";

const DB_DETAILS_REGION_ID = "tm-inst-db-details";
const DB_DETAILS_HEADING_ID = "tm-inst-db-details-heading";
const DB_DETAILS_TOGGLE_ID = "tm-inst-db-details-toggle";
const DB_EVENT_MIX_ID = "tm-inst-db-event-mix";
const DB_EVENT_MIX_COLLAPSED_LIMIT = 4;

const DB_CONTENT_UNAVAILABLE =
  "Database content details are unavailable because the database could not be queried read-only.";
const DB_RECORD_COUNT_NOTE =
  "Record counts explain database contents; they do not divide the database file size.";

interface InstanceHealthPanelProps {
  instance: TelemetryInstance | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  dismissingSignature: string | null;
  onDismiss: (insight: Insight) => void;
  onInsightFocus: (focus: string | undefined) => void;
}

const QUALITY_META: Record<
  InstanceDataQuality | "stale",
  { glyph: string; label: string; tone: string }
> = {
  complete: { glyph: "●", label: "Current", tone: "good" },
  partial: { glyph: "◐", label: "Partial", tone: "warn" },
  unavailable: { glyph: "○", label: "Unavailable", tone: "danger" },
  stale: { glyph: "↻", label: "Stale", tone: "warn" },
};

function severityGlyph(severity: Insight["severity"]): string {
  if (severity === "critical") return "▲";
  if (severity === "warning") return "■";
  return "●";
}

function formatObservedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown time";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ByteValue({ bytes }: { bytes: number }) {
  return (
    <span title={formatExactBytes(bytes)} aria-label={formatExactBytes(bytes)}>
      {formatBytes(bytes)}
    </span>
  );
}

function ProportionBar({ categories, total }: { categories: CategoryFootprint[]; total: number }) {
  if (total <= 0) {
    return <div className="tm-inst-bar tm-inst-bar-empty" aria-hidden="true" />;
  }
  const ordered = INSTANCE_CATEGORY_ORDER.map((cat) =>
    categories.find((c) => c.category === cat),
  ).filter((c): c is CategoryFootprint => c !== undefined && c.bytes > 0);
  return (
    <div className="tm-inst-bar" role="img" aria-label="Storage category proportions">
      {ordered.map((cat) => {
        const pct = (cat.bytes / total) * 100;
        return (
          <span
            key={cat.category}
            className="tm-inst-bar-seg"
            style={{ width: `${pct}%`, background: instanceCategoryColor(cat.category) }}
            title={`${instanceCategoryLabel(cat.category)}: ${formatExactBytes(cat.bytes)} (${pct.toFixed(1)}%)`}
          />
        );
      })}
    </div>
  );
}

function CountRows({ rows }: { rows: DatabaseCountRow[] }) {
  return (
    <dl className="tm-inst-db-dl">
      {rows.map((row) => (
        <div key={row.key} className="tm-inst-db-item">
          <dt>{row.label}</dt>
          <dd>{row.count.toLocaleString("en-US")}</dd>
        </div>
      ))}
    </dl>
  );
}

function EventMix({
  content,
  expanded,
  onToggle,
}: {
  content: DatabaseContentModel;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (content.eventMix.length === 0) return null;
  const collapsible = content.eventMix.length > DB_EVENT_MIX_COLLAPSED_LIMIT;
  const visible =
    collapsible && !expanded
      ? content.eventMix.slice(0, DB_EVENT_MIX_COLLAPSED_LIMIT)
      : content.eventMix;
  return (
    <div className="tm-inst-db-eventmix">
      <div className="tm-inst-db-eventmix-head">
        <span className="tm-inst-db-subheading">Event mix</span>
        {collapsible ? (
          <button
            type="button"
            className="tm-inst-db-toggle tm-inst-db-toggle-sm"
            aria-expanded={expanded}
            aria-controls={DB_EVENT_MIX_ID}
            onClick={onToggle}
          >
            <span className="tm-inst-db-caret" aria-hidden="true">
              {expanded ? "▾" : "▸"}
            </span>
            {expanded ? "Show fewer" : `Show all ${content.eventMix.length}`}
          </button>
        ) : null}
      </div>
      <ul id={DB_EVENT_MIX_ID} className="tm-inst-db-kinds">
        {visible.map((kind) => (
          <li key={kind.kind} className="tm-inst-db-kind">
            <span className="tm-inst-db-kind-label">{kind.label}</span>
            <span className="tm-inst-db-kind-count">{kind.count.toLocaleString("en-US")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DatabaseDetails({
  bytes,
  categoryUnavailable,
  database,
  content,
  tableRowsEmpty,
  eventMixExpanded,
  onToggleEventMix,
}: {
  bytes: number;
  categoryUnavailable: boolean;
  database: DatabaseReclaim;
  content: DatabaseContentModel;
  tableRowsEmpty: boolean;
  eventMixExpanded: boolean;
  onToggleEventMix: () => void;
}) {
  const contentUnavailable = tableRowsEmpty && !database.measured;
  return (
    <section id={DB_DETAILS_REGION_ID} className="tm-inst-db" aria-labelledby={DB_DETAILS_HEADING_ID}>
      <h4 id={DB_DETAILS_HEADING_ID} className="tm-inst-db-title">
        Database details
      </h4>
      {categoryUnavailable ? (
        <p className="tm-inst-db-unavail muted">{DB_CONTENT_UNAVAILABLE}</p>
      ) : (
        <>
          <p className="tm-inst-db-note muted">{DB_RECORD_COUNT_NOTE}</p>
          <div className="tm-inst-db-groups">
            <div className="tm-inst-db-group">
              <h5 className="tm-inst-db-heading">Storage</h5>
              <dl className="tm-inst-db-dl">
                <div className="tm-inst-db-item">
                  <dt>Database file</dt>
                  <dd>
                    <ByteValue bytes={bytes} />
                  </dd>
                </div>
                {database.measured ? (
                  <div className="tm-inst-db-item">
                    <dt>Reclaimable free pages</dt>
                    <dd>
                      <ByteValue bytes={database.free_bytes} /> (
                      {(database.free_percent * 100).toFixed(0)}%)
                    </dd>
                  </div>
                ) : null}
              </dl>
            </div>

            {content.hasContent ? (
              <>
                {content.session.length > 0 ? (
                  <div className="tm-inst-db-group">
                    <h5 className="tm-inst-db-heading">Session data</h5>
                    <CountRows rows={content.session} />
                    <EventMix
                      content={content}
                      expanded={eventMixExpanded}
                      onToggle={onToggleEventMix}
                    />
                  </div>
                ) : null}

                {content.telemetry.length > 0 ? (
                  <div className="tm-inst-db-group">
                    <h5 className="tm-inst-db-heading">Telemetry data</h5>
                    <CountRows rows={content.telemetry} />
                  </div>
                ) : null}

                {content.otherManagedRecords !== null ? (
                  <div className="tm-inst-db-group">
                    <h5 className="tm-inst-db-heading">Other managed records</h5>
                    <p className="tm-inst-db-other">
                      {content.otherManagedRecords.toLocaleString("en-US")}{" "}
                      {content.otherManagedRecords === 1 ? "record" : "records"}
                    </p>
                  </div>
                ) : null}
              </>
            ) : contentUnavailable ? (
              <p className="tm-inst-db-unavail muted">{DB_CONTENT_UNAVAILABLE}</p>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}

function CategoryTable({
  snapshot,
  detailsExpanded,
  onToggleDetails,
  eventMixExpanded,
  onToggleEventMix,
}: {
  snapshot: InstanceSnapshot;
  detailsExpanded: boolean;
  onToggleDetails: () => void;
  eventMixExpanded: boolean;
  onToggleEventMix: () => void;
}) {
  const ordered = INSTANCE_CATEGORY_ORDER.map((cat) =>
    snapshot.categories.find((c) => c.category === cat),
  ).filter((c): c is CategoryFootprint => c !== undefined);
  const content = useMemo(
    () => deriveDatabaseContent(snapshot.counts.table_rows, snapshot.counts.events_by_kind),
    [snapshot.counts.table_rows, snapshot.counts.events_by_kind],
  );
  const tableRowsEmpty = Object.keys(snapshot.counts.table_rows).length === 0;
  return (
    <table className="tm-inst-table">
      <thead>
        <tr>
          <th scope="col">Category</th>
          <th scope="col">Size</th>
          <th scope="col" title="Regular files counted in this category (not directories)">
            Files
          </th>
        </tr>
      </thead>
      <tbody>
        {ordered.map((cat) => {
          const isDatabase = cat.category === "database";
          return (
            <Fragment key={cat.category}>
              <tr>
                <th scope="row">
                  <span
                    className="tm-inst-swatch"
                    aria-hidden="true"
                    style={{ background: instanceCategoryColor(cat.category) }}
                  />
                  {instanceCategoryLabel(cat.category)}
                  {cat.unavailable ? (
                    <span className="tm-inst-tag">unavailable</span>
                  ) : cat.partial ? (
                    <span className="tm-inst-tag">partial</span>
                  ) : null}
                  {isDatabase ? (
                    <button
                      type="button"
                      id={DB_DETAILS_TOGGLE_ID}
                      className="tm-inst-db-toggle"
                      aria-expanded={detailsExpanded}
                      aria-controls={DB_DETAILS_REGION_ID}
                      onClick={onToggleDetails}
                    >
                      Details
                      <span className="tm-inst-db-caret" aria-hidden="true">
                        {detailsExpanded ? "▾" : "›"}
                      </span>
                    </button>
                  ) : null}
                </th>
                <td>
                  <ByteValue bytes={cat.bytes} />
                </td>
                <td className="tm-inst-num">{cat.entry_count.toLocaleString("en-US")}</td>
              </tr>
              {isDatabase && detailsExpanded ? (
                <tr className="tm-inst-db-row">
                  <td colSpan={3} className="tm-inst-db-cell">
                    <DatabaseDetails
                      bytes={cat.bytes}
                      categoryUnavailable={cat.unavailable}
                      database={snapshot.database}
                      content={content}
                      tableRowsEmpty={tableRowsEmpty}
                      eventMixExpanded={eventMixExpanded}
                      onToggleEventMix={onToggleEventMix}
                    />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function HistoryTrend({ history }: { history: InstanceHistoryPoint[] }) {
  const points = history.filter((p) => p.data_quality !== "unavailable");
  if (points.length < 2) {
    return (
      <p className="tm-inst-trend-empty muted">
        A daily trend appears after a second day of history.
      </p>
    );
  }
  const width = 320;
  const height = 56;
  const pad = 4;
  const values = points.map((p) => p.total_bytes);
  const max = Math.max(...values, 1);
  const min = Math.min(...values);
  const span = Math.max(max - min, 1);
  const stepX = (width - pad * 2) / (points.length - 1);
  const coords = points.map((p, i) => {
    const x = pad + i * stepX;
    const y = height - pad - ((p.total_bytes - min) / span) * (height - pad * 2);
    return { x, y, point: p };
  });
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const area = `${pad},${height - pad} ${line} ${(width - pad).toFixed(1)},${height - pad}`;
  const latest = points[points.length - 1];
  return (
    <div className="tm-inst-trend">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="tm-inst-spark"
        role="img"
        aria-label={`Daily footprint trend, latest ${formatBytes(latest.total_bytes)} across ${points.length} days`}
        preserveAspectRatio="none"
      >
        <polygon points={area} className="tm-inst-spark-area" />
        <polyline points={line} className="tm-inst-spark-line" />
        {coords.map((c) => (
          <circle
            key={c.point.day}
            cx={c.x}
            cy={c.y}
            r={c.point.data_quality === "partial" ? 2.4 : 1.8}
            className={
              c.point.data_quality === "partial"
                ? "tm-inst-spark-dot is-partial"
                : "tm-inst-spark-dot"
            }
          >
            <title>{`${c.point.day}: ${formatBytes(c.point.total_bytes)}${
              c.point.data_quality === "partial" ? " (partial)" : ""
            }`}</title>
          </circle>
        ))}
      </svg>
      <p className="tm-inst-trend-caption muted">
        {points.length} day{points.length === 1 ? "" : "s"} · latest {formatBytes(latest.total_bytes)}
      </p>
    </div>
  );
}

function MaintenanceCards({
  insights,
  dismissingSignature,
  onDismiss,
  onFocus,
}: {
  insights: Insight[];
  dismissingSignature: string | null;
  onDismiss: (insight: Insight) => void;
  onFocus: (focus: string | undefined) => void;
}) {
  if (insights.length === 0) return null;
  return (
    <section className="tm-insight-grid" aria-label="Maintenance recommendations">
      {insights.map((insight) => {
        const tone = insightSeverityTone(insight.severity);
        const focus =
          typeof insight.click_through.params?.focus === "string"
            ? (insight.click_through.params.focus as string)
            : undefined;
        return (
          <article
            key={insight.signature}
            className={`panel tm-insight-card tm-inst-card tone-${tone}`}
          >
            <header className="tm-insight-head">
              <span className="tm-insight-glyph" aria-hidden="true">
                {severityGlyph(insight.severity)}
              </span>
              <span className="tm-insight-severity">{insight.severity}</span>
              {insight.observed_at ? (
                <span className="tm-inst-card-time">{formatObservedAt(insight.observed_at)}</span>
              ) : null}
              <button
                type="button"
                className="tm-insight-dismiss"
                onClick={() => onDismiss(insight)}
                disabled={dismissingSignature === insight.signature}
                aria-label="Dismiss recommendation"
              >
                {dismissingSignature === insight.signature ? "…" : "×"}
              </button>
            </header>
            <p className="tm-insight-statement">{insight.statement}</p>
            {insight.safety_note ? (
              <p className="tm-inst-safety">
                <span className="tm-inst-safety-label" aria-hidden="true">
                  ⚑ safe next step
                </span>
                {insight.safety_note}
              </p>
            ) : null}
            <button
              type="button"
              className="tm-insight-evidence"
              onClick={() => onFocus(focus)}
            >
              View evidence →
            </button>
          </article>
        );
      })}
    </section>
  );
}

export function InstanceHealthPanel({
  instance,
  loading,
  refreshing,
  onRefresh,
  dismissingSignature,
  onDismiss,
  onInsightFocus,
}: InstanceHealthPanelProps) {
  const snapshot = instance?.snapshot ?? null;
  const quality: InstanceDataQuality | "stale" = instance?.stale
    ? "stale"
    : (snapshot?.data_quality ?? "unavailable");
  const meta = QUALITY_META[quality];

  const [databaseDetailsExpanded, setDatabaseDetailsExpanded] = useState(false);
  const [eventMixExpanded, setEventMixExpanded] = useState(false);

  // A database-vacuum maintenance card focuses "database"; open the details so
  // the reclaimability metric is visible without a second click, then let the
  // parent perform its existing scroll.
  const handleInsightFocus = useCallback(
    (focus: string | undefined) => {
      if (focus === "database") setDatabaseDetailsExpanded(true);
      onInsightFocus(focus);
    },
    [onInsightFocus],
  );

  const totalCount = useMemo(() => {
    if (!snapshot) return 0;
    return snapshot.categories.reduce((sum, c) => sum + c.entry_count, 0);
  }, [snapshot]);

  if (loading && !instance) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }

  return (
    <section id="tm-instance-anchor" className="panel tm-chart-card tm-inst-panel" aria-label="Instance health and capacity">
      <header className="tm-inst-header">
        <div>
          <p className="tm-inst-eyebrow">Waypoint · instance</p>
          <h2 className="tm-inst-title">Instance health &amp; capacity</h2>
        </div>
        <div className="tm-inst-header-meta">
          <span className={`tm-inst-status tone-${meta.tone}`}>
            <span className="tm-inst-status-glyph" aria-hidden="true">
              {meta.glyph}
            </span>
            {meta.label}
          </span>
          <button
            type="button"
            className="tm-inst-refresh"
            onClick={onRefresh}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing…" : "↻ Refresh"}
          </button>
        </div>
      </header>

      {snapshot && snapshot.data_quality !== "unavailable" ? (
        <p className="tm-inst-observed muted">
          Observed {formatObservedAt(snapshot.observed_at)}
          {instance?.stale && instance.stale_reason ? ` · ${instance.stale_reason}` : ""}
        </p>
      ) : null}

      {!snapshot || snapshot.data_quality === "unavailable" ? (
        <div className="tm-inst-unavailable">
          <p className="muted">
            No instance snapshot is available yet.{" "}
            {instance?.stale_reason ?? "Try refreshing, or check back after the next collection."}
          </p>
          {instance?.cli_note ? <p className="tm-inst-clinote muted">{instance.cli_note}</p> : null}
        </div>
      ) : (
        <>
          <div className="tm-inst-hero">
            <div className="tm-inst-total">
              <span className="tm-inst-total-value">
                <ByteValue bytes={snapshot.total_bytes} />
              </span>
              <span className="tm-inst-total-label">managed storage · {totalCount.toLocaleString("en-US")} files</span>
            </div>
          </div>

          <ProportionBar categories={snapshot.categories} total={snapshot.total_bytes} />
          <CategoryTable
            snapshot={snapshot}
            detailsExpanded={databaseDetailsExpanded}
            onToggleDetails={() => setDatabaseDetailsExpanded((prev) => !prev)}
            eventMixExpanded={eventMixExpanded}
            onToggleEventMix={() => setEventMixExpanded((prev) => !prev)}
          />

          {snapshot.structured_logs.length > 0 || snapshot.redundant_logs.count > 0 ? (
            <div className="tm-inst-overlays">
              <p className="tm-inst-overlay-eyebrow">
                Already counted above — not added to the total
              </p>
              {snapshot.structured_logs.map((log) => (
                <span key={log.tree} className="tm-inst-overlay">
                  {log.tree === "orphan_sessions" ? "Orphan" : "Live"}-tree structured logs:{" "}
                  <ByteValue bytes={log.bytes} /> ({log.count}{" "}
                  {log.count === 1 ? "file" : "files"})
                </span>
              ))}
              {snapshot.redundant_logs.count > 0 ? (
                <span className="tm-inst-overlay">
                  Redundant-log cleanup candidates:{" "}
                  <ByteValue bytes={snapshot.redundant_logs.bytes} /> (
                  {snapshot.redundant_logs.count})
                  {snapshot.redundant_logs.orphan_overlap_count > 0
                    ? ` · ${snapshot.redundant_logs.orphan_overlap_count} of them inside orphan dirs`
                    : ""}
                  {snapshot.redundant_logs.running_excluded_count > 0
                    ? ` · ${snapshot.redundant_logs.running_excluded_count} running excluded`
                    : ""}
                </span>
              ) : null}
            </div>
          ) : null}

          <div className="tm-inst-facts">
            <div className="tm-inst-fact">
              <span className="tm-inst-fact-label" title="Session directories on disk">
                Session dirs
              </span>
              <span className="tm-inst-fact-value">{snapshot.counts.session_dir_count}</span>
            </div>
            <div className="tm-inst-fact">
              <span
                className="tm-inst-fact-label"
                title="Session directories with no matching stored session"
              >
                Orphan dirs
              </span>
              <span className="tm-inst-fact-value">{snapshot.counts.orphan_dir_count}</span>
            </div>
            <div className="tm-inst-fact">
              <span className="tm-inst-fact-label">Attachments</span>
              <span className="tm-inst-fact-value">{snapshot.counts.attachment_count}</span>
            </div>
            <div className="tm-inst-fact">
              <span
                className="tm-inst-fact-label"
                title="Write-ahead log size — part of SQLite companions above"
              >
                WAL (in companions)
              </span>
              <span className="tm-inst-fact-value">
                <ByteValue bytes={snapshot.wal_bytes} />
              </span>
            </div>
            {snapshot.database.measured ? (
              <div className="tm-inst-fact">
                <span
                  className="tm-inst-fact-label"
                  title="Reusable free space inside the database file (reclaimable by VACUUM)"
                >
                  DB reclaimable
                </span>
                <span className="tm-inst-fact-value">
                  <ByteValue bytes={snapshot.database.free_bytes} /> (
                  {(snapshot.database.free_percent * 100).toFixed(0)}%)
                </span>
              </div>
            ) : null}
            {snapshot.filesystem.measured ? (
              <div className="tm-inst-fact">
                <span className="tm-inst-fact-label">Volume free</span>
                <span className="tm-inst-fact-value">
                  <ByteValue bytes={snapshot.filesystem.free_bytes} />
                </span>
              </div>
            ) : null}
          </div>

          {snapshot.notes.length > 0 ? (
            <ul className="tm-inst-notes">
              {snapshot.notes.map((note) => (
                <li key={note} className="muted">
                  {note}
                </li>
              ))}
            </ul>
          ) : null}

          <MaintenanceCards
            insights={instance?.insights ?? []}
            dismissingSignature={dismissingSignature}
            onDismiss={onDismiss}
            onFocus={handleInsightFocus}
          />

          <HistoryTrend history={instance?.history ?? []} />

          {instance?.cli_note ? <p className="tm-inst-clinote muted">{instance.cli_note}</p> : null}
        </>
      )}
    </section>
  );
}
