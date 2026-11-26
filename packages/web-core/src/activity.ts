import { asRecord, FILE_EDIT_TOOLS, normalizeToolName, readToolName, toolFilePath } from "./events";
import { commandForEvent } from "./session/toolPresentation";
import { isWorkingStatus } from "./sessionPresentation";
import { readTodoEntries, summarizeTodos } from "./todos";
import type { EventRecord, SessionStatus } from "./types";

// A tool call in plain words: a verb and, when it names one, the command or file it acts on.
export interface Step {
  verb: string;
  subject: string | null;
}

const stepText = (step: Step) => (step.subject ? `${step.verb} ${step.subject}` : step.verb);

export interface SessionActivity {
  // One short line: the current step while working, else the last thing the agent said.
  line: string | null;
  // The step `line` reads, when it reads one.
  step: Step | null;
  // Task progress from the latest todo list, until every task is done.
  progress: { done: number; total: number } | null;
}

const MAX_LINE = 140;
const MAX_COMMAND = 80;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim())?.trim() ?? "";
}

// Claude carries a tool's input under the payload's `input`, Codex under the started item.
function toolInput(event: EventRecord): Record<string, unknown> {
  const metadata = event.metadata ?? {};
  const payload = asRecord(metadata.payload);
  const item = asRecord(asRecord(metadata.item)?.item) ?? asRecord(payload?.item);
  return asRecord(metadata.tool_input) ?? asRecord(payload?.input) ?? item ?? {};
}

function baseName(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

// "Running" with `npm run lint`, "Editing" with `runtime.py`.
export function describeStep(event: EventRecord): Step | null {
  const tool = normalizeToolName(readToolName(event));
  const input = toolInput(event);
  const command = firstLine(commandForEvent(event) ?? "");
  if (command) return { verb: "Running", subject: truncate(command, MAX_COMMAND) };
  const path = toolFilePath(input);
  const file = path ? baseName(path) : "";
  if (tool && FILE_EDIT_TOOLS.has(tool)) return file ? { verb: "Editing", subject: file } : { verb: "Editing files", subject: null };
  if (tool === "Read") return file ? { verb: "Reading", subject: file } : { verb: "Reading files", subject: null };
  const plain =
    tool === "Grep" || tool === "Glob"
      ? "Searching the code"
      : tool === "Task" || tool === "Agent"
        ? "Running a subagent"
        : tool === "WebFetch" || tool === "WebSearch"
          ? "Searching the web"
          : tool === "TodoWrite"
            ? "Updating the task list"
            : tool;
  return plain ? { verb: plain, subject: null } : null;
}

function plainLine(text: string): string {
  const line = firstLine(text)
    .replace(/!?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)/g, "$1")
    .replace(/^[#>\-*\s]+/, "")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return truncate(line, MAX_LINE);
}

// Streamed deltas arrive as many agent_output events; the trailing run is the reply.
function lastAgentMessage(events: EventRecord[]): string | null {
  let end = events.length - 1;
  while (end >= 0 && events[end].kind !== "agent_output") end -= 1;
  if (end < 0) return null;
  let start = end;
  while (start > 0 && events[start - 1].kind === "agent_output") start -= 1;
  const text = events
    .slice(start, end + 1)
    .map((event) => event.text)
    .join("");
  return plainLine(text) || null;
}

function lastStep(events: EventRecord[]): Step | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].kind !== "tool_call") continue;
    const step = describeStep(events[index]);
    if (step) return step;
  }
  return null;
}

export function deriveActivity(
  events: EventRecord[],
  latestTodo: EventRecord | null,
  status: SessionStatus | undefined,
): SessionActivity {
  const todos = summarizeTodos(readTodoEntries(latestTodo));
  const progress =
    todos && !todos.allComplete ? { done: todos.completed, total: todos.total } : null;
  const step = isWorkingStatus(status) ? lastStep(events) : null;
  return { line: step ? stepText(step) : lastAgentMessage(events), step, progress };
}

// ─── Loading ───

export interface ActivityLoaderOptions<T> {
  fetch: (id: string, signal: AbortSignal) => Promise<T>;
  // `lastEventAt` is the value the result was fetched for.
  onResult: (id: string, result: T, lastEventAt: string) => void;
  // A fetch that failed; one the loader aborted itself is not a failure.
  onFailure?: (id: string, lastEventAt: string) => void;
  // A change settles for this long before it is fetched, but never waits past
  // `maxWaitMs`, so a session that streams constantly still refreshes.
  settleMs?: number;
  maxWaitMs?: number;
  concurrency?: number;
}

