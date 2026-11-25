"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { TranscriptTurn } from "@waypoint/web-core/session/turns";

// Within this many pixels of the end, the view follows new content.
const FOLLOW_PX = 160;

// The transcript's scrolling: it follows the end while you are there, keeps its place
// when older messages load above, and says when you are away from the end.
export function useTranscriptScroll(turns: TranscriptTurn[], olderPending: boolean, onLoadOlder: () => void) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const firstKey = useRef<string | null>(null);
  const [away, setAway] = useState(false);

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.clientHeight - el.scrollTop < FOLLOW_PX;
    following.current = near;
    setAway(!near);
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

  // A load that ends without a new first turn (it failed, or its messages are hidden) leaves no place to keep.
  useLayoutEffect(() => {
    if (!olderPending) anchor.current = null;
  }, [olderPending]);

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
    if (anchor.current) return;
    const el = scroller.current;
    if (el) anchor.current = { height: el.scrollHeight, top: el.scrollTop };
    onLoadOlder();
  }, [onLoadOlder]);

  const jumpToEnd = useCallback(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, []);

  return { scroller, content, away, onScroll, loadOlder, jumpToEnd };
}
