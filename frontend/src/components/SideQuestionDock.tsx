"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { dismissSideQuestion, forkSideQuestion } from "@/lib/api";
import { type SideQuestion } from "@/lib/types";

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

  return (
    <article className={`sq-card${isError ? " sq-error" : ""}`}>
      <div className="sq-card-meta">
        {isPending ? (
          <span className="sq-card-asking">
            <span className="sq-card-pulse" aria-hidden="true" />
            asking…
          </span>
        ) : isError ? (
          <span>✕ Aside</span>
        ) : (
          <span>¶ Aside</span>
        )}
        {sq.resumed ? (
          <span className="sq-chip-resumed">↻ resumed</span>
        ) : null}
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
        <p className="sq-card-a">{sq.answer ?? sq.error}</p>
      )}

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
}: {
  questions: SideQuestion[];
  host: string;
  token: string;
  sessionId: string;
}) {
  const router = useRouter();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [forkingId, setForkingId] = useState<string | null>(null);
  const [dismissingIds, setDismissingIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSheetOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheetOpen]);

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

  const handleClearAll = useCallback(async () => {
    for (const sq of questions) {
      void handleDismiss(sq.id);
    }
  }, [questions, handleDismiss]);

  if (questions.length === 0) return null;

  const sorted = [...questions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  const cards = sorted.map((sq) => (
    <SideQuestionCard
      key={sq.id}
      sq={sq}
      onDismiss={() => handleDismiss(sq.id)}
      onFork={() => handleFork(sq.id)}
      forking={forkingId === sq.id}
      dismissing={dismissingIds.has(sq.id)}
    />
  ));

  return (
    <div className={`sq-dock${sheetOpen ? " sq-dock-expanded" : ""}`}>
      {/* Mobile bottom sheet (expanded) */}
      {sheetOpen ? (
        <>
          <button
            type="button"
            className="sq-scrim"
            aria-label="Close side questions"
            onClick={() => setSheetOpen(false)}
          />
          <div className="sq-sheet" role="dialog" aria-label="Side questions">
            <div className="sq-sheet-head">
              <span className="sq-sheet-title">Side questions</span>
              {questions.length >= 2 ? (
                <button
                  type="button"
                  className="sq-clear-all"
                  onClick={() => {
                    void handleClearAll();
                    setSheetOpen(false);
                  }}
                >
                  Clear all
                </button>
              ) : null}
              <button
                type="button"
                className="sq-sheet-close"
                aria-label="Close"
                onClick={() => setSheetOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="sq-sheet-cards">{cards}</div>
          </div>
        </>
      ) : null}

      {/* Mobile slim bar */}
      <button
        type="button"
        className="sq-bar"
        onClick={() => setSheetOpen(true)}
        aria-label={`${questions.length} side question${questions.length !== 1 ? "s" : ""} — tap to view`}
        aria-expanded={sheetOpen}
      >
        <span className="sq-bar-icon" aria-hidden="true">
          ¶
        </span>
        <span className="sq-bar-label">
          {questions.length} side question{questions.length !== 1 ? "s" : ""}
        </span>
        <span className="sq-bar-chevron" aria-hidden="true">
          ↑
        </span>
      </button>

      {/* Desktop floating card panel */}
      <div className="sq-panel" aria-label="Side questions">
        <div className="sq-panel-head">
          <span className="sq-panel-label">Side questions</span>
          {questions.length >= 2 ? (
            <button type="button" className="sq-clear-all" onClick={() => void handleClearAll()}>
              Clear all
            </button>
          ) : null}
        </div>
        <div className="sq-panel-cards">{cards}</div>
      </div>
    </div>
  );
}
