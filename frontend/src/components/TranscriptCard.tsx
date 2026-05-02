import { memo, useCallback, useState } from "react";

import { fidelityFor, transportLabel } from "@/lib/backends";
import { EventRecord, SessionTransport } from "@/lib/types";
import { MarkdownMessage } from "@/components/MarkdownMessage";

function legacyCopy(text: string): boolean {
  // execCommand("copy") has different security gating than the async API:
  // it works on plain-HTTP origins and when document focus is ambiguous,
  // as long as the call originates from a user gesture. It returns false
  // (rather than throwing) when the host browser refuses.
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(el);
  return ok;
}

export function CopyMessageButton({
  text,
  label = "Copy message",
}: {
  text: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    if (!text) return;
    let ok = false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        // writeText can reject at runtime — NotAllowedError when the
        // document loses focus or the user denied clipboard-write
        // permission. Fall back to the legacy path before giving up so
        // we don't silently no-op in those contexts.
        ok = legacyCopy(text);
      }
    } else {
      ok = legacyCopy(text);
    }
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [text]);
  return (
    <button
      type="button"
      className={`message-copy${copied ? " copied" : ""}`}
      onClick={(event) => {
        // Tool cards live inside a <details>, so the click would otherwise
        // toggle the disclosure as well as copying.
        event.stopPropagation();
        event.preventDefault();
        void onCopy();
      }}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
    >
      <span aria-hidden>{copied ? "✓" : "⎘"}</span>
    </button>
  );
}

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

export interface AskAnswerEntry {
  question: string;
  answer: string | null;
  notes?: string;
}

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
  pair?: ToolPair;
  onAnswerAskQuestion?: (
    text: string,
    toolUseId?: string,
    answers?: AskAnswerEntry[],
  ) => Promise<boolean> | void;
}

export const TranscriptCard = memo(function TranscriptCard({
  event,
  transport,
  pair,
  onAnswerAskQuestion,
}: TranscriptCardProps) {
  if (fidelityFor(transport) === "structured") {
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
  onAnswerAskQuestion?: (
    text: string,
    toolUseId?: string,
  ) => Promise<boolean> | void;
}) {
  // Convention: the chat-bubble agent label is the first word of the
  // transport label, lowercased ("Claude Cli" → "claude", "Codex App
  // Server" → "codex"). New backends inherit it for free as long as
  // their transport label leads with a single-word agent name.
  const agentLabel = transportLabel(transport).split(" ")[0] || transport;
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
  onAnswerAskQuestion?: (
    text: string,
    toolUseId?: string,
  ) => Promise<boolean> | void;
}) {
  switch (event.kind) {
    case "user_input": {
      if (event.metadata?.kind === "ask_user_question_answer") {
        return <AskAnswerSummaryCard event={event} />;
      }
      return (
        <article className="panel transcript codex user_input">
          <div className="transcript-role">
            <span className="badge user">you</span>
            <span className="role-time">{formatTime(event.ts)}</span>
            <CopyMessageButton text={event.text} />
          </div>
          <MarkdownMessage text={event.text} />
        </article>
      );
    }
    case "agent_output":
      if (event.metadata?.item_kind === "reasoning") {
        return <ReasoningDisclosure event={event} agentLabel={agentLabel} />;
      }
      return (
        <article className="panel transcript codex agent_output">
          <div className="transcript-role">
            <span className="badge agent">{agentLabel}</span>
            <span className="role-time">{formatTime(event.ts)}</span>
            <CopyMessageButton text={event.text} />
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
      if (isTodoToolEvent(event)) {
        return <TodoToolCard event={event} />;
      }
      return <ToolDisclosure event={event} bodyClassName="shell" />;
    }
    case "tool_result":
      if (isTodoToolEvent(event)) {
        return <TodoToolCard event={event} />;
      }
      return <ToolDisclosure event={event} bodyClassName="output" />;
    case "approval_request":
      // The interactive ApprovalCard sits above the composer with the same
      // text and Approve/Decline buttons; rendering the event here too would
      // duplicate the prompt. The chronological record lives in the
      // post-resolution "Approval response sent: …" system note.
      return null;
    case "system_note":
    case "status_update":
      return <SystemRule event={event} />;
    default:
      return <HeuristicCard event={event} />;
  }
}

