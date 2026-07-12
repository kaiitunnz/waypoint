"use client";

import { useId, useState } from "react";

import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { confidenceLabel } from "@/lib/telemetry";
import { NLInsightEvidence, NLInsightResponse } from "@/lib/types";
import { formatRelativeTime } from "@/lib/usage";

// The summarizer returns markdown (a "- " bullet list, occasional **bold**);
// render it so inline formatting resolves instead of showing literal "**".
const NL_REMARK_PLUGINS = [remarkGfm, remarkBreaks];

interface NLInsightCardProps {
  nlEnabled: boolean;
  response: NLInsightResponse | null;
  loading: boolean;
  generating: boolean;
  onGenerate: () => void;
  onEvidenceClick: (evidence: NLInsightEvidence) => void;
}

export function NLInsightCard({
  nlEnabled,
  response,
  loading,
  generating,
  onGenerate,
  onEvidenceClick,
}: NLInsightCardProps) {
  const titleId = useId();
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  if (!nlEnabled) {
    return (
      <section className="panel tm-chart-card tm-nl-card" aria-labelledby={titleId}>
        <header className="tm-chart-head">
          <h3 id={titleId}>AI insight</h3>
          <span className="tm-nl-badge">Opt-in</span>
        </header>
        <p className="muted tm-chart-empty tm-nl-optin">
          Off by default. When enabled, a coding agent turns the on-screen
          aggregates into a plain-language digest — see Privacy settings below.
          Enable via <code>telemetry_nl.enabled</code> in{" "}
          <code>waypoint.yaml</code>.
        </p>
      </section>
    );
  }

  if (loading && !response) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }

  const insight = response?.insight ?? null;

  if (!insight) {
    return (
      <section className="panel tm-chart-card tm-nl-card" aria-labelledby={titleId}>
        <header className="tm-chart-head">
          <h3 id={titleId}>AI insight</h3>
        </header>
        <p className="muted tm-chart-empty">No digest yet for this range.</p>
        <button
          type="button"
          className="tm-insight-evidence"
          onClick={onGenerate}
          disabled={generating}
        >
          {generating ? "Generating…" : "Generate now"}
        </button>
      </section>
    );
  }

  return (
    <section className="panel tm-chart-card tm-nl-card" aria-labelledby={titleId}>
      <header className="tm-chart-head">
        <h3 id={titleId}>
          {response?.available ? "Insights available" : "AI insight"}
        </h3>
        <span className="tm-nl-badge">
          {confidenceLabel(insight.confidence)} confidence
        </span>
      </header>

      <div className="tm-nl-prose">
        <ReactMarkdown
          remarkPlugins={NL_REMARK_PLUGINS}
          components={{
            ul: (props) => <ul className="tm-nl-prose-list" {...props} />,
            // Flatten stray paragraphs the model may emit between bullets.
            p: (props) => <p className="tm-insight-statement" {...props} />,
          }}
        >
          {insight.prose}
        </ReactMarkdown>
      </div>

      {insight.instance_bullets && insight.instance_bullets.length > 0 ? (
        <div className="tm-nl-instance">
          <p className="tm-nl-instance-eyebrow">Instance health &amp; capacity</p>
          <ul className="tm-nl-instance-list">
            {insight.instance_bullets.map((bullet, i) => (
              <li key={`${bullet.template_id}:${i}`} className="tm-nl-instance-row">
                <span className="tm-nl-instance-text">{bullet.text}</span>
                {bullet.evidence.length > 0 ? (
                  <button
                    type="button"
                    className="tm-insight-evidence"
                    onClick={() => onEvidenceClick(bullet.evidence[0])}
                  >
                    View →
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {insight.evidence.length > 0 ? (
        <div className="tm-nl-evidence">
          <button
            type="button"
            className="tm-insight-evidence tm-nl-evidence-toggle"
            onClick={() => setEvidenceOpen((open) => !open)}
            aria-expanded={evidenceOpen}
          >
            {evidenceOpen ? "Hide evidence" : `Show evidence (${insight.evidence.length})`}
          </button>
          {evidenceOpen ? (
            <ul className="tm-nl-evidence-list">
              {insight.evidence.map((evidence, i) => {
                const echoesValue =
                  evidence.value.trim().length === 0 ||
                  evidence.statement.includes(evidence.value.trim());
                return (
                  <li key={`${evidence.metric}:${i}`} className="tm-nl-evidence-row">
                    <span className="tm-nl-evidence-text">
                      {evidence.statement}
                      {echoesValue ? null : <strong> {evidence.value}</strong>}
                    </span>
                    <button
                      type="button"
                      className="tm-insight-evidence"
                      onClick={() => onEvidenceClick(evidence)}
                    >
                      View →
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}

      <p className="tm-nl-disclaimer muted">{insight.disclaimer}</p>

      <footer className="tm-nl-footer">
        <span className="muted">
          {formatRelativeTime(insight.generated_at)} · {insight.source_backend}
          {insight.source_model ? ` · ${insight.source_model}` : ""}
          {response && !response.fresh ? " · stale" : ""}
        </span>
        <button
          type="button"
          className="tm-insight-evidence"
          onClick={onGenerate}
          disabled={generating}
        >
          {generating ? "Regenerating…" : "Regenerate"}
        </button>
      </footer>
    </section>
  );
}
