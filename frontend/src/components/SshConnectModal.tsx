"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { PasswordInput } from "@/components/PasswordInput";
import { trapTabFocus } from "@/lib/keyboard";

interface SshConnectModalProps {
  targetName: string;
  error?: string | null;
  onSubmit: (password: string) => Promise<void>;
  onCancel: () => void;
}

// Prompts for an SSH password to seed a password-auth target's ControlMaster
// connection. The password is held in local component state only and is never
// written to the store or localStorage — it vanishes when the modal unmounts
// (on success or cancel). On a failed attempt the field is kept so the user
// can correct a typo without retyping from scratch.
export function SshConnectModal({
  targetName,
  error,
  onSubmit,
  onCancel,
}: SshConnectModalProps) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const modalRef = useRef<HTMLFormElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Restore focus to whatever opened the modal (the Connect button) on close.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      trapTabFocus(event, modalRef.current);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!password || busy) {
      return;
    }
    setBusy(true);
    try {
      await onSubmit(password);
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="ssh-connect-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel();
        }
      }}
    >
      <form
        ref={modalRef}
        className="ssh-connect-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ssh-connect-title"
        onSubmit={handleSubmit}
      >
        <div className="ssh-connect-head">
          <strong id="ssh-connect-title">Connect to {targetName}</strong>
          <span className="muted">SSH password · never stored</span>
        </div>
        <label className="field">
          <span>SSH password</span>
          <PasswordInput
            ref={inputRef}
            // Intentionally off: this is an ephemeral connection secret we never
            // persist, so we don't invite a password manager to save it.
            autoComplete="off"
            value={password}
            disabled={busy}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "ssh-connect-error" : undefined}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? (
          <span id="ssh-connect-error" className="ssh-connect-error" role="alert">
            {error}
          </span>
        ) : null}
        <div className="ssh-connect-actions">
          <button
            type="button"
            className="secondary"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy || !password}>
            {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
