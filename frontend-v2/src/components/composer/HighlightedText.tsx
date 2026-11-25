import { Fragment, type ReactNode } from "react";

import { highlightRuns } from "@waypoint/web-core/fuzzy";

import styles from "./SuggestionMenu.module.css";

// A long path may wrap after any of its slashes.
function breakable(value: string): ReactNode {
  const parts = value.split("/");
  return parts.map((part, index) => (
    <Fragment key={index}>
      {part}
      {index < parts.length - 1 ? (
        <>
          /<wbr />
        </>
      ) : null}
    </Fragment>
  ));
}

interface HighlightedTextProps {
  text: string;
  // The matched character indices.
  positions: ReadonlyArray<number>;
  path?: boolean;
}

export function HighlightedText({ text, positions, path }: HighlightedTextProps) {
  return highlightRuns(text, positions).map((run, index) => {
    const value = path ? breakable(run.value) : run.value;
    return run.hit ? (
      <mark key={index} className={styles.hit}>
        {value}
      </mark>
    ) : (
      <Fragment key={index}>{value}</Fragment>
    );
  });
}
