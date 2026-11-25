"use client";

import type { EventRecord } from "@waypoint/web-core/types";
import {
  askFormState,
  askToolUseId,
  resolveAskQuestion,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";
import { askAnswerEntries, formatEventTime } from "@waypoint/web-core/session/cardText";

import { useAskState } from "../SessionContext";
import { RunLine } from "./RunLine";
import styles from "./AskCard.module.css";

// A question the agent asked, read-only: one line on the route that opens to the
// question, its options and the answer. Answering happens in the dock above the composer.
export function AskCard({ event, questions }: { event: EventRecord; questions: AskUserQuestion[] }) {
  const { evidence, snapshot } = useAskState();
  const toolUseId = askToolUseId(event);
  const resolution = toolUseId ? resolveAskQuestion(toolUseId, evidence) : ({ state: "pending" } as const);
  const form = askFormState(snapshot, event, resolution);
  const first = questions[0]?.question ?? "";
  const waiting = resolution.state === "pending" && form !== "gone";
  const answers = resolution.state === "answered" ? askAnswerEntries(resolution.answerEvent) : [];

  const outcome =
    resolution.state === "answered"
      ? "answered"
      : resolution.state === "cancelled"
        ? "cancelled"
        : waiting
          ? null
          : "not answered";

  return (
    <RunLine
      asked={waiting}
      defaultOpen={false}
      phrase={
        <>
          Asked you{first ? `: ${first}` : ""}
          {outcome ? <span className={styles.outcome}>{outcome}</span> : null}
        </>
      }
      trailing={<time>{formatEventTime(event.ts)}</time>}
    >
      <div className={styles.detail} data-ask-tool-use-id={toolUseId ?? undefined}>
        {questions.map((question, index) => (
          <div key={index} className={styles.question}>
            <p className={styles.text}>{question.question}</p>
            <ul className={styles.options}>
              {question.options.map((option) => (
                <li key={option.label}>
                  <span className={styles.label}>{option.label}</span>
                  {option.description ? <span className={styles.desc}>{option.description}</span> : null}
                </li>
              ))}
            </ul>
            {answers[index]?.answer ? <p className={styles.answer}>{answers[index].answer}</p> : null}
          </div>
        ))}
      </div>
    </RunLine>
  );
}
