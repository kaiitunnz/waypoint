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
    if (!node) return;
    const page = document.documentElement;
    let width = node.offsetWidth;
    const observer = new ResizeObserver(() => {
      // A change of width rewraps the text.
      if (node.offsetWidth !== width) {
        width = node.offsetWidth;
        fit(field.current);
      }
      page.style.setProperty("--composer-height", `${node.offsetHeight}px`);
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
      page.style.removeProperty("--composer-height");
    };
  }, [root, field]);
}
