"use client";

import { useId } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import type { Backend } from "@waypoint/web-core/types";

import { ChoiceField } from "@/components/ui/Choice";
import { Field, TextInput } from "@/components/ui/Field";

import { agentOptions } from "./LaunchFields";
import styles from "./LaunchDialog.module.css";

export interface AttachState {
  target: string;
  agent: Backend;
  title: string;
}

interface AttachPaneProps {
  value: AttachState;
  onChange: (value: AttachState) => void;
  agents: Backend[];
  catalog: BackendCatalog;
  busy: boolean;
}

// Attach a running tmux pane, driven over the Terminal interface.
export function AttachPane({ value, onChange, agents, catalog, busy }: AttachPaneProps) {
  const id = useId();
  return (
    <div className={styles.stack}>
      <Field label="Tmux pane" htmlFor={`${id}-pane`}>
        <TextInput
          id={`${id}-pane`}
          mono
          data-autofocus=""
          spellCheck={false}
          autoCapitalize="none"
          autoCorrect="off"
          disabled={busy}
          value={value.target}
          onChange={(event) => onChange({ ...value, target: event.target.value })}
        />
      </Field>
      <ChoiceField
        label="Agent hint"
        value={value.agent}
        options={agentOptions(agents, catalog)}
        onChange={(agent) => onChange({ ...value, agent })}
        disabled={busy}
      />
      <Field label="Title" htmlFor={`${id}-title`}>
        <TextInput
          id={`${id}-title`}
          placeholder="Untitled"
          disabled={busy}
          value={value.title}
          onChange={(event) => onChange({ ...value, title: event.target.value })}
        />
      </Field>
    </div>
  );
}
