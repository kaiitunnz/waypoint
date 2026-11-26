"use client";

import {
  askFormState,
  askFormView,
  askToolUseId,
  clearAskAnswers,
  EMPTY_ASK_DRAFT,
  serializeAnswers,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";
import type { EventRecord } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";

import { useAskController } from "./AskController";
import styles from "./AskForm.module.css";
import { QuestionEditor } from "./QuestionEditor";

interface AskFormProps {
  event: EventRecord;
  questions: AskUserQuestion[];
}

// Answers or cancels one pending question through the session's shared controller.
export function AskForm({ event, questions }: AskFormProps) {
  const controller = useAskController();
  const toolUseId = askToolUseId(event);
  const draft = toolUseId ? controller.draft(toolUseId) : EMPTY_ASK_DRAFT;
  const operation = toolUseId ? controller.operation(toolUseId) : null;
  const failure = toolUseId ? controller.failure(toolUseId) : null;
  const retrying = toolUseId ? controller.retrying(toolUseId) : false;
  const resolution = toolUseId ? controller.resolution(toolUseId) : ({ state: "pending" } as const);
  const formState = askFormState(controller.snapshot, event, resolution);
  if (formState === "resolved" || formState === "gone" || !toolUseId) return null;

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

  const status = retrying ? (
    <p className={styles.note}>Waiting for the agent…</p>
  ) : failure ? (
    <div className={styles.failure} role="alert">
      <p className={failure.notReady ? undefined : styles.error}>
        {failure.notReady ? "The agent isn't ready for a reply yet" : failure.message}
      </p>
      <ActionChip icon="refresh" disabled={busy} onClick={failure.operation === "send" ? submit : cancel}>
        Retry
      </ActionChip>
    </div>
  ) : formState === "starting" || formState === "loading" ? (
    <p className={styles.note}>Waiting for the agent to accept a reply</p>
  ) : formState === "unavailable" ? (
    <div className={styles.failure}>
      <p>{controller.canReattach ? "This session isn't running" : "The agent can't take a reply right now"}</p>
      {controller.canReattach ? (
        <ActionChip icon="refresh" onClick={controller.reattach}>
          Reattach
        </ActionChip>
      ) : null}
    </div>
  ) : null;

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
      {status}
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
