"use client";

import { useState } from "react";

import type { ChangesSummary } from "@waypoint/web-core/session/changesSummary";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { Disclosure } from "@/components/session/cards/Disclosure";
import { PathLabel } from "@/components/session/cards/PathLabel";

import { Section } from "./Section";
import styles from "./ChangesSection.module.css";

// Files shown before the rest fold behind "Show all".
const SHOWN = 5;

export const changesCountText = (count: number) => (count === 0 ? "None" : count === 1 ? "1 file" : `${count} files`);

// The workspace's changed files, from git status.
export function ChangesSection({ summary }: { summary: ChangesSummary }) {
  const [all, setAll] = useState(false);
  const files = all ? summary.files : summary.files.slice(0, SHOWN);
  return (
    <Section title="Changes" count={changesCountText(summary.count)}>
      {summary.count > 0 ? (
        <>
          <ul className={styles.files}>
            {files.map((file) => (
              <li key={file.path} className={styles.file}>
                <PathLabel {...splitPath(file.path)} full={file.path} className={styles.path} />
                <span className={styles.letter} role="img" aria-label={LETTER_LABEL[file.letter]}>
                  {file.letter}
                </span>
              </li>
            ))}
          </ul>
          {summary.count > SHOWN ? (
            <div className={styles.more}>
              <Disclosure open={all} total={summary.count} noun="files" onToggle={() => setAll(!all)} />
            </div>
          ) : null}
        </>
      ) : null}
    </Section>
  );
}

const LETTER_LABEL = { M: "Modified", A: "Added", D: "Deleted", R: "Renamed", U: "Untracked" } as const;
