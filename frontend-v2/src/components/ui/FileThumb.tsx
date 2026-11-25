"use client";

import { useState, type ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./FileThumb.module.css";
import { Icon } from "./Icon";

interface FileThumbProps {
  // The image to show, or null for a file without one.
  src: string | null;
  alt?: string;
  // Stands for a file without an image.
  mark?: ReactNode;
  className?: string;
}

// An image that fails to load, such as a deleted upload's, shows the mark.
export function FileThumb({ src, alt = "", mark = <Icon name="file" size={16} />, className }: FileThumbProps) {
  const [failed, setFailed] = useState<string | null>(null);
  if (src && src !== failed) {
    return (
      // A blob or token-query URL: next/image cannot optimise it.
      // eslint-disable-next-line @next/next/no-img-element
      <img className={cx(styles.thumb, className)} src={src} alt={alt} loading="lazy" onError={() => setFailed(src)} />
    );
  }
  return (
    <span className={cx(styles.tile, className)} aria-hidden="true">
      {mark}
    </span>
  );
}
