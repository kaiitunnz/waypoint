import { EventRecord, SessionTransport } from "@/lib/types";

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
}

export function TranscriptCard({ event, transport }: TranscriptCardProps) {
  if (transport === "codex_app_server") {
    return <CodexCard event={event} />;
  }
  return <HeuristicCard event={event} />;
}

function CodexCard({ event }: { event: EventRecord }) {
  switch (event.kind) {
    case "user_input":
      return (
        <article className="panel transcript codex user_input">
          <div className="session-row">
            <span className="badge user">you</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <pre>{event.text}</pre>
        </article>
      );
    case "agent_output":
      return (
        <article className="panel transcript codex agent_output">
          <div className="session-row">
            <span className="badge agent">codex</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <pre>{event.text}</pre>
        </article>
      );
    case "tool_call":
      return (
        <article className="panel transcript codex tool_call">
          <div className="session-row">
            <span className="badge tool">tool call</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <pre className="shell">{event.text}</pre>
        </article>
      );
    case "tool_result":
      return (
        <article className="panel transcript codex tool_result">
          <div className="session-row">
            <span className="badge tool">tool result</span>
            <span className="muted">{formatTime(event.ts)}</span>
          </div>
          <pre className="output">{event.text}</pre>
        </article>
      );
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
