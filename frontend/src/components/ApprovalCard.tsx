import { useEffect, useRef, useState, type ReactNode } from "react";

import { DiffPreview } from "@/components/DiffPreview";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { CopyMessageButton } from "@/components/CopyMessageButton";
import {
  normalizeToolName,
  parseEvent,
  type EventDiffPreview,
  type PlanDecision,
} from "@/lib/events";
import type { EventRecord } from "@/lib/types";

export interface SharedApprovalAction {
  id: string;
  label: string;
  className: "primary" | "secondary";
  loadingLabel?: string;
  onSelect: (note?: string) => void | Promise<void>;
}

interface SharedApprovalCardProps {
  badge: string;
  children: ReactNode;
  actions?: SharedApprovalAction[];
  className?: string;
  copyLabel?: string;
  copyText: string;
  notePlaceholder?: string;
  supportsNote?: boolean;
  timeLabel?: string;
}

export function SharedApprovalCard({
  badge,
  children,
  actions = [],
  className = "",
  copyLabel = "Copy approval body",
  copyText,
  notePlaceholder = "Add a note…",
  supportsNote = false,
  timeLabel,
}: SharedApprovalCardProps) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleAction(action: SharedApprovalAction) {
    if (pendingAction) {
      return;
    }
    setPendingAction(action.id);
    try {
      await action.onSelect(noteText.trim() || undefined);
    } finally {
      if (mountedRef.current) {
        setPendingAction(null);
      }
    }
  }

  const cardClassName = `panel approval${className ? ` ${className}` : ""}`;

  return (
    <section className={cardClassName}>
      <div className="session-row">
        <span className="badge fidelity structured">{badge}</span>
        {timeLabel ? <span className="role-time">{timeLabel}</span> : null}
        <CopyMessageButton text={copyText} label={copyLabel} />
      </div>
      {children}
      {supportsNote && actions.length > 0 ? (
        noteOpen ? (
          <div className="approval-note-wrap ask-question-note">
            <textarea
              className="ask-question-note-input"
              value={noteText}
              onChange={(event) => setNoteText(event.target.value)}
              placeholder={notePlaceholder}
              rows={2}
            />
            <button
              type="button"
              className="link-button"
              onClick={() => setNoteOpen(false)}
            >
              Hide note
            </button>
          </div>
        ) : (
          <div className="approval-note-wrap">
            <button
              type="button"
              className="link-button ask-question-note-toggle"
              onClick={() => setNoteOpen(true)}
            >
              + Add note
            </button>
          </div>
        )
      ) : null}
      {actions.length > 0 ? (
        <div className="action-row">
          {actions.map((action) => (
            <button
              key={action.id}
              className={action.className}
              onClick={() => void handleAction(action)}
              type="button"
              disabled={pendingAction !== null}
            >
              {pendingAction === action.id && action.loadingLabel
                ? action.loadingLabel
                : action.label}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ApprovalRequestCard({
  event,
  onDecide,
  supportsNote = false,
  decisions,
}: {
  event: EventRecord;
  onDecide: (decision: string, text?: string, approvalId?: string) => void | Promise<void>;
  supportsNote?: boolean;
  decisions?: readonly string[];
}) {
  const diffPreview = parseEvent(event).diffPreview;
  const toolName = normalizeToolName(
    typeof event.metadata.tool_name === "string" ? event.metadata.tool_name : null,
  );
  const toolInput =
    event.metadata.tool_input && typeof event.metadata.tool_input === "object"
      ? (event.metadata.tool_input as Record<string, unknown>)
      : null;
  const copyText = approvalCopyText(event.text, toolName, toolInput);
  const approvalId =
    typeof event.metadata?.approval_id === "string"
      ? (event.metadata.approval_id as string)
      : undefined;
  // Escalation decisions are backend-specific; only render the ones the
  // session's backend honours. Approve/Decline/Cancel are universal — every
  // backend maps them to allow/deny — so they always show.
  const allowed = new Set(decisions ?? []);

  return (
    <SharedApprovalCard
      badge="approval"
      copyText={copyText}
      notePlaceholder="Add a note to your approval or decline…"
      supportsNote={supportsNote}
      actions={[
        {
          id: "accept",
          label: "Approve",
          className: "primary",
          onSelect: (note) => onDecide("accept", note, approvalId),
        },
        ...(allowed.has("acceptForSession")
          ? [
              {
                id: "acceptForSession",
                label: "Approve for session",
                className: "secondary" as const,
                onSelect: (note?: string) =>
                  onDecide("acceptForSession", note, approvalId),
              },
            ]
          : []),
        ...(allowed.has("acceptAlways")
          ? [
              {
                id: "acceptAlways",
                label: "Always allow",
                className: "secondary" as const,
                onSelect: (note?: string) =>
                  onDecide("acceptAlways", note, approvalId),
              },
            ]
          : []),
        {
          id: "decline",
          label: "Decline",
          className: "secondary",
          onSelect: (note) => onDecide("decline", note, approvalId),
        },
        {
          id: "cancel",
          label: "Cancel",
          className: "secondary",
          onSelect: (note) => onDecide("cancel", note, approvalId),
        },
      ]}
    >
      <ApprovalCardBody
        eventText={event.text}
        toolName={toolName}
        toolInput={toolInput}
        diffPreview={diffPreview}
      />
    </SharedApprovalCard>
  );
}

const PLAN_ACTION_DEFINITIONS: ReadonlyArray<{
  id: PlanDecision;
  label: string;
  loadingLabel: string;
  className: "primary" | "secondary";
}> = [
  {
    id: "accept",
    label: "Approve",
    loadingLabel: "Approving…",
    className: "primary",
  },
  {
    id: "acceptForSession",
    label: "Approve for session",
    loadingLabel: "Approving…",
    className: "secondary",
  },
  {
    id: "decline",
    label: "Decline",
    loadingLabel: "Declining…",
    className: "secondary",
  },
  {
    id: "cancel",
    label: "Cancel",
    loadingLabel: "Cancelling…",
    className: "secondary",
  },
];

export function PlanApprovalCard({
  agentLabel,
  canApprove = false,
  className,
  decisions,
  onDecide,
  plan,
  prompt = "Approve plan and exit plan mode",
  timeLabel,
}: {
  agentLabel: string;
  canApprove?: boolean;
  className?: string;
  decisions?: ReadonlyArray<PlanDecision>;
  onDecide?: (decision: PlanDecision, note?: string) => void | Promise<void>;
  plan: string;
  prompt?: string;
  timeLabel?: string;
}) {
  const allowed = new Set<PlanDecision>(
    decisions && decisions.length > 0
      ? decisions
      : PLAN_ACTION_DEFINITIONS.map((entry) => entry.id),
  );
  const actions =
    canApprove && onDecide
      ? PLAN_ACTION_DEFINITIONS.filter((entry) => allowed.has(entry.id)).map(
          (entry) => ({
            id: entry.id,
            label: entry.label,
            loadingLabel: entry.loadingLabel,
            className: entry.className,
            onSelect: (note?: string) => onDecide(entry.id, note),
          }),
        )
      : [];
  return (
    <SharedApprovalCard
      badge={`${agentLabel} plan`}
      className={className}
      copyLabel="Copy plan"
      copyText={plan}
      notePlaceholder="Add a note to your decision…"
      supportsNote={canApprove}
      timeLabel={timeLabel}
      actions={actions}
    >
      <ApprovalPlanBody plan={plan} prompt={prompt} />
    </SharedApprovalCard>
  );
}

function approvalCopyText(
  eventText: string,
  toolName: string | null,
  toolInput: Record<string, unknown> | null,
): string {
  if (toolName === "ExitPlanMode" && typeof toolInput?.plan === "string") {
    return toolInput.plan as string;
  }
  if (
    (toolName === "Task" || toolName === "Agent") &&
    typeof toolInput?.prompt === "string"
  ) {
    return toolInput.prompt as string;
  }
  if (toolName === "Bash" && typeof toolInput?.command === "string") {
    return toolInput.command as string;
  }
  if (isApprovalWorkflowTool(toolName) && typeof toolInput?.script === "string") {
    return toolInput.script as string;
  }
  return eventText;
}

function ApprovalCardBody({
  eventText,
  toolName,
  toolInput,
  diffPreview,
}: {
  eventText: string;
  toolName: string | null;
  toolInput: Record<string, unknown> | null;
  diffPreview?: EventDiffPreview | null;
}) {
  if (diffPreview) {
    return (
      <>
        <p className="approval-prompt">{eventText}</p>
        <DiffPreview preview={diffPreview} />
      </>
    );
  }
  if (toolName === "ExitPlanMode" && typeof toolInput?.plan === "string") {
    return <ApprovalPlanBody plan={toolInput.plan as string} />;
  }
  if (
    (toolName === "Task" || toolName === "Agent") &&
    toolInput &&
    typeof toolInput.prompt === "string"
  ) {
    const description =
      typeof toolInput.description === "string" ? (toolInput.description as string) : "";
    const subagent =
      typeof toolInput.subagent_type === "string"
        ? (toolInput.subagent_type as string)
        : "";
    return (
      <>
        <p className="approval-prompt">
          Approve subagent task
          {description ? `: ${description}` : ""}
          {subagent ? ` (via ${subagent})` : ""}
        </p>
        <div className="approval-plan">
          <MarkdownMessage text={toolInput.prompt as string} />
        </div>
      </>
    );
  }
  if (toolName === "Bash" && typeof toolInput?.command === "string") {
    const desc =
      typeof toolInput.description === "string"
        ? (toolInput.description as string)
        : "";
    return (
      <>
        <p className="approval-prompt">
          Approve Bash command{desc ? `: ${desc}` : ""}
        </p>
        <pre className="approval-shell">{toolInput.command as string}</pre>
      </>
    );
  }
  if (isApprovalWorkflowTool(toolName) && typeof toolInput?.script === "string") {
    return (
      <>
        <p className="approval-prompt">
          Review this dynamic workflow before it runs
        </p>
        <pre className="approval-shell">{toolInput.script as string}</pre>
      </>
    );
  }
  if (isApprovalFileEditTool(toolName)) {
    const path = approvalFileEditPath(toolInput);
    return (
      <>
        <p className="approval-prompt">
          Approve {toolName}
          {path ? ` on ${path}` : ""}
        </p>
        <p className="diff-unavailable">Diff preview was not included by the backend.</p>
      </>
    );
  }
  return <pre>{eventText}</pre>;
}

function ApprovalPlanBody({
  plan,
  prompt = "Approve plan and exit plan mode",
}: {
  plan: string;
  prompt?: string;
}) {
  return (
    <>
      <p className="approval-prompt">{prompt}</p>
      <div className="approval-plan">
        <MarkdownMessage text={plan} />
      </div>
    </>
  );
}

function isApprovalFileEditTool(toolName: string | null): boolean {
  return (
    toolName === "Edit" ||
    toolName === "MultiEdit" ||
    toolName === "Write" ||
    toolName === "NotebookEdit"
  );
}

function isApprovalWorkflowTool(toolName: string | null): boolean {
  return toolName === "Workflow" || toolName === "RunWorkflow";
}

function approvalFileEditPath(toolInput: Record<string, unknown> | null): string | null {
  if (!toolInput) {
    return null;
  }
  const value = toolInput.file_path ?? toolInput.path ?? toolInput.notebook_path;
  return typeof value === "string" && value ? value : null;
}
