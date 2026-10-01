"use client";

import { useEffect, useId, useState } from "react";

import { AskQuestionForm, parseAskUserQuestion } from "@/components/AskQuestion";
import type { PendingQuestion } from "@/lib/types";

// Single up-chevron; orientation handled with a CSS rotation, matching the
// task-progress and side-question docks so the docks read as one family.
function ChevronIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 10l4-4 4 4" />
    </svg>
  );
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function promptFor(question: PendingQuestion): string {
  return parseAskUserQuestion(question.event)?.[0]?.question ?? question.event.text;
}

function countLabel(count: number): string {
  return count === 1 ? "Question waiting" : `${count} questions waiting`;
}

// Every open AskUserQuestion of the session, oldest first: a strip above the
// composer that expands into an accordion with one answer form open. It never
// auto-expands.
export function PendingQuestionDock({
  questions,
  onReveal,
}: {
  questions: PendingQuestion[];
  onReveal: (question: PendingQuestion) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const panelId = useId();

  useEffect(() => {
    if (!expanded) return;
    const onKey = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  useEffect(() => {
    if (!questions.length) setExpanded(false);
  }, [questions.length]);

  if (!questions.length) return null;

  const count = questions.length;
  const oldest = questions[0];
  const activeId = questions.some((question) => question.tool_use_id === openId)
    ? openId
    : oldest.tool_use_id;

  return (
    <div className={`qd-dock${expanded ? " expanded" : ""}`}>
      {expanded ? (
        <>
          <button
            type="button"
            className="qd-dock-scrim"
            aria-label="Collapse questions"
            onClick={() => setExpanded(false)}
          />
          <div
            className="qd-dock-panel"
            id={panelId}
            role="dialog"
            aria-label="Questions waiting"
          >
            <div className="qd-dock-panel-head">
              <span className="qd-dock-panel-title">Questions waiting</span>
              <span className="qd-dock-count">{count}</span>
              <button
                type="button"
                className="qd-dock-collapse"
                aria-label="Collapse questions"
                onClick={() => setExpanded(false)}
              >
                <ChevronIcon />
              </button>
            </div>
            <div className="qd-dock-entries">
              {questions.map((question) => {
                const prompt = promptFor(question);
                if (question.tool_use_id !== activeId) {
                  return (
                    <button
                      key={question.tool_use_id}
                      type="button"
                      className="qd-dock-row"
                      aria-expanded={false}
                      onClick={() => setOpenId(question.tool_use_id)}
                    >
                      <span className="qd-dock-row-chevron" aria-hidden>
                        <ChevronIcon />
                      </span>
                      <span className="qd-dock-row-text">{prompt}</span>
                      <span className="qd-dock-row-time">
                        {formatTime(question.event.ts)}
                      </span>
                    </button>
                  );
                }
                const parsed = parseAskUserQuestion(question.event);
                return (
                  <section
                    key={question.tool_use_id}
                    className="qd-dock-entry"
                    aria-label={prompt}
                  >
                    <div className="qd-dock-entry-head">
                      <span className="qd-dock-entry-time">
                        asked {formatTime(question.event.ts)}
                      </span>
                      {parsed && parsed.length > 1 ? (
                        <span>
                          {parsed.length} questions
                        </span>
                      ) : null}
                      <button
                        type="button"
                        className="link-button qd-dock-reveal"
                        onClick={() => {
                          setExpanded(false);
                          onReveal(question);
                        }}
                      >
                        Show in transcript
                      </button>
                    </div>
                    {parsed ? (
                      <AskQuestionForm
                        event={question.event}
                        questions={parsed}
                        resolution={{ state: "pending" }}
                      />
                    ) : (
                      <p className="qd-dock-entry-fallback">{question.event.text}</p>
                    )}
                  </section>
                );
              })}
            </div>
          </div>
        </>
      ) : null}
      <div className="qd-dock-strip">
        <button
          type="button"
          className="qd-dock-toggle"
          aria-expanded={expanded}
          aria-controls={expanded ? panelId : undefined}
          aria-label={`${countLabel(count)}: ${promptFor(oldest)}`}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="qd-dock-glyph" aria-hidden>
            ?
          </span>
          <span className="qd-dock-label">
            <span className="qd-dock-kicker">{countLabel(count)}</span>
            <span className="qd-dock-prompt">{promptFor(oldest)}</span>
          </span>
          <span className="qd-dock-count" aria-hidden>
            {count}
          </span>
          <span className="qd-dock-chevron" aria-hidden>
            <ChevronIcon />
          </span>
        </button>
      </div>
      <span className="sr-only" aria-live="polite">
        {countLabel(count)}
      </span>
    </div>
  );
}
