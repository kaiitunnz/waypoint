"use client";

import type { EventRecord } from "@waypoint/web-core/types";
import { askCardState, parseAskUserQuestion } from "@waypoint/web-core/session/askQuestion";

import { useAskState } from "../SessionContext";
import { EventTime } from "./EventTime";
import { RunLine } from "./RunLine";
import styles from "./AskCard.module.css";

export function AskCard({ event }: { event: EventRecord }) {
  const { evidence, snapshot } = useAskState();
  const questions = parseAskUserQuestion(event) ?? [];
  const { waiting, outcome, answers } = askCardState(event, evidence, snapshot);
  const first = questions[0]?.question ?? "";

  return (
    <RunLine
      asked={waiting}
      phrase={`Asked you${first ? `: ${first}` : ""}`}
      trailing={
        <>
          {outcome ? <span className={styles.outcome}>{outcome}</span> : null}
          <EventTime ts={event.ts} />
        </>
      }
    >
      <div className={styles.detail}>
        {questions.map((question, index) => {
          const answer = answers.find((entry) => entry.question === question.question)?.answer;
          return (
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
              {answer ? <p className={styles.answer}>{answer}</p> : null}
            </div>
          );
        })}
      </div>
    </RunLine>
  );
}
