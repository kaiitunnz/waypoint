"use client";

import { insightSeverityTone } from "@/lib/telemetry";
import { Insight } from "@/lib/types";

interface InsightCardsProps {
  insights: Insight[];
  loading: boolean;
  dismissingSignature: string | null;
  onDismiss: (insight: Insight) => void;
  onClickThrough: (insight: Insight) => void;
}

function severityGlyph(severity: Insight["severity"]): string {
  if (severity === "critical") return "▲";
  if (severity === "warning") return "■";
  return "●";
}

export function InsightCards({
  insights,
  loading,
  dismissingSignature,
  onDismiss,
  onClickThrough,
}: InsightCardsProps) {
  if (loading && insights.length === 0) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (insights.length === 0) {
    return null;
  }

  return (
    <section className="tm-insight-grid" aria-label="Insights">
      {insights.map((insight) => {
        const tone = insightSeverityTone(insight.severity);
        return (
          <article key={insight.signature} className={`panel tm-insight-card tone-${tone}`}>
            <header className="tm-insight-head">
              <span className="tm-insight-glyph" aria-hidden="true">
                {severityGlyph(insight.severity)}
              </span>
              <span className="tm-insight-severity">{insight.severity}</span>
              <button
                type="button"
                className="tm-insight-dismiss"
                onClick={() => onDismiss(insight)}
                disabled={dismissingSignature === insight.signature}
                aria-label="Dismiss insight"
              >
                {dismissingSignature === insight.signature ? "…" : "×"}
              </button>
            </header>
            <p className="tm-insight-statement">{insight.statement}</p>
            {Object.keys(insight.metrics).length > 0 ? (
              <dl className="tm-insight-metrics">
                {Object.entries(insight.metrics).map(([key, value]) => (
                  <div key={key} className="tm-insight-metric">
                    <dt>{key.replace(/_/g, " ")}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            <button type="button" className="tm-insight-evidence" onClick={() => onClickThrough(insight)}>
              View evidence →
            </button>
          </article>
        );
      })}
    </section>
  );
}
