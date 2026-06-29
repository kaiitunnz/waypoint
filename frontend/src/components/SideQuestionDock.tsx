"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { dismissSideQuestion, forkSideQuestion } from "@/lib/api";
import { type SideQuestion } from "@/lib/types";

// Single up-chevron; orientation handled with a CSS rotation, matching the
// task-progress dock so the two docks read as one family.
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

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  } catch {
    return "";
  }
}

function SideQuestionCard({
  sq,
  onDismiss,
  onFork,
  forking,
  dismissing,
}: {
  sq: SideQuestion;
  onDismiss: () => void;
  onFork: () => void;
  forking: boolean;
  dismissing: boolean;
}) {
  const isPending = sq.status === "pending";
  const isError = sq.status === "error";
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const answerRef = useRef<HTMLParagraphElement>(null);

  // A long answer is clamped to a few lines with a fade; only then do we offer
  // "Read full aside". Measure against the clamped box (collapsed only).
  useLayoutEffect(() => {
    const el = answerRef.current;
    if (!el || isPending || expanded) return;
    setOverflowing(el.scrollHeight - el.clientHeight > 4);
  }, [sq.answer, sq.error, isPending, expanded]);

  const answerText = sq.answer ?? sq.error ?? "";

  return (
    <article className={`sq-card${isError ? " sq-error" : ""}`}>
      <div className="sq-card-meta">
        {isPending ? (
          <span className="sq-card-asking">
            <span className="sq-card-pulse" aria-hidden="true" />
            asking…
          </span>
        ) : isError ? (
          <span className="sq-card-mark">✕ Aside</span>
        ) : (
          <span className="sq-card-mark">¶ Aside</span>
        )}
        {sq.resumed ? <span className="sq-chip-resumed">↻ resumed</span> : null}
        <span className="sq-card-ts">{fmtTime(sq.created_at)}</span>
        <button
          type="button"
          className="sq-card-dismiss"
          aria-label="Dismiss side question"
          disabled={dismissing}
          onClick={onDismiss}
        >
          ✕
        </button>
      </div>

      <p className="sq-card-q">{sq.question}</p>

      {isPending ? (
        <div className="sq-shimmer-lines" aria-label="Generating answer">
          <span className="sq-shimmer sq-shimmer-full" />
          <span className="sq-shimmer sq-shimmer-wide" />
          <span className="sq-shimmer sq-shimmer-mid" />
        </div>
      ) : (
        <p
          ref={answerRef}
          className={`sq-card-a${
            expanded
              ? " sq-card-a-expanded"
              : ` sq-card-a-clamped${overflowing ? " sq-card-a-fade" : ""}`
          }`}
        >
          {answerText}
        </p>
      )}

      {!isPending && !isError && (overflowing || expanded) ? (
        <button
          type="button"
          className="sq-card-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : "Read full aside"}
        </button>
      ) : null}

      {!isPending && !isError ? (
        <div className="sq-card-foot">
          <button
            type="button"
            className="sq-card-fork"
            aria-label="Open side question as a new session"
            disabled={forking}
            onClick={onFork}
          >
            {forking ? "opening…" : "Open as session →"}
          </button>
        </div>
      ) : null}
    </article>
  );
}

export function SideQuestionDock({
  questions,
  host,
  token,
  sessionId,
  expandSignal,
}: {
  questions: SideQuestion[];
  host: string;
  token: string;
  sessionId: string;
  expandSignal: number;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [forkingId, setForkingId] = useState<string | null>(null);
  const [dismissingIds, setDismissingIds] = useState<Set<string>>(new Set());
  const lastExpandSignalRef = useRef(0);

  // Auto-expand when the parent reports a *live* /btw (a non-hydrated aside) by
  // advancing expandSignal — so a just-sent question opens immediately, but
  // asides replayed on page load/refresh (which never advance the signal) stay
  // collapsed. A manual collapse sticks until the next live /btw.
  useEffect(() => {
    if (expandSignal > lastExpandSignalRef.current) {
      lastExpandSignalRef.current = expandSignal;
      setExpanded(true);
    }
  }, [expandSignal]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const handleDismiss = useCallback(
    async (sqid: string) => {
      setDismissingIds((prev) => new Set(prev).add(sqid));
      try {
        await dismissSideQuestion(host, token, sessionId, sqid);
      } catch {
        setDismissingIds((prev) => {
          const next = new Set(prev);
          next.delete(sqid);
          return next;
        });
      }
    },
    [host, token, sessionId],
  );

  const handleFork = useCallback(
    async (sqid: string) => {
      if (forkingId) return;
      setForkingId(sqid);
      try {
        const sess = await forkSideQuestion(host, token, sessionId, sqid);
        router.push(`/session/${sess.id}`);
      } catch {
        setForkingId(null);
      }
    },
    [forkingId, host, token, sessionId, router],
  );

  const handleClearAll = useCallback(() => {
    for (const sq of questions) {
      void handleDismiss(sq.id);
    }
  }, [questions, handleDismiss]);

  if (questions.length === 0) return null;

  const sorted = [...questions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
  const pendingCount = sorted.filter((s) => s.status === "pending").length;
  const count = questions.length;
  const label = pendingCount > 0 ? "Asking…" : sorted[0].question;

  return (
    <div className={`sq-dock${pendingCount > 0 ? " active" : ""}${expanded ? " expanded" : ""}`}>
      {expanded ? (
        <>
          <button
            type="button"
            className="sq-dock-scrim"
            aria-label="Close side questions"
            onClick={() => setExpanded(false)}
          />
          <div className="sq-dock-panel" role="dialog" aria-label="Side questions">
            <div className="sq-dock-panel-head">
              <span className="sq-dock-panel-title">Side questions</span>
              <span className="sq-dock-count">{count}</span>
              {count >= 2 ? (
                <button type="button" className="sq-clear-all" onClick={handleClearAll}>
                  Clear all
                </button>
              ) : null}
              <button
                type="button"
                className="sq-dock-collapse"
                aria-label="Collapse side questions"
                onClick={() => setExpanded(false)}
              >
                <ChevronIcon />
              </button>
            </div>
            <div className="sq-dock-cards">
              {sorted.map((sq) => (
                <SideQuestionCard
                  key={sq.id}
                  sq={sq}
                  onDismiss={() => handleDismiss(sq.id)}
                  onFork={() => handleFork(sq.id)}
                  forking={forkingId === sq.id}
                  dismissing={dismissingIds.has(sq.id)}
                />
              ))}
            </div>
          </div>
        </>
      ) : null}

      <div className="sq-dock-strip">
        <div className="sq-dock-row">
          <button
            type="button"
            className="sq-dock-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            <span className="sq-dock-glyph" aria-hidden>
              ¶
            </span>
            <span className="sq-dock-label">{label}</span>
            <span className="sq-dock-count">{count}</span>
            <span className="sq-dock-chevron" aria-hidden>
              <ChevronIcon />
            </span>
          </button>
          <button
            type="button"
            className="sq-dock-dismiss"
            aria-label="Clear side questions"
            onClick={handleClearAll}
          >
            ×
          </button>
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {label}
      </span>
    </div>
  );
}
