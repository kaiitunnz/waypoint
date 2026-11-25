"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import type { TranscriptTurn } from "@waypoint/web-core/session/turns";
import type { SessionStatus } from "@waypoint/web-core/types";

import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./Transcript.module.css";
import { TurnView } from "./TurnView";

// Within this many pixels of the end, the view follows new content.
const FOLLOW_PX = 160;

interface TranscriptProps {
  turns: TranscriptTurn[];
  full: boolean;
  structured: boolean;
  status: SessionStatus | null;
  loading: boolean;
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  hiddenCount: number;
  onShowAll: () => void;
  // The transcript's own content when it is not a conversation, such as a placeholder.
  override?: ReactNode;
}

// The scrolling transcript. It follows the end while you are there, keeps its
// place when older messages load above, and offers a way back to the end.
export function Transcript({
  turns,
  full,
  structured,
  status,
  loading,
  hasOlder,
  loadingOlder,
  onLoadOlder,
  hiddenCount,
  onShowAll,
  override,
}: TranscriptProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const firstKey = useRef<string | null>(null);
  const [away, setAway] = useState(false);

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.clientHeight - el.scrollTop < FOLLOW_PX;
    following.current = near;
    setAway(!near);
  }, []);

  const toEnd = useCallback((behavior: ScrollBehavior) => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior });
  }, []);

  // New turns: hold the place when older ones arrived above, else follow the end.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const head = turns[0]?.key ?? null;
    if (anchor.current && head !== firstKey.current) {
      el.scrollTop = anchor.current.top + (el.scrollHeight - anchor.current.height);
      anchor.current = null;
    } else if (following.current) {
      el.scrollTop = el.scrollHeight;
    }
    firstKey.current = head;
  }, [turns]);

  // Content that grows after layout (markdown, math, images, a run opening) keeps the end in view.
  useEffect(() => {
    const el = scroller.current;
    const node = content.current;
    if (!el || !node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (following.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const loadOlder = useCallback(() => {
    const el = scroller.current;
    if (el) anchor.current = { height: el.scrollHeight, top: el.scrollTop };
    onLoadOlder();
  }, [onLoadOlder]);

  const empty = !loading && turns.length === 0;
  const working = status === "running" || status === "starting";

  return (
    <div className={styles.frame}>
      <div ref={scroller} className={styles.scroller} onScroll={measure}>
        <div ref={content} className={cx(styles.col, Boolean(override) && styles.top)}>
          {override ?? (
            <>
              {hasOlder ? (
                <button type="button" className={styles.older} onClick={loadOlder} disabled={loadingOlder}>
                  {loadingOlder ? "Loading older messages…" : "Load older messages"}
                </button>
              ) : null}
              <div className={styles.turns} role="log" aria-label="Transcript">
                {turns.map((turn, index) => (
                  <TurnView key={turn.key} turn={turn} full={full} working={working && index === turns.length - 1} structured={structured} />
                ))}
              </div>
              {empty ? (
                status === "exited" ? (
                  <EmptyState title="Session exited" detail="No transcript was captured." />
                ) : hiddenCount > 0 ? (
                  <div className={styles.empty}>
                    <EmptyState title="Nothing important yet" detail={`${hiddenCount} low-signal event${hiddenCount === 1 ? "" : "s"} hidden`} />
                    <button type="button" className={styles.showAll} onClick={onShowAll}>
                      Show all events
                    </button>
                  </div>
                ) : (
                  <EmptyState title="Waiting for the agent…" />
                )
              ) : null}
            </>
          )}
        </div>
      </div>
      {away && !override ? (
        <button type="button" className={styles.jump} onClick={() => toEnd("smooth")}>
          <Icon name="arrow-down" size={16} />
          Jump to latest
        </button>
      ) : null}
    </div>
  );
}
