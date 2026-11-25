"use client";

import { useEffect, useLayoutEffect, type RefObject } from "react";

function fit(field: HTMLTextAreaElement | null) {
  if (!field) return;
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight + field.offsetHeight - field.clientHeight}px`;
}

// Grows the field with its text, and publishes the composer's height as
// --composer-height, which toasts clear.
export function useComposerSizing(
  root: RefObject<HTMLDivElement | null>,
  field: RefObject<HTMLTextAreaElement | null>,
  draft: string,
  pointer: boolean,
): void {
  useLayoutEffect(() => fit(field.current), [field, draft, pointer]);

  useEffect(() => {
    const node = root.current;
    const input = field.current;
    if (!node || !input) return;
    const page = document.documentElement;
    let width = input.offsetWidth;
    const observer = new ResizeObserver(() => {
      // A change of width rewraps the text: the window's, or Stop taking room.
      if (input.offsetWidth !== width) {
        width = input.offsetWidth;
        fit(input);
      }
      page.style.setProperty("--composer-height", `${node.offsetHeight}px`);
    });
    observer.observe(node);
    observer.observe(input);
    return () => {
      observer.disconnect();
      page.style.removeProperty("--composer-height");
    };
  }, [root, field]);
}
