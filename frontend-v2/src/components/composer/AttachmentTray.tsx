import { filesSummary, formatFileSize, typeTag } from "@waypoint/web-core/attachments";
import type { PendingAttachment } from "@waypoint/web-core/hooks/useAttachments";

import { Button } from "@/components/ui/Button";
import { FileThumb } from "@/components/ui/FileThumb";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import styles from "./AttachmentTray.module.css";

interface AttachmentTrayProps {
  items: PendingAttachment[];
  onRemove: (localId: string) => void;
  onRetry: (localId: string) => void;
  onClear: () => void;
  // True while a send holds the files.
  locked: boolean;
}

export function AttachmentTray({ items, onRemove, onRetry, onClear, locked }: AttachmentTrayProps) {
  return (
    <div className={styles.tray}>
      <div className={styles.head}>
        <span>{filesSummary(items)}</span>
        <Button variant="text" size="sm" disabled={locked} onClick={onClear}>
          Clear all
        </Button>
      </div>
      <ul className={styles.chips} aria-label="Attached files">
        {items.map((item) => (
          <li
            key={item.localId}
            className={cx(styles.chip, item.status === "error" && styles.failed)}
            title={item.error ?? (item.referenced ? `${item.name} (linked)` : item.name)}
          >
            <FileThumb className={styles.thumb} src={item.isImage ? (item.previewUrl ?? null) : null} />
            <span className={styles.text}>
              <span className={styles.name}>{item.name}</span>
              <span className={styles.meta}>
                {item.status === "error"
                  ? "Upload failed"
                  : item.status === "uploading"
                    ? "Uploading…"
                    : `${item.referenced ? "↪ " : ""}${typeTag(item.name, item.isImage)}, ${formatFileSize(item.size)}`}
              </span>
            </span>
            {item.status === "error" ? (
              <IconButton label={`Retry ${item.name}`} size="sm" disabled={locked} onClick={() => onRetry(item.localId)}>
                <Icon name="refresh" size={16} />
              </IconButton>
            ) : null}
            <IconButton label={`Remove ${item.name}`} size="sm" disabled={locked} onClick={() => onRemove(item.localId)}>
              <Icon name="close" size={16} />
            </IconButton>
          </li>
        ))}
      </ul>
    </div>
  );
}
