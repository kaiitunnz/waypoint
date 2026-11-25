"use client";

import { copyText } from "@waypoint/web-core/clipboard";
import { useCopied } from "@waypoint/web-core/hooks/use-copied";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./CopyButton.module.css";

interface CopyButtonProps {
  text: string;
  label?: string;
  className?: string;
  size?: number;
}

export function CopyButton({ text, label = "Copy", className, size = 13 }: CopyButtonProps) {
  const { copied, markCopied } = useCopied();
  return (
    <button
      type="button"
      className={cx(styles.copy, className)}
      aria-label={copied ? "Copied" : label}
      onClick={() => {
        void copyText(text).then((ok) => {
          if (ok) markCopied();
        });
      }}
    >
      <Icon name={copied ? "check" : "copy"} size={size} strokeWidth={1.9} />
    </button>
  );
}
