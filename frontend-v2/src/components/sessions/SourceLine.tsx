import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./SourceLine.module.css";

interface SourceLineProps {
  // The lamp or glyph that says what kind of source this is.
  lead?: ReactNode;
  name: string;
  // Where the name links to.
  href?: string;
  kind: string;
  time?: ReactNode;
  className?: string;
}

// Who is asking: a serif name, its kind, and when.
export function SourceLine({ lead, name, href, kind, time, className }: SourceLineProps) {
  return (
    <div className={cx(styles.source, className)}>
      {lead}
      {href ? (
        <Link href={href} className={styles.name}>
          {name}
        </Link>
      ) : (
        <span className={styles.name}>{name}</span>
      )}
      <span className={styles.kind}>{kind}</span>
      {time ? <span className={styles.time}>{time}</span> : null}
    </div>
  );
}
