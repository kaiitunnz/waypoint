"use client";

import { useCallback, useLayoutEffect, useRef, type FocusEvent, type RefObject } from "react";

import { caretToEnd, showEnd } from "../tailAnchor";

interface TailHandlers {
  onPointerDown: () => void;
  onFocus: (event: FocusEvent<HTMLInputElement>) => void;
  onBlur: (event: FocusEvent<HTMLInputElement>) => void;
}

// Shows the end of a path or command field while it is not being edited: after it mounts, when its
// value changes from outside, and after it loses focus. A focus that no press caused moves the caret
// to the end; a press leaves the caret where the person put it. The caller chains the handlers into
// its own.
export function useTailAnchor(input: RefObject<HTMLInputElement | null>, value: string): TailHandlers {
  const pressed = useRef(false);

  useLayoutEffect(() => {
    const field = input.current;
    if (field && document.activeElement !== field) showEnd(field);
  }, [input, value]);

  const onPointerDown = useCallback(() => {
    pressed.current = true;
  }, []);

  const onFocus = useCallback((event: FocusEvent<HTMLInputElement>) => {
    if (pressed.current) return;
    const field = event.currentTarget;
    caretToEnd(field);
    // iOS scrolls a field back to its start once the focus has settled.
    requestAnimationFrame(() => showEnd(field));
  }, []);

  const onBlur = useCallback((event: FocusEvent<HTMLInputElement>) => {
    pressed.current = false;
    const field = event.currentTarget;
    requestAnimationFrame(() => {
      if (document.activeElement !== field) showEnd(field);
    });
  }, []);

  return { onPointerDown, onFocus, onBlur };
}
