"use client";

import { applyEnvEntries, settingsEnvEntries } from "@waypoint/web-core/session/settingsEnv";
import type { SessionSettingsController } from "@waypoint/web-core/hooks/useSessionSettings";

import { EnvRows } from "@/components/launch/EnvRows";

import styles from "./SettingsFields.module.css";

interface SettingsEnvProps {
  controller: SessionSettingsController;
  disabled: boolean;
}

// The session's environment: its keys, each kept, replaced or removed, and new ones. A
// stored value is never fetched, so every value shows masked and every one typed is masked.
export function SettingsEnv({ controller, disabled }: SettingsEnvProps) {
  const { existingEnv, newEnv, hiddenEnvKeys, changed } = controller;
  const entries = settingsEnvEntries(existingEnv, newEnv, hiddenEnvKeys);
  return (
    <div className={styles.env}>
      <span className={styles.envLabel}>
        Environment variables
        {changed.env ? <i className={styles.dot} role="img" aria-label="Changed" /> : null}
      </span>
      <EnvRows
        entries={entries}
        disabled={disabled}
        valuesHidden
        onChange={(next) => {
          const applied = applyEnvEntries(next, existingEnv);
          controller.setExistingEnv(applied.existing);
          controller.setNewEnv(applied.added);
        }}
      />
    </div>
  );
}
