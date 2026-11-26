"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { FOLLOWING, moved, scrolled, sent, settled, toggled } from "@waypoint/web-core/session/follow";
import type { TranscriptTurn } from "@waypoint/web-core/session/turns";

// How many turns in view are remembered, so one that is replaced or hidden leaves another to hold by.
const ANCHORS = 6;

const fromEnd = (el: Element) => el.scrollHeight - el.clientHeight - el.scrollTop;

// The transcript's scrolling: it follows the end while you are there, keeps its place
// when content lands above or a fold is toggled, and says when you are away from the end.
export function useTranscriptScroll(turns: TranscriptTurn[], pendingCount: number) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(FOLLOWING);
  const sends = useRef(pendingCount);
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
    const next = scrolled(follow.current, el.scrollTop, fromEnd(el));
    if (next !== follow.current) {
      follow.current = next;
      setAway(!next.following);
    }
    remember();
  }, [remember]);

  // Call before a fold changes size. Opening one leaves the view where it is; closing
  // one also keeps `toggle` where the person pressed it.
  const hold = useCallback(
    (toggle: Element, closing: boolean) => {
      const el = scroller.current;
      if (!el) return;
      const top = toggle.getBoundingClientRect().top;
      const next = toggled(follow.current, el.scrollTop);
      follow.current = next;
      // By the next frame the fold has laid out and any scroll it caused has been reported.
      requestAnimationFrame(() => {
        if (closing && toggle.isConnected) el.scrollTop += toggle.getBoundingClientRect().top - top;
        follow.current = settled(next.hold?.resumeOnSend ?? false, closing, el.scrollTop, fromEnd(el));
        setAway(!follow.current.following && fromEnd(el) > 1);
        remember();
      });
    },
    [remember],
  );

  // New content: follow the end, else keep the turn in view where it was.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const head = turns[0]?.key ?? null;
    const before = placed.current;
    const anchor = before?.anchors.find((candidate) => candidate.turn.isConnected);
    if (follow.current.following) {
      el.scrollTop = el.scrollHeight;
    } else if (anchor) {
      const shift = anchor.turn.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor.offset;
      if (shift !== 0) el.scrollTop += shift;
    } else if (before && firstKey.current !== null && head !== firstKey.current) {
      el.scrollTop = before.top + (el.scrollHeight - before.height);
    }
    follow.current = moved(follow.current, el.scrollTop);
    firstKey.current = head;
    remember();
  }, [turns, remember]);

  useLayoutEffect(() => {
    const el = scroller.current;
    const added = pendingCount > sends.current;
    sends.current = pendingCount;
    if (!el || !added) return;
    const next = sent(follow.current);
    if (next === follow.current) return;
    follow.current = next;
    el.scrollTop = el.scrollHeight;
  }, [pendingCount]);

  // Content that grows after layout (markdown, math, images), or a view that shrinks as
  // the composer grows, keeps the end in view while following.
  useEffect(() => {
    const el = scroller.current;
    const node = content.current;
    if (!el || !node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (follow.current.following) el.scrollTop = el.scrollHeight;
      // Held short of the end, content arriving below raises "Jump to latest".
      else if (follow.current.hold) setAway(fromEnd(el) > 1);
      remember();
    });
    observer.observe(node);
    observer.observe(el);
    return () => observer.disconnect();
  }, [remember]);

  const jumpToEnd = useCallback(() => {
    follow.current = { ...follow.current, hold: null };
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, []);

  return { scroller, content, log, away, onScroll, hold, jumpToEnd };
}
