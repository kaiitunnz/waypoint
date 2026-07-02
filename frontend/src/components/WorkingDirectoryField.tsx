"use client";

import { useEffect, useId, useRef } from "react";

interface WorkingDirectoryFieldProps {
  cwd: string;
  onChange: (cwd: string) => void;
  targetLabel: string | null;
  recentCwds: string[];
  // The path the backend rejected as nonexistent, or null. When set, the field
  // shows an inline error and takes focus; editing clears it via onClearError.
  error?: string | null;
  onClearError?: () => void;
}

export function WorkingDirectoryField({
  cwd,
  onChange,
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
  const hasRecents = recentCwds.length > 0;

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
        list={hasRecents ? listId : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <span className="field-error" id={errorId} role="alert">
          Directory not found: <code>{error}</code>
        </span>
      ) : null}
      {hasRecents ? (
        <datalist id={listId}>
          {recentCwds.map((recent) => (
            <option key={recent} value={recent} />
          ))}
        </datalist>
      ) : null}
    </label>
  );
}
