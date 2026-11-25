"use client";

import { useId, type RefObject } from "react";

import type { TuningDraftControls } from "@waypoint/web-core/hooks/useTuningDraft";
import { effortLabel } from "@waypoint/web-core/modelDisplay";
import type { TuningOptions } from "@waypoint/web-core/session/tuning";
import type { BackendPermissionMode } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Flyout } from "@/components/ui/Flyout";
import { Icon } from "@/components/ui/Icon";
import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { cx } from "@/lib/cx";

import hit from "@/styles/hit.module.css";
import styles from "./TuneControl.module.css";

export interface TuneProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // False leaves the focus in the field when the popover opens.
  autoFocus: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  agentName: string;
  summary: string[];
  modeOptions: readonly BackendPermissionMode[];
  options: TuningOptions;
  draft: TuningDraftControls;
  restartLabel: string;
  busy: { mode: boolean; model: boolean; effort: boolean };
  disabled: boolean;
  currentEffort: string | null;
  defaultModelLabel: string | null;
  defaultEffort: string | null;
  focus: boolean;
  focusBusy: boolean;
  onFocusChange: (enabled: boolean) => void;
}

// The mode, model and effort control: its summary in the composer, and the
// settings it opens.
export function TuneControl({
  open,
  onOpenChange,
  autoFocus,
  triggerRef,
  agentName,
  summary,
  modeOptions,
  options,
  draft,
  restartLabel,
  busy,
  disabled,
  currentEffort,
  defaultModelLabel,
  defaultEffort,
  focus,
  focusBusy,
  onFocusChange,
}: TuneProps) {
  const id = useId();
  const parts = summary.length > 0 ? summary : ["Settings"];
  const restartBusy = busy.mode || busy.model || busy.effort;

  async function applyRestart() {
    onOpenChange(false);
    await draft.applyRestart();
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={cx(styles.trigger, hit.hit)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Mode and model: ${parts.join(", ")}${draft.hasPending ? ". A change waits to be applied" : ""}`}
        onClick={() => onOpenChange(!open)}
      >
        <span className={styles.parts} aria-hidden="true">
          {parts.map((part, index) => (
            <span key={index} className={styles.part}>
              {part}
            </span>
          ))}
        </span>
        {draft.hasPending ? <span className={styles.pending} aria-hidden="true" /> : null}
        <Icon name="chevron-down" size={14} strokeWidth={2} />
      </button>
      <Flyout
        open={open}
        onClose={() => onOpenChange(false)}
        triggerRef={triggerRef}
        title="Mode and model"
        autoFocus={autoFocus}
        width={320}
        side="top"
      >
        <div className={styles.body}>
          {modeOptions.length > 0 ? (
            <label className={styles.field} htmlFor={`${id}-mode`}>
              <span className={styles.label}>Permission mode</span>
              <Select
                id={`${id}-mode`}
                value={draft.shown.mode}
                disabled={busy.mode || disabled}
                onChange={(event) => draft.select("mode", event.target.value)}
              >
                {modeOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {options.hasModelPicker ? (
            <label className={styles.field} htmlFor={`${id}-model`}>
              <span className={styles.label}>Model</span>
              <Select
                id={`${id}-model`}
                value={draft.shown.model}
                disabled={busy.model || disabled}
                onChange={(event) => draft.select("model", event.target.value)}
              >
                <option value="">{defaultModelLabel ? `Default (${defaultModelLabel})` : "Default"}</option>
                {options.modelEntries.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {options.hasEffortPicker ? (
            <label className={styles.field} htmlFor={`${id}-effort`}>
              <span className={styles.label}>Reasoning effort</span>
              <Select
                id={`${id}-effort`}
                value={draft.shown.effort}
                disabled={busy.effort || disabled}
                onChange={(event) => draft.select("effort", event.target.value)}
              >
                <option value="">{defaultEffort ? `Default (${effortLabel(defaultEffort)})` : "Default"}</option>
                {options.effortOptions.map((option) => (
                  <option key={option} value={option}>
                    {effortLabel(option)}
                  </option>
                ))}
                {currentEffort && !options.effortOptions.includes(currentEffort) ? (
                  <option value={currentEffort}>{currentEffort}</option>
                ) : null}
              </Select>
            </label>
          ) : null}
          <div className={styles.focus}>
            <span className={styles.focusText}>
              <span>Focus</span>
              <span className={styles.label}>Hold incoming messages</span>
            </span>
            <Toggle checked={focus} label="Focus" onChange={focusBusy ? () => {} : onFocusChange} />
          </div>
          {draft.hasPending ? (
            <div className={styles.restart}>
              <p>
                Restart {agentName} with <strong>{restartLabel}</strong>? The current turn is interrupted and the session
                resumes with the new settings.
              </p>
              <Button variant="primary" size="sm" busy={restartBusy} onClick={() => void applyRestart()}>
                {restartBusy ? "Restarting…" : "Apply restart"}
              </Button>
            </div>
          ) : null}
        </div>
      </Flyout>
    </>
  );
}
