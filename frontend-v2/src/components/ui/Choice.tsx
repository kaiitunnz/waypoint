"use client";

import { useId, useRef, useState, type ReactNode } from "react";

import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { Sheet } from "./Dialog";
import { Field } from "./Field";
import { Flyout } from "./Flyout";
import { Icon } from "./Icon";
import { Menu, MenuItem } from "./Popover";
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
}

// One choice from a short list: a labelled select on a pointer client, and on
// touch a row with the current value that opens a sheet of the options.
export function ChoiceField({ label, value, options, onChange, disabled, hint }: ChoiceFieldProps) {
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
      <SettingsRow label={label} detail={current?.label ?? value} disabled={disabled} onPress={() => setOpen(true)} />
      <ChoiceSheet
        open={open}
        title={label}
        subtitle={hint}
        value={value}
        options={options}
        onClose={() => setOpen(false)}
        onChange={(next) => {
          setOpen(false);
          onChange(next);
        }}
      />
    </>
  );
}

interface ChoiceRowProps {
  label: string;
  value: string;
  options: ChoiceOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  // The value is changed and not yet applied.
  staged?: boolean;
  // The picker's subtitle on touch.
  hint?: ReactNode;
  // The picker opens, for options loaded on demand.
  onOpen?: () => void;
}

// One choice from a short list, as a row showing the current value: a menu from the row
// on a pointer client, and on touch a sheet of the options. Where the form is a column of
// rows on every client, such as an editor beside the conversation.
export function ChoiceRow({ label, value, options, onChange, disabled, staged, hint, onOpen }: ChoiceRowProps) {
  const pointer = usePointerUI();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const current = options.find((option) => option.value === value);
  const choose = (next: string) => {
    setOpen(false);
    onChange(next);
  };
  return (
    <>
      <SettingsRow
        label={label}
        detail={current?.label ?? value}
        disabled={disabled}
        staged={staged}
        buttonRef={trigger}
        onPress={() => {
          if (!open) onOpen?.();
          setOpen((shown) => !shown);
        }}
      />
      {pointer ? (
        <Flyout open={open} onClose={() => setOpen(false)} triggerRef={trigger} title={label} width={260} align="right" flush>
          <Menu label={label}>
            {options.map((option) => (
              <MenuItem key={option.value} selected={option.value === value} disabled={option.disabled} onSelect={() => choose(option.value)}>
                {option.label}
              </MenuItem>
            ))}
          </Menu>
        </Flyout>
      ) : (
        <ChoiceSheet open={open} title={label} subtitle={hint} value={value} options={options} onClose={() => setOpen(false)} onChange={choose} />
      )}
    </>
  );
}

interface ChoiceSheetProps {
  open: boolean;
  title: string;
  subtitle?: ReactNode;
  value: string;
  options: ChoiceOption[];
  onClose: () => void;
  onChange: (value: string) => void;
}

// The touch picker: the current option takes a fill and a check.
function ChoiceSheet({ open, title, subtitle, value, options, onClose, onChange }: ChoiceSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} title={title} subtitle={subtitle} flush>
      <ul className={styles.list} role="listbox" aria-label={title}>
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <li key={option.value} role="none">
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
                  <span className={styles.label}>{option.label}</span>
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
