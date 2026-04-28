import { EventRecord, SessionTransport } from "@/lib/types";
import { MarkdownMessage } from "@/components/MarkdownMessage";

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
}

export function TranscriptCard({ event, transport }: TranscriptCardProps) {
  if (transport === "codex_app_server" || transport === "claude_cli") {
    return <StructuredCard event={event} transport={transport} />;
  }
  return <HeuristicCard event={event} />;
}

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
