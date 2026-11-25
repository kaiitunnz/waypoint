import {
  memo,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import {
  agentForTransport,
  fidelityFor,
  humaniseBackend,
  type BackendCatalog,
} from "@waypoint/web-core/backends";
import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { useReportPreview } from "@waypoint/web-core/hooks/useAttachmentPreview";
import { BackendModelOption, EventRecord, SessionTransport } from "@waypoint/web-core/types";
import {
  parseEvent,
  parseTaskNotification,
  planTextForEvent,
  readToolName,
  type TaskNotificationKind,
  type TaskNotificationView,
} from "@waypoint/web-core/events";
import { readTodoEntries, summarizeTodos, type TodoEntry } from "@waypoint/web-core/todos";
import { cardKindOf } from "@waypoint/web-core/session/cardKind";
import {
  askAnswerEntries,
  formatEventTime,
  modelChangeText,
  reasoningPreview,
  sendUserFileInput,
} from "@waypoint/web-core/session/cardText";
import {
  fallbackDiffPreviewForFileEdit,
  previewForToolEvent,
  toolBadgeFor,
  toolPairAttachments,
} from "@waypoint/web-core/session/toolPresentation";
import { toolRunCounts } from "@waypoint/web-core/session/toolRun";
import {
  TASK_NOTIFICATION_BADGES,
  taskStatusLamp,
} from "@waypoint/web-core/session/taskNotificationView";
import { taskNoteBody, taskNoteReport, type ReportPreviewState } from "@waypoint/web-core/session/taskNoteBody";
import {
  MessageAttachments,
  useAttachmentContext,
} from "@/components/AttachmentTray";
import { useSessionFilesLink } from "@/components/SessionFilesLinkContext";
import { PlanApprovalCard } from "@/components/ApprovalCard";
import { CopyMessageButton } from "@/components/CopyMessageButton";
import { DiffPreview } from "@/components/DiffPreview";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { TodoListBody } from "@/components/TodoList";
import { AskQuestionForm, useAskQuestionController } from "@/components/AskQuestion";
import {
  askFormState,
  askToolUseId,
  parseAskUserQuestion,
  type AskQuestionResolution,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";
import type { ToolPair } from "@waypoint/web-core/session/transcript";

// Measure the meta strip's real width and surface it as `--meta-reserve`
// on the bubble, so the inline phantom at the end of the last paragraph
// can be sized to exactly the meta's footprint plus a small gap. With
// this, the browser's line-breaker decides cleanly whether the meta fits
// on the last line (no overlap) or has to wrap to its own row (no
// hardcoded 116px guess that sometimes lets text bleed under the meta).
function useMetaReserve(gap: number = 16) {
  const metaRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const node = metaRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setWidth(Math.ceil(w));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const style =
    width != null
      ? ({ "--meta-reserve": `${width + gap}px` } as CSSProperties)
      : undefined;
  return { metaRef, style };
}

export function PendingUserInputCard({
  text,
  ts,
}: {
  text: string;
  ts: string;
}) {
  const { metaRef, style } = useMetaReserve();
  return (
    <article
      className="panel transcript codex user_input pending-optimistic"
      aria-label="Message from you"
      style={style}
    >
      <MarkdownMessage text={text} />
      <div ref={metaRef} className="transcript-meta">
        <span className="badge user">you</span>
        <span className="role-time">{formatEventTime(ts)}</span>
      </div>
    </article>
  );
}

interface TranscriptCardProps {
  event: EventRecord;
  transport: SessionTransport;
  catalog?: BackendCatalog;
  pair?: ToolPair;
  modelOptions?: BackendModelOption[];
  onOpenWorkspaceFile?: (path: string) => void;
}

export const TranscriptCard = memo(function TranscriptCard({
  event,
  transport,
  catalog,
  pair,
  modelOptions,
  onOpenWorkspaceFile,
}: TranscriptCardProps) {
  if (fidelityFor(transport, catalog) === "structured") {
    if (pair) {
      return (
        <ToolPairCard
          pair={pair}
          onOpenWorkspaceFile={onOpenWorkspaceFile}
        />
      );
    }
    return (
      <StructuredCard
        event={event}
        transport={transport}
        catalog={catalog}
        modelOptions={modelOptions}
        onOpenWorkspaceFile={onOpenWorkspaceFile}
      />
    );
  }
  return <HeuristicCard event={event} />;
});

function StructuredCard({
  event,
  transport,
  catalog,
  modelOptions,
  onOpenWorkspaceFile,
}: {
  event: EventRecord;
  transport: SessionTransport;
  catalog?: BackendCatalog;
  modelOptions?: BackendModelOption[];
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  // Convention: the chat-bubble agent label is the first word of the agent
  // that owns this transport, lowercased ("Claude Code" → "claude", "Codex"
  // → "codex"). Resolved from the transport's owning agent so it stays correct
  // regardless of the transport's user-facing name.
  const owner = agentForTransport(transport, catalog);
  const agentLabel =
    (owner ? humaniseBackend(owner, catalog).split(" ")[0].toLowerCase() : "") ||
    transport;
  return (
    <CodexCard
      event={event}
      agentLabel={agentLabel}
      modelOptions={modelOptions}
      onOpenWorkspaceFile={onOpenWorkspaceFile}
    />
  );
}

function CodexCard({
  event,
  agentLabel = "codex",
  modelOptions,
  onOpenWorkspaceFile,
}: {
  event: EventRecord;
  agentLabel?: string;
  modelOptions?: BackendModelOption[];
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  switch (cardKindOf(event)) {
    case "ask_answer":
      return <AskAnswerSummaryCard event={event} />;
    case "user_message":
      return <UserMessageBubble event={event} />;
    case "reasoning":
      return <ReasoningDisclosure event={event} agentLabel={agentLabel} />;
    case "agent_message":
      return <AgentMessageBubble event={event} agentLabel={agentLabel} />;
    case "ask_question":
      return <AskUserQuestionCard event={event} questions={parseAskUserQuestion(event) ?? []} />;
    case "send_file":
      return <SendUserFileCard event={event} />;
    case "todo":
      return <TodoToolCard event={event} />;
    case "tool":
      return (
        <ToolDisclosure
          event={event}
          bodyClassName={event.kind === "tool_call" ? "shell" : "output"}
          onOpenWorkspaceFile={onOpenWorkspaceFile}
        />
      );
    case "plan": {
      const plan = planTextForEvent(event);
      return plan ? (
        <PlanApprovalCard
          agentLabel={agentLabel}
          className="transcript codex plan-output"
          plan={plan}
          prompt="Plan"
          timeLabel={formatEventTime(event.ts)}
        />
      ) : null;
    }
    case "status":
      return <CommandStatusCard event={event} agentLabel={agentLabel} />;
    case "model_switch":
      return <ModelChangeDivider event={event} modelOptions={modelOptions} />;
    case "task_notification": {
      const view = parseTaskNotification(event);
      return view ? <TaskNotificationCard view={view} event={event} /> : null;
    }
    case "diff_note":
    case "rule":
      return <SystemRule event={event} onOpenWorkspaceFile={onOpenWorkspaceFile} />;
    case "heuristic":
      return <HeuristicCard event={event} />;
    case "none":
      return null;
  }
}

function ModelChangeDivider({
  event,
  modelOptions,
}: {
  event: EventRecord;
  modelOptions?: BackendModelOption[];
}) {
  return (
    <div className="system-rule model-change" role="note">
      <span className="system-rule-body">
        <span className="system-rule-time">{formatEventTime(event.ts)}</span>
        <span className="model-change-glyph" aria-hidden="true">
          ⇄
        </span>
        <span className="system-rule-text">{modelChangeText(event, modelOptions)}</span>
      </span>
    </div>
  );
}

function UserMessageBubble({ event }: { event: EventRecord }) {
  const { metaRef, style } = useMetaReserve();
  return (
    <article
      className="panel transcript codex user_input"
      aria-label="Message from you"
      style={style}
    >
      <MarkdownMessage text={event.text} />
      <MessageAttachments specs={attachmentSpecsFor(event)} />
      <div ref={metaRef} className="transcript-meta">
        <span className="badge user">you</span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
        <CopyMessageButton text={event.text} />
      </div>
    </article>
  );
}

function AgentMessageBubble({
  event,
  agentLabel,
}: {
  event: EventRecord;
  agentLabel: string;
}) {
  const { metaRef, style } = useMetaReserve();
  return (
    <article
      className="panel transcript codex agent_output"
      aria-label={`Message from ${agentLabel}`}
      style={style}
    >
      <MarkdownMessage text={event.text} />
      <div ref={metaRef} className="transcript-meta">
        <span className="badge agent">{agentLabel}</span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
        <CopyMessageButton text={event.text} />
      </div>
    </article>
  );
}

function CommandStatusCard({
  event,
  agentLabel,
}: {
  event: EventRecord;
  agentLabel: string;
}) {
  return (
    <article className="panel transcript codex command-status">
      <div className="transcript-role">
        <span className="badge neutral">/status</span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
        <span className="muted">{agentLabel}</span>
        <CopyMessageButton text={event.text} />
      </div>
      <MarkdownMessage text={event.text} />
    </article>
  );
}

function SystemRule({
  event,
  onOpenWorkspaceFile,
}: {
  event: EventRecord;
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  const diffPreview = parseEvent(event).diffPreview;
  if (diffPreview) {
    return (
      <details className="panel transcript codex system diff-system-card">
        <summary className="transcript-summary">
          <div className="transcript-role">
            <span className="badge neutral">changes</span>
            <span className="role-time">{formatEventTime(event.ts)}</span>
          </div>
          <p className="transcript-preview">{event.text}</p>
        </summary>
        <DiffPreview preview={diffPreview} onOpenWorkspaceFile={onOpenWorkspaceFile} />
      </details>
    );
  }
  return (
    <div className="system-rule" role="note">
      <span className="system-rule-body">
        <span className="system-rule-time">{formatEventTime(event.ts)}</span>
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
  // Reasoning is the model's scratchpad — collapsed by default with a
  // single-line preview so the transcript stays scannable, expandable
  // when the user wants the full chain of thought. The mechanism is
  // backend-agnostic: any plugin emitting metadata.item_kind="reasoning"
  // gets this treatment.
  const preview = reasoningPreview(event.text);
  return (
    <details className="panel transcript codex agent_output reasoning-disclosure">
      <summary className="transcript-summary">
        <div className="transcript-role">
          <span className="badge agent reasoning">{agentLabel} thinking</span>
          <span className="role-time">{formatEventTime(event.ts)}</span>
        </div>
        {preview ? <p className="transcript-preview">{preview}</p> : null}
      </summary>
      <MarkdownMessage text={event.text} />
    </details>
  );
}

export function ToolCallRunGroup({
  toolNames,
  initiallyOpen,
  children,
}: {
  toolNames: string[];
  initiallyOpen: boolean;
  children: React.ReactNode;
}) {
  const count = toolNames.length;
  const {
    bash: bashCount,
    edit: editCount,
    read: readCount,
    todo: todoCount,
    agent: agentCount,
    skill: skillCount,
    monitor: monitorCount,
    other: otherCount,
  } = toolRunCounts(toolNames);

  return (
    <details className="tool-call-run" open={initiallyOpen}>
      <summary className="tool-call-run-summary">
        <div className="tool-run-chips">
          {bashCount > 0 && (
            <span className="tool-run-chip bash">
              <span className="tool-run-glyph">›_</span>
              <span className="tool-run-label">bash</span>
              <span className="tool-run-count">×{bashCount}</span>
            </span>
          )}
          {editCount > 0 && (
            <span className="tool-run-chip edit">
              <span className="tool-run-glyph">✎</span>
              <span className="tool-run-label">edit</span>
              <span className="tool-run-count">×{editCount}</span>
            </span>
          )}
          {readCount > 0 && (
            <span className="tool-run-chip read">
              <span className="tool-run-glyph">▤</span>
              <span className="tool-run-label">read</span>
              <span className="tool-run-count">×{readCount}</span>
            </span>
          )}
          {todoCount > 0 && (
            <span className="tool-run-chip todo">
              <span className="tool-run-glyph">☑</span>
              <span className="tool-run-label">todos</span>
              <span className="tool-run-count">×{todoCount}</span>
            </span>
          )}
          {agentCount > 0 && (
            <span className="tool-run-chip agent">
              <span className="tool-run-glyph">◇</span>
              <span className="tool-run-label">agent</span>
              <span className="tool-run-count">×{agentCount}</span>
            </span>
          )}
          {skillCount > 0 && (
            <span className="tool-run-chip skill">
              <span className="tool-run-glyph">❖</span>
              <span className="tool-run-label">skill</span>
              <span className="tool-run-count">×{skillCount}</span>
            </span>
          )}
          {monitorCount > 0 && (
            <span className="tool-run-chip monitor">
              <span className="tool-run-glyph">◉</span>
              <span className="tool-run-label">monitor</span>
              <span className="tool-run-count">×{monitorCount}</span>
            </span>
          )}
          {otherCount > 0 && (
            <span className="tool-run-chip other">
              <span className="tool-run-glyph">ƒ</span>
              <span className="tool-run-label">other</span>
              <span className="tool-run-count">×{otherCount}</span>
            </span>
          )}
        </div>
        <span className="tool-run-total">{count} call{count !== 1 ? "s" : ""}</span>
      </summary>
      <div className="tool-call-run-children">
        {children}
      </div>
    </details>
  );
}

// Consecutive task-notification cards collapse into one run, mirroring
// ToolCallRunGroup. `kinds` holds one entry per notification (chip colours reuse
// the tool-run classes, glyphs reuse TASK_NOTIFICATION_BADGES).
export function TaskNotificationRunGroup({
  kinds,
  initiallyOpen,
  children,
}: {
  kinds: TaskNotificationKind[];
  initiallyOpen: boolean;
  children: React.ReactNode;
}) {
  const count = kinds.length;
  let agentCount = 0;
  let monitorCount = 0;
  let commandCount = 0;
  let messageCount = 0;
  let otherCount = 0;
  for (const kind of kinds) {
    if (kind === "agent") agentCount++;
    else if (kind === "monitor") monitorCount++;
    else if (kind === "background_command") commandCount++;
    else if (kind === "message") messageCount++;
    else otherCount++;
  }

  return (
    <details className="tool-call-run" open={initiallyOpen}>
      <summary className="tool-call-run-summary">
        <div className="tool-run-chips">
          {agentCount > 0 && (
            <span className="tool-run-chip agent">
              <span className="tool-run-glyph">◇</span>
              <span className="tool-run-label">agent</span>
              <span className="tool-run-count">×{agentCount}</span>
            </span>
          )}
          {monitorCount > 0 && (
            <span className="tool-run-chip monitor">
              <span className="tool-run-glyph">◉</span>
              <span className="tool-run-label">monitor</span>
              <span className="tool-run-count">×{monitorCount}</span>
            </span>
          )}
          {commandCount > 0 && (
            <span className="tool-run-chip bash">
              <span className="tool-run-glyph">›_</span>
              <span className="tool-run-label">command</span>
              <span className="tool-run-count">×{commandCount}</span>
            </span>
          )}
          {messageCount > 0 && (
            <span className="tool-run-chip other">
              <span className="tool-run-glyph">↘</span>
              <span className="tool-run-label">message</span>
              <span className="tool-run-count">×{messageCount}</span>
            </span>
          )}
          {otherCount > 0 && (
            <span className="tool-run-chip other">
              <span className="tool-run-glyph">⁂</span>
              <span className="tool-run-label">task</span>
              <span className="tool-run-count">×{otherCount}</span>
            </span>
          )}
        </div>
        <span className="tool-run-total">
          {count} note{count !== 1 ? "s" : ""}
        </span>
      </summary>
      <div className="tool-call-run-children">
        {children}
      </div>
    </details>
  );
}

function ToolDisclosure({
  event,
  bodyClassName,
  onOpenWorkspaceFile,
}: {
  event: EventRecord;
  bodyClassName: string;
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  const tool = toolBadgeFor(readToolName(event));
  const preview = previewForToolEvent(event, tool.label);
  const kindLabel = event.kind === "tool_call" ? "call" : "result";
  const diffPreview =
    parseEvent(event).diffPreview ||
    fallbackDiffPreviewForFileEdit(
      [event],
      event.kind === "tool_result" ? "applied" : "proposed",
    );
  return (
    <details className={`panel transcript codex ${event.kind} tool-disclosure`}>
      <summary className="transcript-summary">
        <div className="transcript-role">
          <span className={`tool-glyph ${tool.variant}`} aria-hidden>
            {tool.glyph}
          </span>
          <span className="tool-name">{tool.label}</span>
          <span className="badge tool-status pending">{kindLabel}</span>
          <span className="role-time">{formatEventTime(event.ts)}</span>
        </div>
        {preview ? <p className="transcript-preview">{preview}</p> : null}
        <MessageAttachments specs={attachmentSpecsFor(event)} />
      </summary>
      {diffPreview ? (
        <DiffPreview preview={diffPreview} onOpenWorkspaceFile={onOpenWorkspaceFile} />
      ) : (
        <pre className={bodyClassName}>{event.text}</pre>
      )}
    </details>
  );
}

function ToolPairCard({
  pair,
  onOpenWorkspaceFile,
}: {
  pair: ToolPair;
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  const { call, result } = pair;
  const kind = cardKindOf(call ?? result ?? ({} as EventRecord), pair);
  if (kind === "ask_question" && call) {
    return <AskUserQuestionCard event={call} questions={parseAskUserQuestion(call) ?? []} />;
  }
  if (kind === "send_file") {
    const sendUserFileEvent = call ?? result;
    if (sendUserFileEvent) return <SendUserFileCard event={sendUserFileEvent} />;
  }
  if (kind === "todo") {
    return <TodoToolPairCard pair={pair} />;
  }
  const status = result ? "complete" : "pending";
  const tool = toolBadgeFor(readToolName(call ?? result ?? ({} as EventRecord)));
  const diffPreview =
    (result ? parseEvent(result).diffPreview : null) ||
    (call ? parseEvent(call).diffPreview : null) ||
    fallbackDiffPreviewForFileEdit([call, result], result ? "applied" : "proposed");
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
          <span className="role-time">{formatEventTime(pair.ts)}</span>
        </div>
        {summary ? <p className="transcript-preview">{summary}</p> : null}
        <MessageAttachments specs={toolPairAttachments(pair)} />
      </summary>
      <div className="tool-pair-body">
        {diffPreview ? (
          <DiffPreview preview={diffPreview} onOpenWorkspaceFile={onOpenWorkspaceFile} />
        ) : (
          <>
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
          </>
        )}
      </div>
    </details>
  );
}

// The agent's SendUserFile hand-off. Rendered standalone (classified "content")
// with the sent files and a shortcut to the Files browser.
function SendUserFileCard({ event }: { event: EventRecord }) {
  const filesLink = useSessionFilesLink();
  const specs = attachmentSpecsFor(event);
  const { files, caption } = sendUserFileInput(event);
  const count = specs.length || files.length;
  return (
    <article className="panel transcript codex tool_call send-user-file">
      <div className="transcript-role">
        <span className="tool-glyph task" aria-hidden>
          ↧
        </span>
        <span className="tool-name">Sent you</span>
        <span className="send-user-file-count">
          {count} file{count === 1 ? "" : "s"}
        </span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
      </div>
      {caption ? <p className="send-user-file-caption">{caption}</p> : null}
      {specs.length > 0 ? (
        <MessageAttachments specs={specs} />
      ) : files.length > 0 ? (
        <ul className="send-user-file-names">
          {files.map((path, index) => (
            <li key={`${path}-${index}`} className="send-user-file-name">
              {path.split(/[\\/]/).pop() || path}
            </li>
          ))}
        </ul>
      ) : null}
      {filesLink ? (
        <button
          type="button"
          className="send-user-file-open"
          onClick={filesLink.openFilesBrowser}
        >
          Open in Files
        </button>
      ) : null}
    </article>
  );
}

function TaskReportPreview({ state }: { state: ReportPreviewState | null }) {
  if (!state || state.status === "loading") {
    return <p className="task-note-preview-note">Loading report…</p>;
  }
  if (state.status === "error") {
    return (
      <p className="task-note-preview-note is-error">
        Report preview unavailable — open the full file
      </p>
    );
  }
  const { preview } = state;
  if (preview.binary || preview.content === null) {
    return (
      <p className="task-note-preview-note is-error">
        Binary report — open the full file
      </p>
    );
  }
  return (
    <>
      <pre className="task-note-result">{preview.content}</pre>
      {preview.truncated ? (
        <p className="task-note-caption">
          Preview — first {Math.round(preview.content_bytes / 1024)} KB of the
          full report
        </p>
      ) : null}
    </>
  );
}

function TaskNotificationCard({
  view,
  event,
}: {
  view: TaskNotificationView;
  event: EventRecord;
}) {
  const [open, setOpen] = useState(false);
  const attachmentContext = useAttachmentContext();
  const badge = TASK_NOTIFICATION_BADGES[view.kind];
  const lamp = taskStatusLamp(view);
  const report = taskNoteReport(view, event);
  const { specs, reportSpec, inlineReports } = report;
  const previewState = useReportPreview(attachmentContext, reportSpec, open);
  const {
    headline,
    teaser: preview,
    usageParts,
    unavailable,
    hasBody,
    showLinks,
    supersedes,
    cutCaption,
  } = taskNoteBody(view, report, previewState);
  const hasReport = reportSpec !== null;

  const header = (
    <div className="transcript-role">
      <span className={`tool-glyph ${badge.variant}`} aria-hidden>
        {badge.glyph}
      </span>
      <span className="tool-name">{badge.label}</span>
      <span className="task-note-summary" title={headline}>
        {headline}
      </span>
      {lamp ? (
        <span className={`inst-lamp tone-${lamp.tone} task-note-status`}>
          {lamp.label}
        </span>
      ) : null}
      <span className="role-time">{formatEventTime(event.ts)}</span>
    </div>
  );

  if (!hasBody) {
    // No body means no `event`, and the teaser is only ever `event`.
    return (
      <article className="panel transcript codex task-notification">
        {header}
      </article>
    );
  }

  const renderCutCaption = (body: string | null, truncated: boolean) => {
    const caption = cutCaption(body, truncated);
    return caption ? (
      <p className="task-note-caption">
        {caption === "retained"
          ? "Preview shown — full output attached below"
          : "Preview — the rest was not retained"}
      </p>
    ) : null;
  };

  return (
    <details
      className="panel transcript codex task-notification"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="transcript-summary">
        {header}
        {preview ? <p className="transcript-preview">{preview}</p> : null}
      </summary>
      <div className="task-note-body">
        {view.resultPreview && !supersedes(view.resultPreview) ? (
          <pre className="task-note-result">{view.resultPreview}</pre>
        ) : null}
        {renderCutCaption(view.resultPreview, view.resultTruncated)}
        {view.event && !supersedes(view.event) ? (
          <pre className="task-note-result">{view.event}</pre>
        ) : null}
        {renderCutCaption(view.event, view.eventTruncated)}
        {view.note ? <p className="task-note-text">{view.note}</p> : null}
        {renderCutCaption(view.note, view.noteTruncated)}
        {usageParts.length > 0 ? (
          <p className="task-note-usage">{usageParts.join(" · ")}</p>
        ) : null}
        {inlineReports.length > 0 || specs.length > 0 ? (
          <div className="task-note-report">
            <span className="task-note-report-label">
              {hasReport || inlineReports.length > 0
                ? "Full report"
                : "Full output"}
            </span>
            {inlineReports.map((text, i) =>
              text.trim() ? (
                <pre className="task-note-result" key={i}>
                  {text}
                </pre>
              ) : (
                <p className="task-note-preview-note" key={i}>
                  No output
                </p>
              ),
            )}
            {reportSpec ? <TaskReportPreview state={previewState} /> : null}
            {showLinks ? (
              <MessageAttachments specs={specs} />
            ) : null}
          </div>
        ) : unavailable ? (
          <p className="task-note-unavailable">{unavailable}</p>
        ) : null}
      </div>
    </details>
  );
}

// Fixed badge for the cross-backend Todo card. Codex's todo_list is a
// built-in item (not a tool the agent invokes), so don't borrow Claude's
// "TodoWrite" tool name to look it up — render the same badge for both.
const TODO_BADGE = { glyph: "☑", variant: "todo", label: "Todos" } as const;

// The live list lives in the docked TaskProgressDock; in the transcript the
// todo event is just a chronological marker ("when did this list exist") that
// expands on demand, so it doesn't duplicate the dock or shove the
// conversation down.
function TodoMarkerCard({ todos, ts }: { todos: TodoEntry[] | null; ts: string }) {
  const progress = summarizeTodos(todos);
  const summary = progress
    ? `${progress.total} item${progress.total === 1 ? "" : "s"} · ${progress.completed}/${progress.total} done`
    : "no items";
  return (
    <details className="panel transcript codex todo-marker-card">
      <summary className="transcript-summary todo-marker-summary">
        <span className={`tool-glyph ${TODO_BADGE.variant}`} aria-hidden>
          {TODO_BADGE.glyph}
        </span>
        <span className="tool-name">{TODO_BADGE.label}</span>
        <span className="todo-marker-meta">{summary}</span>
        <span className="role-time">{formatEventTime(ts)}</span>
      </summary>
      <div className="todo-marker-body">
        <TodoListBody todos={todos} />
      </div>
    </details>
  );
}

function TodoToolCard({ event }: { event: EventRecord }) {
  return <TodoMarkerCard todos={readTodoEntries(event)} ts={event.ts} />;
}

function TodoToolPairCard({ pair }: { pair: ToolPair }) {
  const todos = readTodoEntries(pair.result) ?? readTodoEntries(pair.call);
  return <TodoMarkerCard todos={todos} ts={pair.ts} />;
}

function AskUserQuestionCard({
  event,
  questions,
}: {
  event: EventRecord;
  questions: AskUserQuestion[];
}) {
  const controller = useAskQuestionController();
  const toolUseId = askToolUseId(event);
  const resolution: AskQuestionResolution = toolUseId
    ? controller.resolution(toolUseId)
    : { state: "pending" };
  const formState = askFormState(controller.snapshot, event, resolution);
  const closedResultEvent =
    resolution.state === "closed_unanswered" ? resolution.resultEvent : null;
  const notAnswered = resolution.state === "closed_unanswered" || formState === "gone";

  return (
    <article
      className="panel transcript codex tool_call ask-user-question"
      data-ask-tool-use-id={toolUseId ?? undefined}
    >
      <div className="transcript-role">
        <span className="tool-glyph task" aria-hidden>?</span>
        <span className="tool-name">Ask you</span>
        {resolution.state === "answered" ? (
          <span className="badge tool-status complete">answered</span>
        ) : resolution.state === "cancelled" ? (
          <span className="badge tool-status cancelled">cancelled</span>
        ) : notAnswered ? (
          <span className="badge tool-status unanswered">not answered</span>
        ) : (
          <span className="badge tool-status pending">awaiting answer</span>
        )}
        <span className="role-time">{formatEventTime(event.ts)}</span>
      </div>
      <AskQuestionForm
        event={event}
        questions={questions}
        resolution={resolution}
      />
      {resolution.state === "cancelled" ? (
        <div className="ask-question-closed">
          <p className="ask-question-closed-note">You cancelled this question.</p>
        </div>
      ) : notAnswered ? (
        <div className="ask-question-closed">
          <p className="ask-question-closed-note">
            This question ended without a recorded answer and can no longer be
            answered.
          </p>
          {closedResultEvent?.text?.trim() ? (
            <details className="ask-question-diagnostic">
              <summary>Provider result</summary>
              <pre className="output">{closedResultEvent.text}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function AskAnswerSummaryCard({ event }: { event: EventRecord }) {
  const answers = askAnswerEntries(event);

  return (
    <article
      className="panel transcript codex user_input ask-answer-summary"
      aria-label="Your answers"
    >
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
      <div className="transcript-meta">
        <span className="badge user">you</span>
        <span className="badge neutral ask-answer-chip">answered</span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
      </div>
    </article>
  );
}

function HeuristicCard({ event }: { event: EventRecord }) {
  return (
    <article className={`panel transcript ${event.kind}`}>
      <div className="transcript-role">
        <span className="badge neutral">{event.kind.replaceAll("_", " ")}</span>
        <span className="role-time">{formatEventTime(event.ts)}</span>
        <CopyMessageButton text={event.text} />
      </div>
      <pre>{event.text}</pre>
      <MessageAttachments specs={attachmentSpecsFor(event)} />
    </article>
  );
}
