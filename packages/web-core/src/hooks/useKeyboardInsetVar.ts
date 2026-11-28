"use client";

import { useEffect } from "react";

import { createFrameRestore } from "../frameRestore";
import { frameIsShort, KEYBOARD_CLOSED, publishKeyboard, watchKeyboard } from "../keyboardInset";
import { POINTER_QUERY } from "../layoutRules";
import { revealFocusedField } from "../revealField";
import { useMediaQuery } from "./use-media-query";

function hiddenBox(css: string): HTMLDivElement {
  const box = document.createElement("div");
  box.setAttribute("aria-hidden", "true");
  box.style.cssText = css;
  document.body.append(box);
  return box;
}

// Publishes the on-screen keyboard on <html>, keeps the focused field in view and, in an iOS
// home-screen app, restores the frame the keyboard leaves short. Mount once, near the root; a
// pointer client has no on-screen keyboard.
export function useKeyboardInsetVar() {
  const pointer = useMediaQuery(POINTER_QUERY);
  useEffect(() => {
    if (pointer) return;
    const root = document.documentElement;
    // The frame the full-screen layouts fill; `innerHeight` differs from it while iOS has the
    // keyboard up.
    const frame = hiddenBox("position:fixed;inset:0;visibility:hidden;pointer-events:none");
    const screen = hiddenBox("position:fixed;top:0;height:100lvh;visibility:hidden;pointer-events:none");
    const standalone = window.matchMedia("(display-mode: standalone)").matches;
    let open = false;
    const restore = createFrameRestore({
      isShort: () => standalone && !open && frameIsShort(screen.getBoundingClientRect().height, frame.getBoundingClientRect().height),
      lift: () => {
        // 3000px is taller than any screen.
        const tall = hiddenBox("position:absolute;top:0;left:0;width:1px;height:3000px;opacity:0;pointer-events:none");
        return () => tall.remove();
      },
      setTimeout: (callback, ms) => window.setTimeout(callback, ms),
      clearTimeout: (handle) => window.clearTimeout(handle),
    });
    const stop = watchKeyboard(window, document, () => frame.getBoundingClientRect().height, (state) => {
      open = state.visibleHeight !== null;
      publishKeyboard(root, state);
      restore.check();
      if (open) requestAnimationFrame(() => revealFocusedField(document, window));
    });
    window.addEventListener("resize", restore.check);
    window.visualViewport?.addEventListener("resize", restore.check);
    // Moving between fields while the keyboard stays up.
    const onFocusIn = () => {
      if (open) revealFocusedField(document, window);
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      stop();
      restore.dispose();
      window.removeEventListener("resize", restore.check);
      window.visualViewport?.removeEventListener("resize", restore.check);
      frame.remove();
      screen.remove();
      document.removeEventListener("focusin", onFocusIn);
      publishKeyboard(root, KEYBOARD_CLOSED);
    };
  }, [pointer]);
}
