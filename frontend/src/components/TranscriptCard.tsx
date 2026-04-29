import { memo, useState } from "react";

import { EventRecord, SessionTransport } from "@/lib/types";
import { MarkdownMessage } from "@/components/MarkdownMessage";

export interface ToolPair {
  call: EventRecord | null;
  result: EventRecord | null;
  itemId: string;
  ts: string;
  sequence: number;
}

interface AskQuestionOption {
  label: string;
  description?: string;
}

interface AskUserQuestion {
  question: string;
  header?: string;
  options: AskQuestionOption[];
  multiSelect?: boolean;
}

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
  pair?: ToolPair;
  onAnswerAskQuestion?: (text: string) => Promise<boolean> | void;
}

export const TranscriptCard = memo(function TranscriptCard({
  event,
  transport,
  pair,
  onAnswerAskQuestion,
}: TranscriptCardProps) {
  if (transport === "codex_app_server" || transport === "claude_cli") {
    if (pair) {
      return (
        <ToolPairCard pair={pair} onAnswerAskQuestion={onAnswerAskQuestion} />
      );
    }
    return (
      <StructuredCard
        event={event}
        transport={transport}
        onAnswerAskQuestion={onAnswerAskQuestion}
      />
    );
  }
  return <HeuristicCard event={event} />;
});

function StructuredCard({
  event,
  transport,
  onAnswerAskQuestion,
}: {
  event: EventRecord;
  transport: SessionTransport;
  onAnswerAskQuestion?: (text: string) => Promise<boolean> | void;
}) {
  const agentLabel = transport === "claude_cli" ? "claude" : "codex";
  return (
    <CodexCard
      event={event}
      agentLabel={agentLabel}
      onAnswerAskQuestion={onAnswerAskQuestion}
    />
  );
}

function CodexCard({
  event,
  agentLabel = "codex",
  onAnswerAskQuestion,
}: {
  event: EventRecord;
  agentLabel?: string;
  onAnswerAskQuestion?: (text: string) => Promise<boolean> | void;
}) {
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
    case "tool_call": {
      const ask = parseAskUserQuestion(event);
      if (ask) {
        return (
          <AskUserQuestionCard
            event={event}
            questions={ask}
            onAnswer={onAnswerAskQuestion}
            answered={false}
          />
        );
      }
      return <ToolDisclosure event={event} label="tool call" bodyClassName="shell" />;
    }
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

function ToolPairCard({
  pair,
  onAnswerAskQuestion,
}: {
  pair: ToolPair;
  onAnswerAskQuestion?: (text: string) => Promise<boolean> | void;
}) {
  const { call, result } = pair;
  if (call) {
    const ask = parseAskUserQuestion(call);
    if (ask) {
      return (
        <AskUserQuestionCard
          event={call}
          questions={ask}
          onAnswer={onAnswerAskQuestion}
          answered={Boolean(result)}
        />
      );
    }
  }
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

function parseAskUserQuestion(event: EventRecord): AskUserQuestion[] | null {
  if ((event.metadata?.tool_name as string | undefined) !== "AskUserQuestion") {
    return null;
  }
  const payload = event.metadata?.payload as { input?: unknown } | undefined;
  const input = payload?.input as { questions?: unknown } | undefined;
  const raw = input?.questions;
  if (!Array.isArray(raw) || raw.length === 0) {
    return null;
  }
  const parsed: AskUserQuestion[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const q = entry as Record<string, unknown>;
    if (typeof q.question !== "string") continue;
    const optionsRaw = Array.isArray(q.options) ? q.options : [];
    const options: AskQuestionOption[] = [];
    for (const opt of optionsRaw) {
      if (!opt || typeof opt !== "object") continue;
      const o = opt as Record<string, unknown>;
      if (typeof o.label !== "string") continue;
      options.push({
        label: o.label,
        description: typeof o.description === "string" ? o.description : undefined,
      });
    }
    if (!options.length) continue;
    parsed.push({
      question: q.question,
      header: typeof q.header === "string" ? q.header : undefined,
      options,
      multiSelect: q.multiSelect === true,
    });
  }
  return parsed.length ? parsed : null;
}

function AskUserQuestionCard({
  event,
  questions,
  onAnswer,
  answered,
}: {
  event: EventRecord;
  questions: AskUserQuestion[];
  onAnswer?: (text: string) => Promise<boolean> | void;
  answered: boolean;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [picked, setPicked] = useState<Record<number, Set<string>>>({});

  function toggleOption(questionIndex: number, label: string, multiSelect: boolean) {
    setPicked((current) => {
      const next = { ...current };
      const existing = next[questionIndex] ?? new Set<string>();
      const updated = new Set(existing);
      if (multiSelect) {
        if (updated.has(label)) updated.delete(label);
        else updated.add(label);
      } else {
        if (updated.has(label) && updated.size === 1) updated.clear();
        else {
          updated.clear();
          updated.add(label);
        }
      }
      next[questionIndex] = updated;
      return next;
    });
  }

  async function submit() {
    if (!onAnswer || answered || submitting) return;
    const lines: string[] = [];
    questions.forEach((entry, index) => {
      const selections = picked[index];
      if (!selections || selections.size === 0) return;
      const value = Array.from(selections).join(", ");
      lines.push(`**${entry.header ?? entry.question}**: ${value}`);
    });
    if (!lines.length) return;
    setSubmitting(true);
    try {
      await onAnswer(lines.join("\n"));
      setPicked({});
    } finally {
      setSubmitting(false);
    }
  }

  const totalPicked = Object.values(picked).reduce(
    (acc, set) => acc + set.size,
    0,
  );
  const interactive = Boolean(onAnswer) && !answered;

  return (
    <article className="panel transcript codex tool_call ask-user-question">
      <div className="session-row">
        <span className="badge tool">ask</span>
        {answered ? (
          <span className="badge tool-status complete">answered</span>
        ) : (
          <span className="badge tool-status pending">awaiting answer</span>
        )}
        <span className="muted">{formatTime(event.ts)}</span>
      </div>
      {questions.map((entry, index) => {
        const selections = picked[index] ?? new Set<string>();
        return (
          <div className="ask-question" key={index}>
            <div className="ask-question-head">
              {entry.header ? (
                <span className="badge neutral ask-question-chip">
                  {entry.header}
                </span>
              ) : null}
              <p className="ask-question-text">{entry.question}</p>
              {entry.multiSelect ? (
                <span className="meta">multi-select</span>
              ) : null}
            </div>
            <ul className="ask-question-options">
              {entry.options.map((option) => {
                const selected = selections.has(option.label);
                return (
                  <li key={option.label}>
                    <button
                      type="button"
                      className={`ask-option ${selected ? "selected" : ""}`}
                      onClick={() =>
                        toggleOption(index, option.label, entry.multiSelect ?? false)
                      }
                      disabled={!interactive}
                    >
                      <span className="ask-option-label">{option.label}</span>
                      {option.description ? (
                        <span className="ask-option-desc">
                          {option.description}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      {interactive ? (
        <div className="action-row">
          <button
            type="button"
            className="primary"
            disabled={submitting || totalPicked === 0}
            onClick={() => void submit()}
          >
            {submitting ? "Sending…" : "Send answers"}
          </button>
          {totalPicked > 0 ? (
            <button
              type="button"
              className="secondary"
              disabled={submitting}
              onClick={() => setPicked({})}
            >
              Clear
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
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
