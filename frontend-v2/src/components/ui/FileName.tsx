import { highlightRuns, positionsWithin } from "@waypoint/web-core/fuzzy";
import { splitFileName } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import styles from "./FileName.module.css";

interface FileNameProps {
  name: string;
  // Fuzzy-match positions in `name` to show in bold.
  positions?: ReadonlyArray<number>;
  title?: string;
  className?: string;
}

function Text({ text, positions }: { text: string; positions: ReadonlyArray<number> }) {
  return highlightRuns(text, positions).map((run, index) =>
    run.hit ? (
      <mark key={index} className={styles.hit}>
        {run.value}
      </mark>
    ) : (
      run.value
    ),
  );
}

// A file name on one line that gives up the middle of its name first: the extension and the
// characters before it stay. The two parts are flex items, which read and copy as separate
// blocks, so the whole name is carried once for assistive technology.
export function FileName({ name, positions = [], title, className }: FileNameProps) {
  const { head, tail } = splitFileName(name);
  return (
    <span className={cx(styles.name, className)} title={title}>
      {head ? (
        <span className={styles.head} aria-hidden="true">
          <Text text={head} positions={positionsWithin(positions, 0, head.length)} />
        </span>
      ) : null}
      <span className={styles.tail} aria-hidden="true">
        <Text text={tail} positions={positionsWithin(positions, head.length, tail.length)} />
      </span>
      <span className={styles.whole}>{name}</span>
    </span>
  );
}
