"use client";

import { useId, useState, type InputHTMLAttributes, type ReactNode, type Ref } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import styles from "./Field.module.css";

interface FieldProps {
  label: string;
  htmlFor: string;
  children: ReactNode;
  className?: string;
}

export function Field({ label, htmlFor, children, className }: FieldProps) {
  return (
    <div className={cx(styles.field, className)}>
      <label className={styles.label} htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  ref?: Ref<HTMLInputElement>;
  // Machine strings (hosts, ports, paths) use the mono face.
  mono?: boolean;
}

export function TextInput({ mono, className, ref, ...rest }: TextInputProps) {
  return <input {...rest} ref={ref} className={cx(styles.input, mono && styles.mono, className)} />;
}

export function SearchInput({ className, ref, ...rest }: TextInputProps) {
  return (
    <span className={cx(styles.wrap, className)}>
      <span className={styles.lead}>
        <Icon name="search" size={17} />
      </span>
      <input {...rest} ref={ref} type="search" className={cx(styles.input, styles.withLead)} />
    </span>
  );
}

export function PasswordInput({ className, ref, ...rest }: Omit<TextInputProps, "type">) {
  const [shown, setShown] = useState(false);
  const id = useId();
  return (
    <span className={cx(styles.wrap, className)}>
      <input
        {...rest}
        id={rest.id ?? id}
        ref={ref}
        type={shown ? "text" : "password"}
        className={cx(styles.input, styles.withTrail)}
      />
      <button
        type="button"
        className={styles.trail}
        aria-label={shown ? "Hide password" : "Show password"}
        onClick={() => setShown((value) => !value)}
      >
        <Icon name={shown ? "eye-off" : "eye"} size={18} />
      </button>
    </span>
  );
}
