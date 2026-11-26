"use client";

import { NOT_READY_MESSAGE } from "@waypoint/web-core/pendingAskActions";
import {
  askFormState,
  askFormView,
  askToolUseId,
  clearAskAnswers,
  serializeAnswers,
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
}

export function AskForm({ event, questions }: AskFormProps) {
  const controller = useAskController();
  const toolUseId = askToolUseId(event);
  if (!toolUseId) return null;
  const formState = askFormState(controller.snapshot, event, controller.resolution(toolUseId));
  if (formState === "resolved" || formState === "gone") return null;

  const draft = controller.draft(toolUseId);
  const operation = controller.operation(toolUseId);
  const failure = controller.failure(toolUseId);
  const busy = operation !== null;
  const actionable = formState === "actionable";
  const { hasAnswer } = askFormView(questions, draft);
  const canSend = actionable && !busy && hasAnswer;

  const submit = () => {
    if (!canSend) return;
    const payload = serializeAnswers(questions, draft);
    if (payload) void controller.answer(toolUseId, payload.text, payload.answers);
  };
  const cancel = () => void controller.cancel(toolUseId);

  const phase: AskPhase = busy
    ? { state: "sending", retrying: controller.retrying(toolUseId) }
    : failure
      ? { state: "error", message: failure.notReady ? NOT_READY_MESSAGE : failure.message }
      : { state: "idle" };
  const reattach = formState === "unavailable" && controller.canReattach && !failure;

  return (
    <div className={styles.form}>
      <QuestionEditor
        questions={questions}
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
