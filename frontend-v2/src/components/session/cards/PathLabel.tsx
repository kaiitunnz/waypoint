"use client";

import { useContext, useState } from "react";

import { useLongPress } from "@waypoint/web-core/hooks/useLongPress";
import { splitFileName } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { TranscriptHoldContext } from "../TranscriptHold";
import styles from "./PathLabel.module.css";

interface PathLabelProps {
  dir: string;
  base: string;
  // The whole path, shown on hover and when a long press opens it.
  full: string;
  className?: string;
}

// One line: the folder gives way first, from its left, then the middle of the name.
export function PathLabel({ dir, base, full, className }: PathLabelProps) {
  const [open, setOpen] = useState(false);
  const hold = useContext(TranscriptHoldContext);
  const press = useLongPress((element) => {
    hold(element, open);
    setOpen(!open);
  });
  const name = splitFileName(base);
  return (
    <span className={cx(styles.path, open && styles.open, className)} title={full} {...press}>
      {open ? (
        full
      ) : (
        <>
          {dir ? (
            <span className={styles.dir}>
              <bdi dir="ltr">{dir}</bdi>
            </span>
          ) : null}
          <span className={styles.base}>
            <span className={styles.head}>{name.head}</span>
            <span className={styles.tail}>{name.tail}</span>
          </span>
        </>
      )}
    </span>
  );
}
