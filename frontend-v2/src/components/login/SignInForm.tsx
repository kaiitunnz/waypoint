"use client";

import { useId, type FormEvent } from "react";

import type { ProbeStatus } from "@waypoint/web-core/hooks/useBackendProbe";

import { Button } from "@/components/ui/Button";
import { Field, PasswordInput, TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./SignInForm.module.css";

interface SignInFormProps {
  name: string;
  protocol: string;
  hostname: string;
  portText: string;
  portValid: boolean;
  onPortChange: (text: string) => void;
  // Leaves the host card to pick another host.
  onChangeHost: () => void;
  probe: ProbeStatus;
  password: string;
  onPasswordChange: (password: string) => void;
  onSubmit: () => void;
  busy: boolean;
  error: string;
}

const PROBE_LAMP: Record<ProbeStatus, LampState> = {
  idle: "idle",
  checking: "idle",
  reachable: "done",
  unreachable: "error",
};

// The password step: the chosen host with an editable port and the single
// reachability probe of that host and port, then the password.
export function SignInForm({
  name,
  protocol,
  hostname,
  portText,
  portValid,
  onPortChange,
  onChangeHost,
  probe,
  password,
  onPasswordChange,
  onSubmit,
  busy,
  error,
}: SignInFormProps) {
  const portId = useId();
  const passwordId = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy && portValid && password) onSubmit();
  };

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.host}>
        <StatusLamp state={PROBE_LAMP[probe]} />
        <span className={styles.name}>{name}</span>
        <Button variant="text" size="sm" onClick={onChangeHost} className={styles.change}>
          Change
        </Button>
        <span className={styles.url}>
          <span className={cx("mono", styles.address)}>
            {protocol}://{hostname}
          </span>
          <label className={styles.port}>
            <span className={styles.colon}>:</span>
            <TextInput
              id={portId}
              mono
              inputMode="numeric"
              autoComplete="off"
              aria-label="Port"
              aria-invalid={!portValid || undefined}
              value={portText}
              onChange={(event) => onPortChange(event.target.value)}
            />
          </label>
        </span>
        <span className={styles.probe} data-state={probe} role="status">
          {probe === "reachable" ? <Icon name="check" size={13} strokeWidth={2.2} /> : null}
          {probe === "checking" ? "Checking" : probe === "reachable" ? "Reachable" : probe === "unreachable" ? "Unreachable" : ""}
        </span>
      </div>
      <Field label="Password" htmlFor={passwordId} className={styles.password}>
        <PasswordInput
          id={passwordId}
          name="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => onPasswordChange(event.target.value)}
        />
      </Field>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <Button variant="primary" block type="submit" busy={busy} disabled={!password || !portValid}>
        {busy ? "Connecting" : "Connect"}
      </Button>
    </form>
  );
}
