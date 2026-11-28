"use client";

import { useCallback, useLayoutEffect, useRef, type FocusEvent, type RefObject } from "react";

import { caretToEnd, followsEnd, showEnd } from "../tailAnchor";

interface TailHandlers {
  onPointerDown: () => void;
  onFocus: (event: FocusEvent<HTMLInputElement>) => void;
  onBlur: (event: FocusEvent<HTMLInputElement>) => void;
}

// The caller chains the returned handlers into its own. A press leaves the caret where the person
// put it; a focus no press caused moves it to the end.
export function useTailAnchor(input: RefObject<HTMLInputElement | null>, value: string): TailHandlers {
  const pressed = useRef(false);

  useLayoutEffect(() => {
    const field = input.current;
    if (field && followsEnd(field, document.activeElement === field)) showEnd(field);
  }, [input, value]);

  const onPointerDown = useCallback(() => {
    pressed.current = true;
  }, []);

  const onFocus = useCallback((event: FocusEvent<HTMLInputElement>) => {
    if (pressed.current) return;
    const field = event.currentTarget;
    caretToEnd(field);
    // Some browsers reset the scroll as the focus settles.
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
