"use client";

import { useEffect, useState } from "react";

import { previewSchedule } from "../api";
import { cronFromState, type RecurrenceState, type TimingMode } from "../recurrence";

const PREVIEW_DEBOUNCE_MS = 350;
const PREVIEW_COUNT = 3;

export interface RecurrencePreview {
  // The next run instants, as the server computes them.
  occurrences: string[];
  error: string | null;
  previewing: boolean;
}

// The debounced server preview of a recurrence. `onValidChange` (stable)
// reports whether it previews cleanly, always true for a one-time send.
export function useRecurrencePreview(
  host: string,
  token: string,
  timing: TimingMode,
  state: RecurrenceState,
  onValidChange: (valid: boolean) => void,
): RecurrencePreview {
  const [occurrences, setOccurrences] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const cron = timing === "repeat" ? cronFromState(state) : null;

  useEffect(() => {
    if (timing !== "repeat") {
      onValidChange(true);
      setError(null);
      setOccurrences([]);
      return;
    }
    if (!cron) {
      onValidChange(false);
      setError(null);
      setOccurrences([]);
      return;
    }
    let cancelled = false;
    setPreviewing(true);
    const handle = window.setTimeout(async () => {
      try {
        const next = await previewSchedule(host, token, cron, state.timezone, state.startAt || null, PREVIEW_COUNT);
        if (cancelled) return;
        setOccurrences(next);
        setError(null);
        onValidChange(true);
      } catch (err) {
        if (cancelled) return;
        setOccurrences([]);
        setError(err instanceof Error ? err.message : "invalid recurrence");
        onValidChange(false);
      } finally {
        if (!cancelled) setPreviewing(false);
      }
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [timing, cron, state.timezone, state.startAt, host, token, onValidChange]);

  return { occurrences, error, previewing };
}