function SystemRule({ event }: { event: EventRecord }) {
  return (
    <div className="system-rule" role="note">
      <span className="system-rule-body">
        <span className="system-rule-time">{formatTime(event.ts)}</span>
        <span className="system-rule-text" title={event.text}>
          {event.text}
        </span>
      </span>
    </div>
  );
}

function ReasoningDisclosure({
  event,
  agentLabel,
}: {
  event: EventRecord;
  agentLabel: string;
}) {
  // Reasoning is the model's scratchpad — collapse it by default so the
  // final answer underneath stays the dominant element. The user can still
  // expand to inspect the chain of thought.
  return (
    <details className="panel transcript codex agent_output reasoning-disclosure">
      <summary className="transcript-summary">
        <div className="transcript-role">
          <span className="badge agent reasoning">{agentLabel} thinking</span>
          <span className="role-time">{formatTime(event.ts)}</span>
        </div>
      </summary>
      <MarkdownMessage text={event.text} />
    </details>
  );
}

interface ToolBadge {
  glyph: string;
  variant: string;
  label: string;
}

function toolBadgeFor(toolName: string | null | undefined): ToolBadge {
  // Visually distinct glyphs help the user scan a long transcript and tell
  // a shell command apart from a file edit at a glance. The variant maps to
  // a CSS-only colour theme so we don't ship icon assets.
  switch (toolName) {
    case "Bash":
      return { glyph: "›_", variant: "bash", label: "Bash" };
    case "Read":
      return { glyph: "▤", variant: "read", label: toolName };
    case "Edit":
    case "MultiEdit":
      return { glyph: "✎", variant: "edit", label: toolName };
    case "Write":
      return { glyph: "✚", variant: "write", label: toolName };
    case "Grep":
      return { glyph: "⌕", variant: "grep", label: toolName };
    case "Glob":
      return { glyph: "✱", variant: "glob", label: toolName };
    case "WebFetch":
    case "WebSearch":
      return { glyph: "⌖", variant: "web", label: toolName };
    case "Task":
    case "Agent":
      return { glyph: "◇", variant: "task", label: toolName };
    case "TodoWrite":
      return { glyph: "☑", variant: "todo", label: "Todo" };
    case "AskUserQuestion":
      return { glyph: "?", variant: "task", label: "Ask" };
    default:
      if (toolName) {
        return { glyph: "✦", variant: "default", label: toolName };
      }
      return { glyph: "→", variant: "default", label: "tool" };
  }
}

function readToolName(event: EventRecord): string | null {
  const meta = event.metadata as Record<string, unknown> | undefined;
  if (!meta) return null;
  if (typeof meta.tool_name === "string" && meta.tool_name) {
    return meta.tool_name;
  }
  return null;
}

