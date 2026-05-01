"use client";

import { useId } from "react";

interface WorkingDirectoryFieldProps {
  cwd: string;
  onChange: (cwd: string) => void;
  targetLabel: string | null;
  recentCwds: string[];
}

export function WorkingDirectoryField({
  cwd,
  onChange,
  targetLabel,
  recentCwds,
}: WorkingDirectoryFieldProps) {
  const listId = useId();
  const label = targetLabel
    ? `Working directory on ${targetLabel}`
    : "Working directory";
  const hasRecents = recentCwds.length > 0;

  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={cwd}
        onChange={(event) => onChange(event.target.value)}
        placeholder={targetLabel ? "~" : undefined}
        list={hasRecents ? listId : undefined}
        autoComplete="off"
      />
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
