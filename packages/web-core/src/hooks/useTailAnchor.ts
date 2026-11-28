"use client";

import { useImperativeHandle, useLayoutEffect, useRef, useState, type FocusEventHandler, type PointerEventHandler, type Ref } from "react";

import { createTailAnchor } from "../tailAnchor";

interface TailProps {
  value?: unknown;
  onPointerDown?: PointerEventHandler<HTMLInputElement>;
  onFocus?: FocusEventHandler<HTMLInputElement>;
  onBlur?: FocusEventHandler<HTMLInputElement>;
}

// The props for an input that shows the end of its value while it is not being edited. The caller's
// handlers run after the anchor's, and `forwarded` receives the input.
export function useTailAnchor(forwarded: Ref<HTMLInputElement> | undefined, { value, onPointerDown, onFocus, onBlur }: TailProps) {
  const input = useRef<HTMLInputElement>(null);
  useImperativeHandle(forwarded, () => input.current as HTMLInputElement, []);
  const [anchor] = useState(() =>
    createTailAnchor<HTMLInputElement>({ isFocused: (field) => document.activeElement === field, nextFrame: (callback) => void requestAnimationFrame(callback) }),
  );
  useLayoutEffect(() => anchor.sync(input.current), [anchor, value]);

  return {
    ref: input,
    onPointerDown: (event) => {
      anchor.press();
      onPointerDown?.(event);
    },
    onFocus: (event) => {
      anchor.focus(event.currentTarget);
      onFocus?.(event);
    },
    onBlur: (event) => {
      anchor.blur(event.currentTarget);
      onBlur?.(event);
    },
  } satisfies TailProps & { ref: Ref<HTMLInputElement> };
}
