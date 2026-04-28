import { memo } from "react";

import { EventRecord, SessionTransport } from "@/lib/types";
import { MarkdownMessage } from "@/components/MarkdownMessage";

export interface ToolPair {
  call: EventRecord | null;
  result: EventRecord | null;
  itemId: string;
  ts: string;
  sequence: number;
}

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
  pair?: ToolPair;
}

export const TranscriptCard = memo(function TranscriptCard({ event, transport, pair }: TranscriptCardProps) {
  if (transport === "codex_app_server" || transport === "claude_cli") {
    if (pair) {
      return <ToolPairCard pair={pair} />;
    }
    return <StructuredCard event={event} transport={transport} />;
  }
  return <HeuristicCard event={event} />;
});

function StructuredCard({ event, transport }: { event: EventRecord; transport: SessionTransport }) {
  const agentLabel = transport === "claude_cli" ? "claude" : "codex";
  return <CodexCard event={event} agentLabel={agentLabel} />;
}

function CodexCard({ event, agentLabel = "codex" }: { event: EventRecord; agentLabel?: string }) {
  switch (event.kind) {
    case "user_input":
      return (
        <article className="panel transcript codex user_input">
          <div className="session-row">
            <span className="badge user">you</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <MarkdownMessage text={event.text} />
        </article>
      );
    case "agent_output":
      return (
        <article className="panel transcript codex agent_output">
          <div className="session-row">
            <span className="badge agent">{agentLabel}</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <MarkdownMessage text={event.text} />
        </article>
      );
    case "tool_call":
      return <ToolDisclosure event={event} label="tool call" bodyClassName="shell" />;
    case "tool_result":
      return <ToolDisclosure event={event} label="tool result" bodyClassName="output" />;
    case "approval_request":
      return (
        <article className="panel transcript codex approval_request">
          <div className="session-row">
            <span className="badge fidelity structured">approval</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <pre>{event.text}</pre>
        </article>
      );
    case "system_note":
    case "status_update":
      return (
        <article className="transcript codex system">
          <span className="muted">
            {formatTime(event.ts)} · {event.text}
          </span>
        </article>
      );
    default:
      return <HeuristicCard event={event} />;
  }
}

function ToolDisclosure({
  event,
  label,
  bodyClassName,
}: {
  event: EventRecord;
  label: string;
  bodyClassName: string;
}) {
  const preview = summarizeToolText(event.text);
  return (
    <details className={`panel transcript codex ${event.kind} tool-disclosure`}>
      <summary className="transcript-summary">
        <div className="session-row">
          <span className="badge tool">{label}</span>
          <span className="muted">{formatTime(event.ts)}</span>
        </div>
        {preview ? <p className="transcript-preview muted">{preview}</p> : null}
      </summary>
      <pre className={bodyClassName}>{event.text}</pre>
    </details>
  );
}

function ToolPairCard({ pair }: { pair: ToolPair }) {
  const { call, result } = pair;
  const callPreview = call ? summarizeToolText(call.text) : null;
  const resultPreview = result ? summarizeToolText(result.text) : null;
  const status = result ? "complete" : "pending";
  const summary = callPreview || resultPreview || "tool call";
  return (
    <details className="panel transcript codex tool_pair tool-disclosure">
      <summary className="transcript-summary">
        <div className="session-row">
          <span className="badge tool">tool</span>
          <span className={`badge tool-status ${status}`}>{status}</span>
          <span className="muted">{formatTime(pair.ts)}</span>
        </div>
        {summary ? <p className="transcript-preview muted">{summary}</p> : null}
      </summary>
      <div className="tool-pair-body">
        {call ? (
          <div className="tool-pair-section">
            <p className="tool-pair-label">call</p>
            <pre className="shell">{call.text}</pre>
          </div>
        ) : null}
        {result ? (
          <div className="tool-pair-section">
            <p className="tool-pair-label">result</p>
            <pre className="output">{result.text}</pre>
          </div>
        ) : (
          <div className="tool-pair-section">
            <p className="tool-pair-label muted">awaiting result…</p>
          </div>
        )}
      </div>
    </details>
  );
}

function summarizeToolText(text: string): string {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) {
    return "No output";
  }
  const first = lines[0];
  const suffix = lines.length > 1 ? ` · ${lines.length} lines` : "";
  return `${truncate(first, 120)}${suffix}`;
}

function truncate(value: string, max: number): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max - 1)}…`;
}

function HeuristicCard({ event }: { event: EventRecord }) {
  return (
    <article className={`panel transcript ${event.kind}`}>
      <div className="session-row">
        <span className="badge neutral">{event.kind.replaceAll("_", " ")}</span>
        <span className="muted">{formatTime(event.ts)}</span>
      </div>
      <pre>{event.text}</pre>
    </article>
  );
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString();
}
