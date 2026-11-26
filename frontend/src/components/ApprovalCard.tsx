import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { DiffPreview } from "@/components/DiffPreview";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { CopyMessageButton } from "@/components/CopyMessageButton";
import { PLAN_DECISIONS, type PlanDecision } from "@waypoint/web-core/events";
import {
  approvalActions,
  approvalBody,
  approvalCopyText,
  approvalId,
  approvalPrompt,
  planApprovalActions,
  type ApprovalBody,
} from "@waypoint/web-core/session/approvals";
import type { EventRecord } from "@waypoint/web-core/types";

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

  function handleNoteKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!isModifiedEnterShortcut(event)) {
      return;
    }
    event.preventDefault();
    if (pendingAction) {
      return;
    }
    const primaryAction =
      actions.find((action) => action.className === "primary") ?? actions[0];
    if (!primaryAction) {
      return;
    }
    void handleAction(primaryAction);
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
              onKeyDown={handleNoteKeyDown}
              placeholder={notePlaceholder}
              rows={2}
              aria-keyshortcuts="Meta+Enter Control+Enter"
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
  const id = approvalId(event);
  const body = approvalBody(event);
  return (
    <SharedApprovalCard
      badge="approval"
      copyText={approvalCopyText(body, event.text)}
      notePlaceholder="Add a note to your approval or decline…"
      supportsNote={supportsNote}
      actions={approvalActions(decisions).map((action) => ({
        id: action.id,
        label: action.label,
        className: action.primary ? "primary" : "secondary",
        onSelect: (note?: string) => onDecide(action.id, note, id),
      }))}
    >
      <ApprovalCardBody body={body} />
    </SharedApprovalCard>
  );
}

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
  const actions =
    canApprove && onDecide
      ? planApprovalActions(decisions?.length ? decisions : PLAN_DECISIONS).map((entry) => ({
          id: entry.id,
          label: entry.label,
          loadingLabel: entry.loadingLabel,
          className: entry.primary ? ("primary" as const) : ("secondary" as const),
          onSelect: (note?: string) => onDecide(entry.id, note),
        }))
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

function ApprovalCardBody({ body }: { body: ApprovalBody }) {
  const prompt = approvalPrompt(body);
  switch (body.kind) {
    case "diff":
      return (
        <>
          <p className="approval-prompt">{prompt}</p>
          <DiffPreview preview={body.preview} />
        </>
      );
    case "plan":
      return <ApprovalPlanBody plan={body.plan} />;
    case "task":
      return (
        <>
          <p className="approval-prompt">{prompt}</p>
          <div className="approval-plan">
            <MarkdownMessage text={body.prompt} />
          </div>
        </>
      );
    case "command":
      return (
        <>
          <p className="approval-prompt">{prompt}</p>
          <pre className="approval-shell">{body.command}</pre>
        </>
      );
    case "workflow":
      return (
        <>
          <p className="approval-prompt">{prompt}</p>
          <pre className="approval-shell">{body.script}</pre>
        </>
      );
    case "edit":
      return (
        <>
          <p className="approval-prompt">{prompt}</p>
          <p className="diff-unavailable">Diff preview was not included by the backend.</p>
        </>
      );
    case "raw":
      return <pre>{body.text}</pre>;
  }
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
