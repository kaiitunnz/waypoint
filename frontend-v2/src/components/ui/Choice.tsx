"use client";

import { useId, useState, type ReactNode } from "react";

import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { Sheet } from "./Dialog";
import { Field } from "./Field";
import { Icon } from "./Icon";
import { Select } from "./Select";
import { SettingsRow } from "./Settings";
import styles from "./Choice.module.css";

export interface ChoiceOption {
  value: string;
  label: string;
  // A second line in the touch picker.
  detail?: string;
  disabled?: boolean;
}

interface ChoiceFieldProps {
  label: string;
  value: string;
  options: ChoiceOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  // Shown under the field on a pointer client, and as the picker's subtitle on touch.
  hint?: ReactNode;
  mono?: boolean;
}

// One choice from a short list: a labelled select on a pointer client, and on
// touch a row with the current value that opens a sheet of the options.
export function ChoiceField({ label, value, options, onChange, disabled, hint, mono }: ChoiceFieldProps) {
  const pointer = usePointerUI();
  const id = useId();
  const [open, setOpen] = useState(false);
  const current = options.find((option) => option.value === value);

  if (pointer) {
    return (
      <Field label={label} htmlFor={id}>
        <Select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </Select>
        {hint ? <span className={styles.hint}>{hint}</span> : null}
      </Field>
    );
  }
  return (
    <>
      <SettingsRow label={label} detail={current?.label ?? value} mono={mono} disabled={disabled} onPress={() => setOpen(true)} />
      <ChoiceSheet
        open={open}
        title={label}
        subtitle={hint}
        value={value}
        options={options}
        mono={mono}
        onClose={() => setOpen(false)}
        onChange={(next) => {
          setOpen(false);
          onChange(next);
        }}
      />
    </>
  );
}

interface ChoiceSheetProps {
  open: boolean;
  title: string;
  subtitle?: ReactNode;
  value: string;
  options: ChoiceOption[];
  mono?: boolean;
  onClose: () => void;
  onChange: (value: string) => void;
}

// The touch picker: the current option takes a fill and a check.
export function ChoiceSheet({ open, title, subtitle, value, options, mono, onClose, onChange }: ChoiceSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} title={title} subtitle={subtitle} flush>
      <ul className={styles.list} role="listbox" aria-label={title}>
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <li key={option.value}>
              <button
                type="button"
                role="option"
                aria-selected={selected}
                disabled={option.disabled}
                className={cx(styles.option, selected && styles.selected)}
                onClick={() => onChange(option.value)}
              >
                <span className={styles.check}>{selected ? <Icon name="check" size={18} /> : null}</span>
                <span className={styles.text}>
                  <span className={cx(styles.label, mono && styles.mono)}>{option.label}</span>
                  {option.detail ? <span className={styles.detail}>{option.detail}</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}
