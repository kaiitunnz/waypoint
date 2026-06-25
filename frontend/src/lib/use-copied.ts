import { useCallback, useEffect, useRef, useState } from "react";

// Shared reset window for every copy button's "copied" confirmation, so the
// glyph/label flips back after a consistent delay app-wide.
export const COPIED_RESET_MS = 800;

// Drives the transient "copied" confirmation state shared by every copy button.
// The copy mechanism stays at the call site (async clipboard vs the sync
// execCommand path some surfaces need) — this only owns the flag and its timer,
// clearing it on re-copy and unmount so a stale timeout never sets state on a
// gone component.
export function useCopied(resetMs: number = COPIED_RESET_MS) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  const clear = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);
  useEffect(() => clear, [clear]);
  const markCopied = useCallback(() => {
    clear();
    setCopied(true);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setCopied(false);
    }, resetMs);
  }, [clear, resetMs]);
  const reset = useCallback(() => {
    clear();
    setCopied(false);
  }, [clear]);
  return { copied, markCopied, reset };
}
