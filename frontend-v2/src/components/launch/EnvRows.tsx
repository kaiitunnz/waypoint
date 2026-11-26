"use client";

import {
  addEnvEntry,
  editEnvValue,
  envRowState,
  envValueDisplay,
  removeEnvEntry,
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
}

const machine = { spellCheck: false, autoCapitalize: "none", autoComplete: "off", autoCorrect: "off" } as const;

// One row per variable: the key in full, the value short or masked, and a pencil
// and a ×. Replacing or removing a value shows inline until undone.
export function EnvRows({ entries, onChange, disabled }: EnvRowsProps) {
  return (
    <div className={styles.editor}>
      {entries.length > 0 ? (
        <ul className={styles.rows}>
          {entries.map((entry) => {
            const state = envRowState(entry);
            const label = entry.key || "variable";
            const undo = (
              <Button variant="text" size="sm" disabled={disabled} onClick={() => onChange(undoEnvEntry(entries, entry.id))}>
                Undo
              </Button>
            );
            return (
              <li key={entry.id} className={cx(styles.row, state === "add" && styles.added)}>
                {state === "add" ? (
                  <TextInput
                    {...machine}
                    mono
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
                  <span className={styles.value} title={entry.value ? undefined : "Empty"}>
                    {envValueDisplay(entry.key, entry.value)}
                  </span>
                ) : (
                  <TextInput
                    {...machine}
                    mono
                    aria-label={`Value of ${label}`}
                    placeholder="value"
                    autoFocus={state === "edit"}
                    disabled={disabled}
                    className={styles.valueInput}
                    value={entry.value}
                    onChange={(event) =>
                      onChange(
                        state === "add"
                          ? updateEnvEntry(entries, entry.id, { value: event.target.value })
                          : editEnvValue(entries, entry.id, event.target.value),
                      )
                    }
                  />
                )}
                <span className={styles.actions}>
                  {state === "keep" ? (
                    <>
                      <IconButton
                        label={`Replace ${label}`}
                        size="sm"
                        disabled={disabled}
                        onClick={() => onChange(editEnvValue(entries, entry.id, entry.value))}
                      >
                        <Icon name="pencil" size={16} />
                      </IconButton>
                      <IconButton
                        label={`Remove ${label}`}
                        size="sm"
                        disabled={disabled}
                        onClick={() => onChange(removeEnvEntry(entries, entry.id))}
                      >
                        <Icon name="close" size={16} />
                      </IconButton>
                    </>
                  ) : (
                    undo
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