function ToolDisclosure({
  event,
  bodyClassName,
}: {
  event: EventRecord;
  bodyClassName: string;
}) {
  const tool = toolBadgeFor(readToolName(event));
  const preview = previewForToolEvent(event, tool.label);
  const kindLabel = event.kind === "tool_call" ? "call" : "result";
  return (
    <details className={`panel transcript codex ${event.kind} tool-disclosure`}>
      <summary className="transcript-summary">
        <div className="transcript-role">
          <span className={`tool-glyph ${tool.variant}`} aria-hidden>
            {tool.glyph}
          </span>
          <span className="tool-name">{tool.label}</span>
          <span className="badge tool-status pending">{kindLabel}</span>
          <span className="role-time">{formatTime(event.ts)}</span>
        </div>
        {preview ? <p className="transcript-preview">{preview}</p> : null}
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
  onAnswerAskQuestion?: (
    text: string,
    toolUseId?: string,
  ) => Promise<boolean> | void;
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
  if (isTodoToolEvent(call) || isTodoToolEvent(result)) {
    return <TodoToolPairCard pair={pair} />;
  }
  const status = result ? "complete" : "pending";
  const tool = toolBadgeFor(readToolName(call ?? result ?? ({} as EventRecord)));
  const summary =
    (call ? previewForToolEvent(call, tool.label) : null) ||
    (result ? previewForToolEvent(result, tool.label) : null) ||
    "tool call";
  return (
    <details className="panel transcript codex tool_pair tool-disclosure">
      <summary className="transcript-summary">
        <div className="transcript-role">
          <span className={`tool-glyph ${tool.variant}`} aria-hidden>
            {tool.glyph}
          </span>
          <span className="tool-name">{tool.label}</span>
          <span className={`badge tool-status ${status}`}>{status}</span>
          <span className="role-time">{formatTime(pair.ts)}</span>
        </div>
        {summary ? <p className="transcript-preview">{summary}</p> : null}
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

type TodoStatus = "completed" | "in-progress" | "pending";

interface TodoEntry {
  text: string;
  status: TodoStatus;
}

const TODO_MARKER: Record<TodoStatus, string> = {
  completed: "✓",
  "in-progress": "◐",
  pending: "○",
};

// Fixed badge for the cross-backend Todo card. Codex's todo_list is a
// built-in item (not a tool the agent invokes), so don't borrow Claude's
// "TodoWrite" tool name to look it up — render the same badge for both.
const TODO_BADGE = { glyph: "☑", variant: "todo", label: "Todos" } as const;

function TodoToolCard({ event }: { event: EventRecord }) {
  const todos = readTodoEntries(event);
  const status = todoStatusForEvent(event);
  return (
    <article className="panel transcript codex todo-card">
      <div className="transcript-role">
        <span className={`tool-glyph ${TODO_BADGE.variant}`} aria-hidden>
          {TODO_BADGE.glyph}
        </span>
        <span className="tool-name">{TODO_BADGE.label}</span>
        <span className={`badge tool-status ${status}`}>{status}</span>
        <span className="role-time">{formatTime(event.ts)}</span>
      </div>
      <TodoListBody todos={todos} />
    </article>
  );
}

function TodoToolPairCard({ pair }: { pair: ToolPair }) {
  const todos = readTodoEntries(pair.result) ?? readTodoEntries(pair.call);
  const status = pair.result ? todoStatusForEvent(pair.result) : "pending";
  return (
    <article className="panel transcript codex tool_pair todo-card">
      <div className="transcript-role">
        <span className={`tool-glyph ${TODO_BADGE.variant}`} aria-hidden>
          {TODO_BADGE.glyph}
        </span>
        <span className="tool-name">{TODO_BADGE.label}</span>
        <span className={`badge tool-status ${status}`}>{status}</span>
        <span className="role-time">{formatTime(pair.ts)}</span>
      </div>
      <TodoListBody todos={todos} />
    </article>
  );
}

function TodoListBody({ todos }: { todos: TodoEntry[] | null }) {
  if (!todos || todos.length === 0) {
    return <p className="todo-empty">No todo items.</p>;
  }
  return (
    <ul className="todo-list">
      {todos.map((todo, index) => (
        <li key={`${todo.text}-${index}`} className={`todo-item ${todo.status}`}>
          <span className="todo-marker" aria-hidden>
            {TODO_MARKER[todo.status]}
          </span>
          <span className="todo-text">{todo.text}</span>
        </li>
      ))}
    </ul>
  );
}

function isTodoToolEvent(event: EventRecord | null | undefined): boolean {
  if (!event) {
    return false;
  }
  return readTodoEntries(event)?.length ? true : readToolName(event) === "TodoWrite";
}

function todoStatusForEvent(event: EventRecord): "complete" | "pending" {
  const method = typeof event.metadata?.method === "string" ? event.metadata.method : "";
  if (method === "item/completed" || method === "user.tool_result") {
    return "complete";
  }
  return "pending";
}

function readTodoEntries(event: EventRecord | null | undefined): TodoEntry[] | null {
  if (!event) {
    return null;
  }
  const meta = asRecord(event.metadata);
  const payload = asRecord(meta?.payload);
  const input = asRecord(payload?.input) ?? asRecord(meta?.tool_input);
  const item = asRecord(payload?.item);
  const rawTodos =
    (Array.isArray(item?.items) ? item.items : null) ??
    (Array.isArray(input?.todos) ? input.todos : null);
  if (!rawTodos || rawTodos.length === 0) {
    return null;
  }
  const todos = rawTodos
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .map((entry) => {
      // Codex todo_list items carry `text`/`completed` (only two states);
      // Claude's TodoWrite tool uses `content`/`status` with a third
      // `in_progress` state. Read both shapes so one card renders both.
      const text =
        typeof entry.text === "string" && entry.text
          ? entry.text
          : typeof entry.content === "string"
            ? entry.content
            : "";
      let status: TodoStatus;
      if (entry.completed === true || entry.status === "completed") {
        status = "completed";
      } else if (entry.status === "in_progress") {
        status = "in-progress";
      } else {
        status = "pending";
      }
      return { text, status };
    })
    .filter((entry) => entry.text);
  return todos.length > 0 ? todos : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
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
  onAnswer?: (
    text: string,
    toolUseId?: string,
    answers?: AskAnswerEntry[],
  ) => Promise<boolean> | void;
  answered: boolean;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [picked, setPicked] = useState<Record<number, Set<string>>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [notesOpen, setNotesOpen] = useState<Record<number, boolean>>({});
  const [activeIndex, setActiveIndex] = useState(0);

  const total = questions.length;
  const safeIndex = Math.min(activeIndex, Math.max(0, total - 1));
  const currentEntry = questions[safeIndex];
  const paginated = total > 1;

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

  function toggleNote(questionIndex: number) {
    setNotesOpen((current) => ({
      ...current,
      [questionIndex]: !current[questionIndex],
    }));
  }

  async function submit() {
    if (!onAnswer || answered || submitting) return;
    // Match the Claude binary's mapToolResultToToolResultBlockParam shape so
    // the model parses the answer the same way native Claude Code does:
    // `"<question>"="<answer>" user notes: <notes>`, joined by `, ` across
    // questions. Questions with neither an answer nor notes are skipped.
    const segments: string[] = [];
    const structured: AskAnswerEntry[] = [];
    questions.forEach((entry, index) => {
      const selections = picked[index];
      const note = (notes[index] ?? "").trim();
      const hasSelections = Boolean(selections && selections.size > 0);
      if (!hasSelections && !note) return;
      const parts: string[] = [];
      let answerValue: string | null = null;
      if (hasSelections) {
        answerValue = Array.from(selections!).join(", ");
        parts.push(`"${entry.question}"="${answerValue}"`);
      } else {
        parts.push(`"${entry.question}"=(no option selected)`);
      }
      if (note) {
        parts.push(`user notes: ${note}`);
      }
      segments.push(parts.join(" "));
      structured.push({
        question: entry.question,
        answer: answerValue,
        notes: note || undefined,
      });
    });
    if (!segments.length) return;
    setSubmitting(true);
    const toolUseId =
      typeof event.metadata?.tool_use_id === "string"
        ? (event.metadata.tool_use_id as string)
        : undefined;
    try {
      await onAnswer(segments.join(", "), toolUseId, structured);
      setPicked({});
      setNotes({});
      setNotesOpen({});
      setActiveIndex(0);
    } finally {
      setSubmitting(false);
    }
  }

  const totalPicked = Object.values(picked).reduce(
    (acc, set) => acc + set.size,
    0,
  );
  const totalNotes = Object.values(notes).filter((value) => value.trim()).length;
  const interactive = Boolean(onAnswer) && !answered;

  return (
    <article className="panel transcript codex tool_call ask-user-question">
      <div className="transcript-role">
        <span className="tool-glyph task" aria-hidden>?</span>
        <span className="tool-name">Ask you</span>
        {answered ? (
          <span className="badge tool-status complete">answered</span>
        ) : (
          <span className="badge tool-status pending">awaiting answer</span>
        )}
        <span className="role-time">{formatTime(event.ts)}</span>
      </div>
      {currentEntry ? (() => {
        const index = safeIndex;
        const entry = currentEntry;
        const selections = picked[index] ?? new Set<string>();
        const filledForQuestion = (i: number) =>
          (picked[i] && picked[i].size > 0) || Boolean((notes[i] ?? "").trim());
        return (
          <div className="ask-question" key={index}>
            {paginated ? (
              <div className="ask-question-pager">
                <span className="muted">
                  Question {index + 1} of {total}
                  {filledForQuestion(index) ? " · answered" : ""}
                </span>
                <div className="ask-question-pager-dots" aria-hidden>
                  {questions.map((_, dotIndex) => (
                    <span
                      key={dotIndex}
                      className={`ask-question-pager-dot${
                        dotIndex === index ? " current" : ""
                      }${filledForQuestion(dotIndex) ? " filled" : ""}`}
                    />
                  ))}
                </div>
              </div>
            ) : null}
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
            {interactive ? (
              notesOpen[index] ? (
                <div className="ask-question-note">
                  <textarea
                    className="ask-question-note-input"
                    value={notes[index] ?? ""}
                    onChange={(e) =>
                      setNotes((current) => ({
                        ...current,
                        [index]: e.target.value,
                      }))
                    }
                    placeholder="Add a note for this question (optional)…"
                    rows={2}
                    disabled={submitting}
                  />
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => toggleNote(index)}
                    disabled={submitting}
                  >
                    Hide note
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="link-button ask-question-note-toggle"
                  onClick={() => toggleNote(index)}
                  disabled={submitting}
                >
                  + Add note
                </button>
              )
            ) : null}
            {paginated && interactive ? (
              <div className="ask-question-nav">
                <button
                  type="button"
                  className="secondary"
                  disabled={submitting || index === 0}
                  onClick={() => setActiveIndex((i) => Math.max(0, i - 1))}
                >
                  ← Previous
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={submitting || index === total - 1}
                  onClick={() => setActiveIndex((i) => Math.min(total - 1, i + 1))}
                >
                  Next →
                </button>
              </div>
            ) : null}
          </div>
        );
      })() : null}
      {interactive ? (
        <div className="action-row">
          <button
            type="button"
            className="primary"
            disabled={submitting || (totalPicked === 0 && totalNotes === 0)}
            onClick={() => void submit()}
          >
            {submitting ? "Sending…" : "Send answers"}
          </button>
          {totalPicked + totalNotes > 0 ? (
            <button
              type="button"
              className="secondary"
              disabled={submitting}
              onClick={() => {
                setPicked({});
                setNotes({});
              }}
            >
              Clear
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function AskAnswerSummaryCard({ event }: { event: EventRecord }) {
  const meta = event.metadata as Record<string, unknown> | undefined;
  const rawAnswers = Array.isArray(meta?.answers) ? meta!.answers : [];
  const answers = rawAnswers
    .filter(
      (item): item is Record<string, unknown> =>
        Boolean(item) && typeof item === "object",
    )
    .map((item) => ({
      question: typeof item.question === "string" ? item.question : "",
      answer: typeof item.answer === "string" ? item.answer : null,
      notes: typeof item.notes === "string" ? item.notes : undefined,
    }))
    .filter((item) => item.question);

  return (
    <article className="panel transcript codex user_input ask-answer-summary">
      <div className="transcript-role">
        <span className="badge user">you</span>
        <span className="badge neutral ask-answer-chip">answered</span>
        <span className="role-time">{formatTime(event.ts)}</span>
      </div>
      {answers.length > 0 ? (
        <ul className="ask-answer-list">
          {answers.map((entry, index) => (
            <li key={index} className="ask-answer-item">
              <p className="ask-answer-question">{entry.question}</p>
              {entry.answer ? (
                <p className="ask-answer-value">{entry.answer}</p>
              ) : (
                <p className="ask-answer-value muted">No option selected</p>
              )}
              {entry.notes ? (
                <p className="ask-answer-note">
                  <span className="ask-answer-note-label">Note</span>
                  {entry.notes}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        // Older events that pre-date the structured payload — fall back to
        // rendering the raw `"Q"="A"` string Claude received.
        <pre className="ask-answer-fallback">{event.text}</pre>
      )}
    </article>
  );
}

function previewForToolEvent(event: EventRecord, toolLabel: string): string {
  // Prefer extracting a meaningful field from the structured tool input —
  // event.text for Claude tool_call events is `"ToolName\n{json input}"`,
  // which is verbose and redundant with the tool-name chip we already render
  // in the role row.
  const meta = event.metadata as Record<string, unknown> | undefined;
  const toolName = readToolName(event);
  const payload = (meta?.payload ?? null) as Record<string, unknown> | null;
  const input =
    (payload && typeof payload.input === "object" && payload.input !== null
      ? (payload.input as Record<string, unknown>)
      : null) ??
    (typeof meta?.tool_input === "object" && meta?.tool_input !== null
      ? (meta.tool_input as Record<string, unknown>)
      : null);

  if (event.kind === "tool_call" && input) {
    if (toolName === "Bash" && typeof input.command === "string") {
      return truncate(collapseWhitespace(input.command), 240);
    }
    if (
      (toolName === "Edit" || toolName === "MultiEdit" || toolName === "Write") &&
      (typeof input.file_path === "string" || typeof input.path === "string")
    ) {
      return String(input.file_path ?? input.path);
    }
    if (toolName === "Read" && typeof input.file_path === "string") {
      const range =
        typeof input.offset === "number" || typeof input.limit === "number"
          ? ` · ${typeof input.offset === "number" ? input.offset : 1}..${
              typeof input.limit === "number"
                ? (typeof input.offset === "number" ? input.offset : 0) + input.limit
                : "end"
            }`
          : "";
      return `${input.file_path}${range}`;
    }
    if (toolName === "Grep" && typeof input.pattern === "string") {
      const path = typeof input.path === "string" ? input.path : "";
      return path ? `${input.pattern}  ·  ${path}` : input.pattern;
    }
    if (toolName === "Glob" && typeof input.pattern === "string") {
      return input.pattern;
    }
    if ((toolName === "WebFetch" || toolName === "WebSearch") && typeof input.url === "string") {
      return input.url;
    }
    if (toolName === "WebSearch" && typeof input.query === "string") {
      return input.query;
    }
    if ((toolName === "Task" || toolName === "Agent") && typeof input.description === "string") {
      return input.description;
    }
    if (toolName === "TodoWrite" && Array.isArray(input.todos)) {
      return `${input.todos.length} todo${input.todos.length === 1 ? "" : "s"}`;
    }
    const generic = summarizeStructuredInput(input);
    if (generic) {
      return truncate(generic, 240);
    }
  }

  // Fall back to summarising the raw event text. Strip a leading tool-name
  // line (Claude's "Bash\n{json}" shape) so the preview doesn't redundantly
  // repeat what the role-row chip already shows.
  return summarizeToolText(event.text, toolLabel);
}

function summarizeStructuredInput(input: Record<string, unknown>): string {
  // Generic preview for tool calls that don't have a dedicated branch above
  // (Skill, ToolSearch, custom MCP tools, etc.). Joining the entries on a
  // single line surfaces the actual arg values instead of the bare "{" the
  // line-based fallback used to produce for pretty-printed JSON.
  const parts: string[] = [];
  for (const [key, value] of Object.entries(input)) {
    parts.push(`${key}: ${formatStructuredValue(value)}`);
  }
  return parts.join(", ");
}

function formatStructuredValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return collapseWhitespace(JSON.stringify(value));
  } catch {
    return "";
  }
}

function summarizeToolText(text: string, toolLabel?: string): string {
  let lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (toolLabel && lines.length > 0 && lines[0].toLowerCase() === toolLabel.toLowerCase()) {
    lines = lines.slice(1);
  }
  if (!lines.length) {
    return "No output";
  }
  // Pretty-printed JSON puts the opening brace on its own line, which made
  // the preview read as a useless "{ · N lines". When the body parses as
  // JSON, collapse it into a one-line key/value summary instead.
  const joined = lines.join("\n");
  if (joined.startsWith("{") || joined.startsWith("[")) {
    try {
      const parsed = JSON.parse(joined);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const generic = summarizeStructuredInput(parsed as Record<string, unknown>);
        if (generic) return truncate(generic, 240);
      }
      return truncate(collapseWhitespace(JSON.stringify(parsed)), 240);
    } catch {
      // Fall through to the line-based summary.
    }
  }
  const first = lines[0];
  const suffix = lines.length > 1 ? ` · ${lines.length} lines` : "";
  return `${truncate(first, 160)}${suffix}`;
}

function collapseWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
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
      <div className="transcript-role">
        <span className="badge neutral">{event.kind.replaceAll("_", " ")}</span>
        <span className="role-time">{formatTime(event.ts)}</span>
        <CopyMessageButton text={event.text} />
      </div>
      <pre>{event.text}</pre>
    </article>
  );
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
