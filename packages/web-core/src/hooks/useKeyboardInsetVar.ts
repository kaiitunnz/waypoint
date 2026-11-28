"use client";

import { useEffect } from "react";

import { KEYBOARD_CLOSED, publishKeyboard, watchKeyboard } from "../keyboardInset";
import { POINTER_QUERY } from "../layoutRules";
import { revealFocusedField } from "../revealField";
import { useMediaQuery } from "./use-media-query";

// Publishes the on-screen keyboard on <html> and keeps the focused field in view. Mount once, near
// the root; a pointer client has no on-screen keyboard.
export function useKeyboardInsetVar() {
  const pointer = useMediaQuery(POINTER_QUERY);
  useEffect(() => {
    if (pointer) return;
    const root = document.documentElement;
    // The frame the full-screen layouts fill; `innerHeight` differs from it while iOS has the
    // keyboard up.
    const frame = document.createElement("div");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;inset:0;visibility:hidden;pointer-events:none";
    document.body.append(frame);
    let open = false;
    const stop = watchKeyboard(window, document, () => frame.getBoundingClientRect().height, (state) => {
      open = state.visibleHeight !== null;
      publishKeyboard(root, state);
      if (open) requestAnimationFrame(() => revealFocusedField(document, window));
    });
    // Moving between fields while the keyboard stays up.
    const onFocusIn = () => {
      if (open) revealFocusedField(document, window);
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      stop();
      frame.remove();
      document.removeEventListener("focusin", onFocusIn);
      publishKeyboard(root, KEYBOARD_CLOSED);
    };
  }, [pointer]);
}
