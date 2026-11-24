"use client";

import { useState } from "react";

import { useDesktop } from "@/lib/layout";

import { Button } from "./Button";
import { Modal, Sheet } from "./Dialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  // What the action does and what it cannot undo.
  detail: string;
  confirmLabel: string;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

// A destructive action asks first: a modal on desktop, a sheet on mobile.
export function ConfirmDialog({ open, title, detail, confirmLabel, onConfirm, onClose }: ConfirmDialogProps) {
  const desktop = useDesktop();
  const [busy, setBusy] = useState(false);
  const Surface = desktop ? Modal : Sheet;

  async function confirm() {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
      onClose();
    }
  }

  return (
    <Surface
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} onClick={() => void confirm()}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p>{detail}</p>
    </Surface>
  );
}
