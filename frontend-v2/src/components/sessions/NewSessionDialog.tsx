"use client";

import { Button } from "@/components/ui/Button";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { useDesktop } from "@/lib/layout";

interface NewSessionDialogProps {
  open: boolean;
  onClose: () => void;
}

// The entry point for starting a session. Launching is not built here yet, so the
// body says so; the launch form replaces it without changing this entry.
export function NewSessionDialog({ open, onClose }: NewSessionDialogProps) {
  const Surface = useDesktop() ? Modal : Sheet;
  return (
    <Surface
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
    </Surface>
  );
}
