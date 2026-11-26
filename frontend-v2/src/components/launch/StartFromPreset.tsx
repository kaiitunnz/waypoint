"use client";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { orderPresets, presetSubLabel } from "@waypoint/web-core/launch/presets";
import type { SessionPresetSummary } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { SectionHeading } from "@/components/ui/SectionHeading";

import type { LaunchRequest } from "./LaunchDialog";
import styles from "./StartFromPreset.module.css";

const SHOWN = 4;

interface StartFromPresetProps {
  presets: SessionPresetSummary[];
  defaultPresetId: string | null;
  catalog: BackendCatalog;
  onOpen: (request: LaunchRequest) => void;
}

// The home's quick launch: the default preset first, then the other ways in.
export function StartFromPreset({ presets, defaultPresetId, catalog, onOpen }: StartFromPresetProps) {
  const shown = orderPresets(presets, defaultPresetId).slice(0, SHOWN);
  return (
    <section>
      <SectionHeading
        title="Start from a preset"
        action={
          <button type="button" onClick={() => onOpen({ mode: "new", preset: { kind: "default" } })}>
            All {presets.length}
          </button>
        }
      />
      {shown.length > 0 ? (
        <ul className={styles.presets}>
          {shown.map((preset) => (
            <li key={preset.id}>
              <button
                type="button"
                className={styles.preset}
                onClick={() => onOpen({ mode: "new", preset: { kind: "preset", presetId: preset.id } })}
              >
                <span className={styles.name}>{preset.name}</span>
                <span className={styles.sub}>{presetSubLabel(preset.spec, catalog)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className={styles.links}>
        <Button variant="text" size="sm" onClick={() => onOpen({ mode: "resume", preset: { kind: "default" } })}>
          Resume a thread
        </Button>
        <Button variant="text" size="sm" onClick={() => onOpen({ mode: "attach", preset: { kind: "default" } })}>
          Attach tmux pane
        </Button>
        <Button variant="text" size="sm" onClick={() => onOpen({ mode: "schedule", preset: { kind: "default" } })}>
          Schedule
        </Button>
      </div>
    </section>
  );
}
