"use client";

import { useEffect } from "react";

import { publishKeyboard } from "../keyboardInset";
import { POINTER_QUERY } from "../layoutRules";
import { revealFocusedField } from "../revealField";
import { watchTouchViewport, type BoxHost } from "../touchViewport";
import { useMediaQuery } from "./use-media-query";

// `innerHeight` is not the frame the full-screen layouts fill while iOS has the keyboard up.
const FRAME_CSS = "position:fixed;inset:0;visibility:hidden;pointer-events:none";
const SCREEN_CSS = "position:fixed;top:0;height:100lvh;visibility:hidden;pointer-events:none";
// 3000px is taller than any screen.
const LIFT_CSS = "position:absolute;top:0;left:0;width:1px;height:3000px;opacity:0;pointer-events:none";

function addBox(css: string): HTMLElement {
  const box = document.createElement("div");
  box.setAttribute("aria-hidden", "true");
  box.style.cssText = css;
  document.body.append(box);
  return box;
}

const boxes: BoxHost = {
  measure(kind) {
    const box = addBox(kind === "frame" ? FRAME_CSS : SCREEN_CSS);
    return { height: () => box.getBoundingClientRect().height, remove: () => box.remove() };
  },
  lift() {
    const box = addBox(LIFT_CSS);
    return () => box.remove();
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
