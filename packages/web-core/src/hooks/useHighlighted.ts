"use client";

import { useEffect, useMemo, useState } from "react";

import {
  buildPlainDiffLines,
  highlightDiffToLines,
  highlightFenceToLines,
  type DiffLine,
  type HighlightToken,
} from "../highlight";
import { HIGHLIGHT_DEBOUNCE_MS } from "../markdownCode";

// A fence's highlighted lines, or null until they are ready for this exact text: a block that
// is still growing shows plain text, not a shorter highlighted prefix.
export function useHighlightedFence(code: string, lang: string): HighlightToken[][] | null {
  const key = `${lang}\n${code}`;
  const [done, setDone] = useState<{ key: string; lines: HighlightToken[][] } | null>(null);

  useEffect(() => {
    if (!lang) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void highlightFenceToLines(code, lang).then((highlighted) => {
        if (!cancelled && highlighted) setDone({ key, lines: highlighted });
      });
    }, HIGHLIGHT_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [code, lang, key]);

  return done?.key === key ? done.lines : null;
}

export function useHighlightedDiff(diff: string, path: string): DiffLine[] {
  const plain = useMemo(() => buildPlainDiffLines(diff), [diff]);
  const [done, setDone] = useState<{ diff: string; lines: DiffLine[] } | null>(null);

  useEffect(() => {
    if (!diff) return;
    let cancelled = false;
    void highlightDiffToLines(diff, path).then((lines) => {
      if (!cancelled && lines) setDone({ diff, lines });
    });
    return () => {
      cancelled = true;
    };
  }, [diff, path]);

  return done?.diff === diff ? done.lines : plain;
}
