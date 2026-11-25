"use client";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

interface NewSessionDialogProps {
  open: boolean;
  onClose: () => void;
}

export function NewSessionDialog({ open, onClose }: NewSessionDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New session"
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      <p>Launching a session from this interface is not available yet.</p>
    </Dialog>
  );
}
