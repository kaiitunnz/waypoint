"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { TranscriptTurn } from "@waypoint/web-core/session/turns";

// Within this many pixels of the end, the view follows new content.
const FOLLOW_PX = 160;

// How many turns in view are remembered, so one that is replaced or hidden leaves another to hold by.
const ANCHORS = 6;

// The transcript's scrolling: it follows the end while you are there, keeps its place
// when content lands above, and says when you are away from the end.
export function useTranscriptScroll(turns: TranscriptTurn[]) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  // The turns in view and how far below the top edge each sat, as last laid out. The scroller's
  // height and offset back them up for when none survives, as a merged first turn does not.
  const placed = useRef<{ anchors: { turn: Element; offset: number }[]; height: number; top: number } | null>(null);
  const firstKey = useRef<string | null>(null);
  const [away, setAway] = useState(false);

  const remember = useCallback(() => {
    const view = scroller.current;
    const rows = log.current?.children;
    if (!view) return;
    const edge = view.getBoundingClientRect().top;
    let low = 0;
    let high = rows?.length ?? 0;
    while (rows && low < high) {
      const mid = (low + high) >> 1;
      if (rows[mid].getBoundingClientRect().bottom > edge) high = mid;
      else low = mid + 1;
    }
    const anchors: { turn: Element; offset: number }[] = [];
    for (let index = low; rows && index < rows.length && anchors.length < ANCHORS; index += 1) {
      const offset = rows[index].getBoundingClientRect().top - edge;
      if (offset > view.clientHeight) break;
      anchors.push({ turn: rows[index], offset });
    }
    placed.current = { anchors, height: view.scrollHeight, top: view.scrollTop };
  }, []);

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.clientHeight - el.scrollTop < FOLLOW_PX;
    following.current = near;
    setAway(!near);
    remember();
  }, [remember]);

  // New content: follow the end, else keep the turn in view where it was.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const head = turns[0]?.key ?? null;
    const before = placed.current;
    const anchor = before?.anchors.find((candidate) => candidate.turn.isConnected);
    if (following.current) {
      el.scrollTop = el.scrollHeight;
    } else if (anchor) {
      const shift = anchor.turn.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor.offset;
      if (shift !== 0) el.scrollTop += shift;
    } else if (before && firstKey.current !== null && head !== firstKey.current) {
      el.scrollTop = before.top + (el.scrollHeight - before.height);
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

  return { scroller, content, log, away, onScroll, jumpToEnd };
}
