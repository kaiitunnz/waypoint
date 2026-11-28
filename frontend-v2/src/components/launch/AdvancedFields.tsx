"use client";

import { useId, useState, type ReactNode } from "react";

import { envSummary, type EnvEntry } from "@waypoint/web-core/launch/env";

import { Button } from "@/components/ui/Button";
import { Sheet } from "@/components/ui/Dialog";
import { Field, TextArea } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { SettingsRow } from "@/components/ui/Settings";
import { usePointerUI } from "@/lib/layout";

import { EnvRows } from "./EnvRows";
import styles from "./AdvancedFields.module.css";

interface DisclosureProps {
  title: string;
  // Shown while folded.
  summary: string;
  children: ReactNode;
}

export function Disclosure({ title, summary, children }: DisclosureProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={styles.disclosure} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={styles.title}>{title}</span>
        {open ? null : <span className={styles.summary}>{summary}</span>}
        <Icon name={open ? "chevron-up" : "chevron-down"} size={18} />
      </button>
      {open ? children : null}
    </>
  );
}

// On touch a row showing the current value, whose editor opens in a sheet.
function EditorRow({ label, summary, staged, children }: { label: string; summary: string; staged?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingsRow label={label} detail={summary || "None"} mono={Boolean(summary)} staged={staged} onPress={() => setOpen(true)} />
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        title={label}
        footer={
          <Button variant="primary" block onClick={() => setOpen(false)}>
            Done
          </Button>
        }
      >
        {children}
      </Sheet>
    </>
  );
}

interface EnvFieldProps {
  entries: EnvEntry[];
  onChange: (entries: EnvEntry[]) => void;
  disabled: boolean;
}

export function EnvField({ entries, onChange, disabled }: EnvFieldProps) {
  const pointer = usePointerUI();
  const rows = <EnvRows entries={entries} onChange={onChange} disabled={disabled} />;
  if (!pointer) {
    return (
      <EditorRow label="Environment" summary={envSummary(entries)}>
        {rows}
      </EditorRow>
    );
  }
  return (
    <div className={styles.envField}>
      <span className={styles.label}>Environment variables</span>
      {rows}
    </div>
  );
}

const firstLine = (text: string) => text.split("\n").map((line) => line.trim()).find(Boolean) ?? "";

interface LinesFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  // The value is changed and not yet applied.
  staged?: boolean;
}

// One machine string per line, such as CLI arguments.
export function LinesField({ label, value, onChange, disabled, staged }: LinesFieldProps) {
  const pointer = usePointerUI();
  const id = useId();
  const area = (
    <TextArea id={id} rows={3} mono value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} />
  );
  if (!pointer) {
    return (
      <EditorRow label={label} summary={firstLine(value)} staged={staged}>
        {area}
      </EditorRow>
    );
  }
  return (
    <Field label={label} htmlFor={id} staged={staged}>
      {area}
    </Field>
  );
}
