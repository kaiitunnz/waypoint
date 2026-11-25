"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { TranscriptTurn } from "@waypoint/web-core/session/turns";

// Within this many pixels of the end, the view follows new content.
const FOLLOW_PX = 160;

// The transcript's scrolling: it follows the end while you are there, keeps its place
// when older messages land above, and says when you are away from the end.
export function useTranscriptScroll(turns: TranscriptTurn[]) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  // The scroller's height and offset as last laid out: what a prepend is measured against.
  const placed = useRef({ height: 0, top: 0 });
  const firstKey = useRef<string | null>(null);
  const [away, setAway] = useState(false);

  const remember = useCallback(() => {
    const el = scroller.current;
    if (el) placed.current = { height: el.scrollHeight, top: el.scrollTop };
  }, []);

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.clientHeight - el.scrollTop < FOLLOW_PX;
    following.current = near;
    setAway(!near);
    remember();
  }, [remember]);

  // New turns: a new first turn is older history, so hold the place; else follow the end.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const head = turns[0]?.key ?? null;
    if (firstKey.current !== null && head !== firstKey.current) {
      el.scrollTop = placed.current.top + (el.scrollHeight - placed.current.height);
    } else if (following.current) {
      el.scrollTop = el.scrollHeight;
    }
    firstKey.current = head;
    remember();
  }, [turns, remember]);

  // Content that grows after layout (markdown, math, images, a run opening) keeps the end in view.
  useEffect(() => {
    const el = scroller.current;
    const node = content.current;
    if (!el || !node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (following.current) el.scrollTop = el.scrollHeight;
      remember();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [remember]);

  const jumpToEnd = useCallback(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, []);

  return { scroller, content, away, onScroll, jumpToEnd };
}
