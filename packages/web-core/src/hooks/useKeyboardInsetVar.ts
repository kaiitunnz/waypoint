"use client";

import { useEffect } from "react";

import { revealFocusedField, watchKeyboard } from "../keyboardInset";
import { POINTER_QUERY } from "../layoutRules";
import { useMediaQuery } from "./use-media-query";

// While the on-screen keyboard is up on touch, publishes on <html> how far it covers the bottom of the
// layout viewport (`--keyboard-inset`), how much of it is in view (`--visible-height`) and
// `data-keyboard`, and keeps the focused field in view. A pointer client has no on-screen keyboard
// and keeps the stylesheet's defaults. Mount once, near the root.
export function useKeyboardInsetVar() {
  const pointer = useMediaQuery(POINTER_QUERY);
  useEffect(() => {
    if (pointer) return;
    const root = document.documentElement;
    let open = false;
    const stop = watchKeyboard(window, document, (state) => {
      open = state.visibleHeight !== null;
      if (state.visibleHeight === null) {
        root.style.removeProperty("--keyboard-inset");
        root.style.removeProperty("--visible-height");
        delete root.dataset.keyboard;
        return;
      }
      root.style.setProperty("--keyboard-inset", `${state.inset}px`);
      root.style.setProperty("--visible-height", `${state.visibleHeight}px`);
      root.dataset.keyboard = "";
      requestAnimationFrame(() => revealFocusedField(document, window));
    });
    // Moving between fields while the keyboard stays up.
    const onFocusIn = () => {
      if (open) revealFocusedField(document, window);
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      stop();
      document.removeEventListener("focusin", onFocusIn);
      root.style.removeProperty("--keyboard-inset");
      root.style.removeProperty("--visible-height");
      delete root.dataset.keyboard;
    };
  }, [pointer]);
}
