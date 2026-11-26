"use client";

import {
  addEnvEntry,
  editEnvValue,
  envRowState,
  envValueDisplay,
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
}

// Replacing or removing a value is staged on the row until undone.
export function EnvRows({ entries, onChange, disabled }: EnvRowsProps) {
  return (
    <div className={styles.editor}>
      {entries.length > 0 ? (
        <ul className={styles.rows}>
          {entries.map((entry) => {
            const state = envRowState(entry);
            const label = entry.key || "variable";
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
                  <span className={styles.value} title={entry.value ? undefined : "Empty"}>
                    {envValueDisplay(entry.key, entry.value)}
                  </span>
                ) : (
                  <TextInput
                    mono
                    autoComplete="off"
                    aria-label={`Value of ${label}`}
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
                        onClick={() => onChange(editEnvValue(entries, entry.id, entry.value))}
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
