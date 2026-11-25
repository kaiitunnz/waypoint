"use client";

import { useState, type FormEvent } from "react";

import type { SshConnectOutcome } from "@waypoint/web-core/hooks/useLaunchTargets";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, PasswordInput } from "@/components/ui/Field";

import styles from "./SshConnectDialog.module.css";

interface SshConnectDialogProps {
  targetName: string;
  onConnect: (password: string) => Promise<SshConnectOutcome>;
  onClose: () => void;
}

// Asks for a password-auth target's SSH password. It lives in this component's
// state only and is gone when it closes.
export function SshConnectDialog({ targetName, onConnect, onClose }: SshConnectDialogProps) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    const outcome = await onConnect(password);
    setBusy(false);
    if (outcome.connected) {
      onClose();
    } else {
      setError(outcome.detail ?? "Authentication failed");
    }
  }

  const body = (
    <form id="ssh-connect-form" className={styles.form} onSubmit={submit}>
      <Field label="SSH password" htmlFor="ssh-password">
        <PasswordInput
          id="ssh-password"
          data-autofocus
          autoComplete="off"
          value={password}
          readOnly={busy}
          aria-invalid={error ? true : undefined}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
  const footer = (
    <>
      <span className={styles.grow} />
      <Button variant="text" onClick={onClose} disabled={busy}>
        Cancel
      </Button>
      <Button variant="primary" type="submit" form="ssh-connect-form" busy={busy} disabled={!password}>
        {busy ? "Connecting" : "Connect"}
      </Button>
    </>
  );
  return (
    <Dialog open title={`Connect to ${targetName}`} onClose={onClose} footer={footer}>
      {body}
    </Dialog>
  );
}
