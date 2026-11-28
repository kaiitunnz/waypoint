"use client";

import { useId } from "react";

import { usePointerUI } from "@/lib/layout";

import { Field, TextInput } from "./Field";
import styles from "./TitleField.module.css";

interface TitleFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  // The value is changed and not yet applied.
  staged?: boolean;
}

// A session's title: a labelled field on a pointer client, and on touch a row whose value
// is edited in place.
export function TitleField({ value, onChange, placeholder, disabled, staged }: TitleFieldProps) {
  const pointer = usePointerUI();
  const id = useId();
  if (pointer) {
    return (
      <Field label="Title" htmlFor={id} staged={staged}>
        <TextInput id={id} value={value} placeholder={placeholder} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
      </Field>
    );
  }
  return (
    <label className={styles.row}>
      <span>Title</span>
      {staged ? <i className={styles.staged} role="img" aria-label="Changed" /> : null}
      <input value={value} placeholder={placeholder} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}
