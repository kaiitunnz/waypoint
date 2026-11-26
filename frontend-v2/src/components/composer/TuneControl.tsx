"use client";

import { useId, type RefObject } from "react";

import { effortLabel } from "@waypoint/web-core/modelDisplay";
import { interruptPrompt, pendingRestartLabel, tuningSummary } from "@waypoint/web-core/session/tuning";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Flyout } from "@/components/ui/Flyout";
import { Icon } from "@/components/ui/Icon";
import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { usePointerUI } from "@/lib/layout";

import styles from "./TuneControl.module.css";
import type { ComposerTuning } from "./useComposerTuning";
import type { SessionFocus } from "./useSessionFocus";

interface TuneControlProps {
  tuning: ComposerTuning;
  triggerRef: RefObject<HTMLButtonElement | null>;
  agentName: string;
  disabled: boolean;
  focus: SessionFocus;
}

export function TuneControl({ tuning, triggerRef, agentName, disabled, focus }: TuneControlProps) {
  const id = useId();
  const pointer = usePointerUI();
  const { input, options, draft, busy, defaultEffort, open, interrupt } = tuning;
  const { modeOptions, defaultModelLabel } = input;
  const summary = tuningSummary(input, options);
  const parts = summary.length > 0 ? summary : ["Settings"];
  const restartBusy = busy.mode || busy.model || busy.effort;

  async function applyRestart() {
    tuning.setOpen(false);
    // The popover took the focus with it.
    if (pointer) triggerRef.current?.focus();
    await draft.applyRestart();
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Mode and model: ${parts.join(", ")}${draft.hasPending ? ". A change waits to be applied" : ""}`}
        onClick={() => tuning.setOpen(!open)}
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
        onClose={() => tuning.setOpen(false)}
        triggerRef={triggerRef}
        title="Mode and model"
        autoFocus={tuning.takesFocus}
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
                {options.customEffort ? <option value={options.customEffort}>{options.customEffort}</option> : null}
              </Select>
            </label>
          ) : null}
          <div className={styles.focus}>
            <span className={styles.focusText}>
              <span>Focus</span>
              <span className={styles.label}>Hold incoming messages</span>
            </span>
            <Toggle checked={focus.on} label="Focus" disabled={focus.busy} onChange={(enabled) => void focus.set(enabled)} />
          </div>
          {draft.hasPending ? (
            <div className={styles.restart}>
              <p>
                Restart {agentName} with <strong>{pendingRestartLabel(draft, modeOptions, options.modelEntries)}</strong>?
                The current turn is interrupted and the session resumes with the new settings.
              </p>
              <Button variant="primary" size="sm" busy={restartBusy} onClick={() => void applyRestart()}>
                {restartBusy ? "Restarting…" : "Apply restart"}
              </Button>
            </div>
          ) : null}
        </div>
      </Flyout>
      <ConfirmDialog
        open={interrupt !== null}
        title="Interrupt the current turn?"
        detail={interrupt ? interruptPrompt(interrupt.field) : ""}
        confirmLabel="Continue"
        onConfirm={() => interrupt?.answer(true)}
        onClose={tuning.dismissInterrupt}
      />
    </>
  );
}
