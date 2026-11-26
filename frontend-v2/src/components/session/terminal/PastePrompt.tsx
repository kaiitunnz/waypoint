"use client";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./PastePrompt.module.css";

interface PastePromptProps {
  length: number;
  onSend: () => void;
  onDismiss: () => void;
}

export function PastePrompt({ length, onSend, onDismiss }: PastePromptProps) {
  return (
    <div className={styles.prompt} role="status">
      <span className={styles.text}>
        <strong>Paste ready</strong>
        <span className={styles.meta}>{length.toLocaleString()} characters</span>
      </span>
      <Button variant="primary" size="sm" onClick={onSend} aria-label={`Send ${length} characters from the clipboard`}>
        Send
      </Button>
      <IconButton label="Cancel paste" size="sm" onClick={onDismiss}>
        <Icon name="close" size={18} />
      </IconButton>
    </div>
  );
}
