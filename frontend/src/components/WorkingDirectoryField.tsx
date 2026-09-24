"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { fetchDirectorySuggestions } from "@/lib/api";

const SUGGEST_DELAY_MS = 200;

interface WorkingDirectoryFieldProps {
  host: string;
  token: string;
  cwd: string;
  onChange: (cwd: string) => void;
  // The SSH launch target the session will run on, or null for this host;
  // directory suggestions come from that machine.
  launchTargetId: string | null;
  targetLabel: string | null;
  recentCwds: string[];
  // The path the backend rejected as nonexistent, or null. When set, the field
  // shows an inline error and takes focus; editing clears it via onClearError.
  error?: string | null;
  onClearError?: () => void;
}

export function WorkingDirectoryField({
  host,
  token,
  cwd,
  onChange,
  launchTargetId,
  targetLabel,
  recentCwds,
  error,
  onClearError,
}: WorkingDirectoryFieldProps) {
  const listId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const label = targetLabel
    ? `Working directory on ${targetLabel}`
    : "Working directory";
  // Tagged with the target they came from so a switch never shows another
  // machine's directories while the new target's fetch is in flight.
  const [suggested, setSuggested] = useState<{
    targetId: string | null;
    dirs: string[];
  }>({ targetId: null, dirs: [] });
  const completable = cwd.startsWith("/") || cwd.startsWith("~");

  useEffect(() => {
    if (!completable || !host || !token) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchDirectorySuggestions(
        host,
        token,
        cwd,
        launchTargetId,
        controller.signal,
      )
        .then((dirs) => {
          if (!controller.signal.aborted) {
            setSuggested({ targetId: launchTargetId, dirs });
          }
        })
        .catch(() => {
          // Suggestions are best-effort; the field still accepts any path.
        });
    }, SUGGEST_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [host, token, cwd, launchTargetId, completable]);

  // Recents first, then the target's child directories for the typed path.
  // Suggestions from an older keystroke stay only while they still extend the
  // input, so the list doesn't flash empty while the next fetch is in flight.
  const options = useMemo(() => {
    const dirs =
      completable && suggested.targetId === launchTargetId
        ? suggested.dirs.filter((dir) => dir.startsWith(cwd))
        : [];
    return [...new Set([...recentCwds, ...dirs])];
  }, [completable, cwd, launchTargetId, recentCwds, suggested]);
  const hasOptions = options.length > 0;

  // Pull focus to the offending field when a launch fails on the cwd, so the
  // fix is immediate even if the launch card scrolled out of view.
  useEffect(() => {
    if (!error) {
      return;
    }
    const input = inputRef.current;
    input?.focus();
    input?.scrollIntoView({ block: "nearest" });
  }, [error]);

  return (
    <label className="field">
      <span>{label}</span>
      <input
        ref={inputRef}
        value={cwd}
        onChange={(event) => {
          onChange(event.target.value);
          if (error) {
            onClearError?.();
          }
        }}
        placeholder={targetLabel ? "~" : undefined}
        list={hasOptions ? listId : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <span className="field-error" id={errorId} role="alert">
          Directory not found: <code>{error}</code>
        </span>
      ) : null}
      {hasOptions ? (
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      ) : null}
    </label>
  );
}
