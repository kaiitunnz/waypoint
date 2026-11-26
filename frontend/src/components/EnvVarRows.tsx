"use client";

// Shared key/value environment-variable editor: one row per variable with a
// name field, a value field, and a quiet remove control, plus a ghost "Add
// variable" action. Used by the launch panel, the resume panel, and the session
// settings modal so every env editor reads the same. Controlled — the parent
// owns the entries and their conversion to/from a plain record.
//
// It owns its own `.env-editor` styling at a specificity that wins over the
// launch panel's `.field input` rule, so its inputs are sized identically in
// every host regardless of the surrounding form.

import { addEnvEntry, deleteEnvEntry, type EnvEntry, updateEnvEntry } from "@waypoint/web-core/launch/env";

export type { EnvEntry } from "@waypoint/web-core/launch/env";

interface EnvVarRowsProps {
  entries: EnvEntry[];
  onChange: (entries: EnvEntry[]) => void;
  disabled?: boolean;
  // Launch/resume enter fresh values in the clear (matching the old textarea);
  // the settings modal masks newly-typed secrets.
  valueType?: "text" | "password";
}

export function EnvVarRows({
  entries,
  onChange,
  disabled,
  valueType = "text",
}: EnvVarRowsProps) {
  const add = () => onChange(addEnvEntry(entries));
  const update = (id: number, patch: Partial<Pick<EnvEntry, "key" | "value">>) =>
    onChange(updateEnvEntry(entries, id, patch));
  const remove = (id: number) => onChange(deleteEnvEntry(entries, id));

  return (
    <div className="env-editor">
      {entries.length > 0 ? (
        <div className="env-editor__rows">
          {entries.map((entry) => (
            <div className="env-editor__row" key={entry.id}>
              <input
                className="env-editor__input env-editor__input--key"
                type="text"
                value={entry.key}
                onChange={(e) => update(entry.id, { key: e.target.value })}
                placeholder="KEY"
                disabled={disabled}
                spellCheck={false}
                autoCapitalize="none"
                autoComplete="off"
                autoCorrect="off"
                aria-label="Variable name"
              />
              <input
                className="env-editor__input env-editor__input--value"
                type={valueType}
                value={entry.value}
                onChange={(e) => update(entry.id, { value: e.target.value })}
                placeholder="value"
                disabled={disabled}
                spellCheck={false}
                autoCapitalize="none"
                autoComplete="off"
                autoCorrect="off"
                aria-label="Variable value"
              />
              <button
                type="button"
                className="env-editor__remove"
                onClick={() => remove(entry.id)}
                disabled={disabled}
                aria-label={`Remove ${entry.key || "variable"}`}
                title="Remove"
              >
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path
                    d="M3 3l6 6M9 3l-6 6"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <button
        type="button"
        className="env-editor__add"
        onClick={add}
        disabled={disabled}
      >
        <span className="env-editor__add-glyph" aria-hidden="true">
          +
        </span>
        Add variable
      </button>
    </div>
  );
}
