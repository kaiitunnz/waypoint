"use client";

import { useState } from "react";

import { renameTarget } from "@waypoint/web-core/sessionActions";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { Field, TextInput } from "@/components/ui/Field";
import { useDesktop } from "@/lib/layout";

interface RenameDialogProps {
  session: SessionRecord;
  onSave: (title: string) => void;
  onClose: () => void;
}

export function RenameDialog({ session, onSave, onClose }: RenameDialogProps) {
  const Surface = useDesktop() ? Modal : Sheet;
  const [draft, setDraft] = useState(session.title);
  const title = renameTarget(session, draft);

  function save() {
    if (title === null) return;
    onSave(title);
    onClose();
  }

  return (
    <Surface
      open
      onClose={onClose}
      title="Rename session"
      footer={
        <>
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={title === null} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <Field label="Title" htmlFor="rename-title">
          <TextInput
            id="rename-title"
            data-autofocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
      </form>
    </Surface>
  );
}
