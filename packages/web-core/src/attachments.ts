import type { AttachmentSpec, EventRecord } from "./types";

// The attachments a message or tool event carries in its metadata.
export function attachmentSpecsFor(event: EventRecord): AttachmentSpec[] {
  const raw = event.metadata?.attachments;
  return Array.isArray(raw) ? (raw as AttachmentSpec[]) : [];
}

// Human-readable byte size, e.g. "812 B", "2.4 MB".
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
