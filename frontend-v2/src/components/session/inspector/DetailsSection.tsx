import type { ReactNode } from "react";

import type { DetailRow } from "@waypoint/web-core/session/sessionDetails";

import { CopyButton } from "@/components/session/cards/CopyButton";
import { PathLabel } from "@/components/session/cards/PathLabel";

import { Section } from "./Section";
import styles from "./DetailsSection.module.css";

function Value({ row }: { row: DetailRow }) {
  // A folder is cut from its left whole; it has no file name whose end must stay.
  if (row.kind === "path") return <PathLabel dir={row.value} base="" full={row.value} folder />;
  if (row.kind === "id") {
    return (
      <span className={styles.id} title={row.value}>
        <bdi dir="ltr">{row.value}</bdi>
      </span>
    );
  }
  return (
    <span className={styles.text} title={row.title}>
      {row.value}
      {row.note ? <span className={styles.note}>{row.note}</span> : null}
    </span>
  );
}

// The session's facts, one per line; a machine string keeps its end when it is long.
export function DetailsSection({ rows, children }: { rows: DetailRow[]; children?: ReactNode }) {
  return (
    <Section title="Details">
      <dl className={styles.list}>
        {rows.map((row) => (
          <div key={row.id} className={styles.row}>
            <dt className={styles.label}>{row.label}</dt>
            <dd className={styles.value}>
              <Value row={row} />
            </dd>
            {row.copy ? <CopyButton text={row.value} label={`Copy ${row.label.toLowerCase()}`} className={styles.copy} /> : <span />}
          </div>
        ))}
      </dl>
      {children}
    </Section>
  );
}
