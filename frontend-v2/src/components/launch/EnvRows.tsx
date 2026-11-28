"use client";

import {
  addEnvEntry,
  editEnvValue,
  ENV_MASK,
  envRowState,
  envValueDisplay,
  isSecretEnvKey,
  stageEnvRemoval,
  undoEnvEntry,
  updateEnvEntry,
  type EnvEntry,
} from "@waypoint/web-core/launch/env";

import { Button } from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import styles from "./EnvRows.module.css";

interface EnvRowsProps {
  entries: EnvEntry[];
  onChange: (entries: EnvEntry[]) => void;
  disabled?: boolean;
  // The values are not known, as for a running session's variables: an untouched row shows
  // a mask, and every value typed is masked, whatever its key.
  valuesHidden?: boolean;
}

// A password field invites the browser's saved sign-in, but this one holds an agent's secret.
const SECRET_INPUT = { type: "password", autoComplete: "new-password", "data-1p-ignore": "", "data-lpignore": "true" } as const;

// Replacing or removing a value is staged on the row until undone. A secret is
// replaced from empty and typed masked, so its value never shows.
export function EnvRows({ entries, onChange, disabled, valuesHidden }: EnvRowsProps) {
  return (
    <div className={styles.editor}>
      {entries.length > 0 ? (
        <ul className={styles.rows}>
          {entries.map((entry) => {
            const state = envRowState(entry);
            const label = entry.key || "variable";
            const secret = valuesHidden || isSecretEnvKey(entry.key);
            return (
              <li key={entry.id} className={cx(styles.row, state === "add" && styles.added)}>
                {state === "add" ? (
                  <TextInput
                    mono
                    autoComplete="off"
                    aria-label="Variable name"
                    placeholder="KEY"
                    data-autofocus=""
                    autoFocus
                    disabled={disabled}
                    className={styles.keyInput}
                    value={entry.key}
                    onChange={(event) => onChange(updateEnvEntry(entries, entry.id, { key: event.target.value }))}
                  />
                ) : (
                  <span className={cx(styles.key, state === "remove" && styles.struck)}>{entry.key}</span>
                )}
                {state === "remove" ? (
                  <span className={styles.removed}>Removed on apply</span>
                ) : state === "keep" ? (
                  <span className={styles.value} title={valuesHidden || entry.value ? undefined : "Empty"}>
                    {valuesHidden ? ENV_MASK : envValueDisplay(entry.key, entry.value)}
                  </span>
                ) : (
                  <TextInput
                    mono
                    aria-label={`Value of ${label}`}
                    {...(secret ? SECRET_INPUT : { autoComplete: "off" })}
                    placeholder="value"
                    autoFocus={state === "edit"}
                    disabled={disabled}
                    className={styles.valueInput}
                    value={entry.value}
                    onChange={(event) => onChange(editEnvValue(entries, entry.id, event.target.value))}
                  />
                )}
                <span className={styles.actions}>
                  {state === "keep" ? (
                    <>
                      <IconButton
                        label={`Replace ${label}`}
                        size="sm"
                        disabled={disabled}
                        onClick={() => onChange(editEnvValue(entries, entry.id, secret ? "" : entry.value))}
                      >
                        <Icon name="pencil" size={16} />
                      </IconButton>
                      <IconButton
                        label={`Remove ${label}`}
                        size="sm"
                        disabled={disabled}
                        onClick={() => onChange(stageEnvRemoval(entries, entry.id))}
                      >
                        <Icon name="close" size={16} />
                      </IconButton>
                    </>
                  ) : (
                    <Button variant="text" size="sm" disabled={disabled} onClick={() => onChange(undoEnvEntry(entries, entry.id))}>
                      Undo
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
      <Button variant="text" size="sm" disabled={disabled} onClick={() => onChange(addEnvEntry(entries))}>
        <Icon name="plus" size={16} />
        Add variable
      </Button>
    </div>
  );
}
