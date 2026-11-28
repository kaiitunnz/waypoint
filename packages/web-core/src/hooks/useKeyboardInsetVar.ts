"use client";

import { useEffect } from "react";

import { publishKeyboard } from "../keyboardInset";
import { POINTER_QUERY } from "../layoutRules";
import { revealFocusedField } from "../revealField";
import { watchTouchViewport, type BoxHost } from "../touchViewport";
import { useMediaQuery } from "./use-media-query";

const boxes: BoxHost = {
  createBox(css) {
    const box = document.createElement("div");
    box.setAttribute("aria-hidden", "true");
    box.style.cssText = css;
    document.body.append(box);
    return { height: () => box.getBoundingClientRect().height, remove: () => box.remove() };
  },
};

// Publishes the on-screen keyboard on <html>, keeps the focused field in view and restores an iOS
// home-screen app's frame. Mount once, near the root; a pointer client has no on-screen keyboard.
export function useKeyboardInsetVar() {
  const pointer = useMediaQuery(POINTER_QUERY);
  useEffect(() => {
    if (pointer) return;
    const root = document.documentElement;
    return watchTouchViewport({
      win: window,
      doc: document,
      host: boxes,
      // `navigator.standalone` is iOS's own; `display-mode: standalone` also matches Android installs.
      homeScreenApp: (navigator as Navigator & { standalone?: boolean }).standalone === true,
      publish: (state) => publishKeyboard(root, state),
      reveal: () => revealFocusedField(document, window),
    });
  }, [pointer]);
}