interface PendingFetch {
  lastEventAt: string;
  firstAt: number;
  timer: ReturnType<typeof setTimeout>;
  // Settled while an older fetch for the session was still running.
  ready?: boolean;
}

const MAX_FAILURES = 3;

// Fetches a session's latest activity when its `last_event_at` moves: at once the
// first time, then settled and capped, a few at a time. A failed fetch is asked
// for again on the next request, after a settle, up to MAX_FAILURES times per change.
export class ActivityLoader<T> {
  private readonly settleMs: number;
  private readonly maxWaitMs: number;
  private readonly concurrency: number;
  private readonly loaded = new Map<string, string>();
  private readonly failures = new Map<string, { lastEventAt: string; count: number }>();
  private readonly pending = new Map<string, PendingFetch>();
  private readonly inflight = new Map<string, { lastEventAt: string; controller: AbortController }>();
  private readonly queue: string[] = [];
  private disposed = false;

  constructor(private readonly options: ActivityLoaderOptions<T>) {
    this.settleMs = options.settleMs ?? 1500;
    this.maxWaitMs = options.maxWaitMs ?? 8000;
    this.concurrency = options.concurrency ?? 3;
  }

  request(id: string, lastEventAt: string, now: number = Date.now()): void {
    if (this.disposed) return;
    if (this.loaded.get(id) === lastEventAt || this.inflight.get(id)?.lastEventAt === lastEventAt) {
      return;
    }
    const failure = this.failures.get(id);
    if (failure?.lastEventAt === lastEventAt && failure.count >= MAX_FAILURES) return;
    const held = this.pending.get(id);
    if (held) {
      if (held.lastEventAt === lastEventAt) return;
      clearTimeout(held.timer);
    }
    const first = !this.loaded.has(id) && !this.failures.has(id) && !this.inflight.has(id) && !held;
    const firstAt = held?.firstAt ?? now;
    const delay = first ? 0 : Math.max(0, Math.min(this.settleMs, firstAt + this.maxWaitMs - now));
    this.pending.set(id, {
      lastEventAt,
      firstAt,
      timer: setTimeout(() => this.enqueue(id), delay),
    });
  }

  // Records a result already held for this `lastEventAt`, so it is not fetched again.
  seed(id: string, lastEventAt: string): void {
    if (!this.disposed) this.loaded.set(id, lastEventAt);
  }

  forget(id: string): void {
    const held = this.pending.get(id);
    if (held) clearTimeout(held.timer);
    this.pending.delete(id);
    this.inflight.get(id)?.controller.abort();
    this.inflight.delete(id);
    this.loaded.delete(id);
    this.failures.delete(id);
    const queued = this.queue.indexOf(id);
    if (queued !== -1) this.queue.splice(queued, 1);
  }

  dispose(): void {
    this.disposed = true;
    for (const id of [...this.pending.keys(), ...this.inflight.keys()]) this.forget(id);
  }

  private enqueue(id: string): void {
    if (!this.queue.includes(id)) this.queue.push(id);
    this.pump();
  }

  private pump(): void {
    while (!this.disposed && this.inflight.size < this.concurrency && this.queue.length > 0) {
      const id = this.queue.shift() as string;
      const held = this.pending.get(id);
      if (!held) continue;
      if (this.inflight.has(id)) {
        held.ready = true;
        continue;
      }
      this.pending.delete(id);
      const controller = new AbortController();
      this.inflight.set(id, { lastEventAt: held.lastEventAt, controller });
      let succeeded = false;
      this.options
        .fetch(id, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          succeeded = true;
          this.options.onResult(id, result, held.lastEventAt);
        })
        .catch(() => {})
        .finally(() => {
          if (controller.signal.aborted) return;
          if (succeeded) {
            this.loaded.set(id, held.lastEventAt);
            this.failures.delete(id);
          } else {
            const previous = this.failures.get(id);
            const count = previous?.lastEventAt === held.lastEventAt ? previous.count + 1 : 1;
            this.failures.set(id, { lastEventAt: held.lastEventAt, count });
            this.options.onFailure?.(id, held.lastEventAt);
          }
          this.inflight.delete(id);
          if (this.pending.get(id)?.ready) this.enqueue(id);
          else this.pump();
        });
    }
  }
}
