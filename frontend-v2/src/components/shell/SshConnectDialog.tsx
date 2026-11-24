"use client";

import { useState, type FormEvent } from "react";

import type { SshConnectOutcome } from "@waypoint/web-core/hooks/useLaunchTargets";
import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";

import { Button } from "@/components/ui/Button";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { Field, PasswordInput } from "@/components/ui/Field";
import { DESKTOP_QUERY } from "@/lib/layout";

import styles from "./SshConnectDialog.module.css";

interface SshConnectDialogProps {
  targetName: string;
  onConnect: (password: string) => Promise<SshConnectOutcome>;
  onClose: () => void;
}

// Asks for the SSH password that opens a password-auth target's connection. The
// password lives in this component's state only and is gone when it closes.
export function SshConnectDialog({ targetName, onConnect, onClose }: SshConnectDialogProps) {
  const desktop = useMediaQuery(DESKTOP_QUERY);
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
          disabled={busy}
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
  const title = `Connect to ${targetName}`;
  return desktop ? (
    <Modal open title={title} onClose={onClose} footer={footer} size="sm">
      {body}
    </Modal>
  ) : (
    <Sheet open title={title} onClose={onClose} footer={footer}>
      {body}
    </Sheet>
  );
}
