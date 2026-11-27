import Link from "next/link";
import type { ReactNode } from "react";

import styles from "./SourceLine.module.css";

interface SourceLineProps {
  lead: ReactNode;
  name: string;
  href?: string;
  kind: string;
  time?: ReactNode;
}

export function SourceLine({ lead, name, href, kind, time }: SourceLineProps) {
  return (
    <div className={styles.source}>
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
