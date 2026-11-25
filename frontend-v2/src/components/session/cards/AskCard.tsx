"use client";

import type { EventRecord } from "@waypoint/web-core/types";
import { askCardState, type AskUserQuestion } from "@waypoint/web-core/session/askQuestion";
import { formatEventTime } from "@waypoint/web-core/session/cardText";

import { useAskState } from "../SessionContext";
import { RunLine } from "./RunLine";
import styles from "./AskCard.module.css";

// A question the agent asked, read-only: one line on the route that opens to the
// question, its options and the answer. Answering happens in the dock above the composer.
export function AskCard({ event, questions }: { event: EventRecord; questions: AskUserQuestion[] }) {
  const { evidence, snapshot } = useAskState();
  const { toolUseId, waiting, outcome, answers } = askCardState(event, evidence, snapshot);
  const first = questions[0]?.question ?? "";

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
