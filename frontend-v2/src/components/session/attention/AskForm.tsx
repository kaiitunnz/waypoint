"use client";

import type { KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { NOT_READY_MESSAGE } from "@waypoint/web-core/pendingAskActions";
import {
  askFormState,
  askFormView,
  askToolUseId,
  clearAskAnswers,
  serializeAnswers,
  type AskFormState,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";
import type { EventRecord } from "@waypoint/web-core/types";

import { AskStatus } from "@/components/sessions/AskStatus";
import type { AskPhase } from "@/components/sessions/useAskResponder";
import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";

import { useAskController } from "../SessionContext";
import styles from "./AskForm.module.css";
import { QuestionEditor } from "./QuestionEditor";

interface AskFormProps {
  event: EventRecord;
  questions: AskUserQuestion[];
  // The card's heading shows the current question.
  headed?: boolean;
}

export function AskForm({ event, questions, headed = false }: AskFormProps) {
  const controller = useAskController();
  const toolUseId = askToolUseId(event);
  if (!toolUseId) return null;
  const formState = askFormState(controller.snapshot, event, controller.resolution(toolUseId));
  if (formState === "resolved" || formState === "gone") return null;
  return <AskFormView toolUseId={toolUseId} questions={questions} headed={headed} formState={formState} />;
}

interface AskFormViewProps {
  toolUseId: string;
  questions: AskUserQuestion[];
  headed?: boolean;
  formState: Exclude<AskFormState, "resolved" | "gone">;
  // What another surface is doing with this question; it holds the form while busy.
  held?: AskPhase;
  // Cmd/Ctrl+Enter anywhere in the form sends it.
  submitKey?: boolean;
}

// The editor and its actions for one pending question, from the controller.
export function AskFormView({ toolUseId, questions, headed = false, formState, held, submitKey }: AskFormViewProps) {
  const controller = useAskController();
  const draft = controller.draft(toolUseId);
  const operation = controller.operation(toolUseId);
  const failure = controller.failure(toolUseId);
  const waiting = held?.state === "sending" || held?.state === "sent";
  const busy = operation !== null || waiting;
  const actionable = formState === "actionable";
  const { hasAnswer } = askFormView(questions, draft);
  const canSend = actionable && !busy && hasAnswer;

  const submit = () => {
    if (!canSend) return;
    const payload = serializeAnswers(questions, draft);
    if (payload) void controller.answer(toolUseId, payload.text, payload.answers);
  };
  const cancel = () => void controller.cancel(toolUseId);
  // The note field handles the shortcut itself; a focused option must not toggle.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!submitKey || event.defaultPrevented || !isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    submit();
  };

  const phase: AskPhase = operation
    ? { state: "sending", retrying: controller.retrying(toolUseId) }
    : failure
      ? { state: "error", message: failure.notReady ? NOT_READY_MESSAGE : failure.message }
      : (held ?? { state: "idle" });
  const reattach = formState === "unavailable" && controller.canReattach && !failure;

  return (
    <div className={styles.form} onKeyDown={onKeyDown}>
      <QuestionEditor
        questions={questions}
        headed={headed}
        draft={draft}
        onDraft={(update) => controller.updateDraft(toolUseId, update)}
        disabled={!actionable || busy}
        locked={busy}
        onSubmit={submit}
      />
      {reattach ? (
        <div className={styles.reattach}>
          <p>This session isn&apos;t running</p>
          <ActionChip icon="refresh" onClick={controller.reattach}>
            Reattach
          </ActionChip>
        </div>
      ) : (
        <AskStatus
          availability={formState === "loading" ? "starting" : formState}
          phase={phase}
          onRetry={failure ? (failure.operation === "send" ? submit : cancel) : undefined}
        />
      )}
      <div className={styles.actions}>
        <Button variant="primary" size="sm" disabled={!canSend} busy={operation === "send"} onClick={submit}>
          Send
        </Button>
        {hasAnswer ? (
          <Button
            variant="text"
            size="sm"
            disabled={busy || !actionable}
            onClick={() => controller.updateDraft(toolUseId, clearAskAnswers)}
          >
            Clear
          </Button>
        ) : null}
        <Button
          variant="text"
          size="sm"
          className={styles.end}
          disabled={!actionable || busy}
          busy={operation === "cancel"}
          onClick={cancel}
        >
          Cancel question
        </Button>
      </div>
    </div>
  );
}
