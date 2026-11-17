"use client";

import { Fragment } from "react";

interface HighlightedTextProps {
  text: string;
  positions: ReadonlyArray<number>;
  breakAfterSlash?: boolean;
}

function withSlashBreaks(value: string, enabled: boolean) {
  if (!enabled || !value.includes("/")) return value;
  return value.split("/").map((part, index, parts) => (
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

export function HighlightedText({
  text,
  positions,
  breakAfterSlash = false,
}: HighlightedTextProps) {
  if (positions.length === 0) return <>{withSlashBreaks(text, breakAfterSlash)}</>;
  const hits = new Set(positions);
  const runs: Array<{ hit: boolean; value: string }> = [];
  for (let index = 0; index < text.length; index++) {
    const hit = hits.has(index);
    const last = runs[runs.length - 1];
    if (last && last.hit === hit) {
      last.value += text[index];
    } else {
      runs.push({ hit, value: text[index] });
    }
  }
  return (
    <>
      {runs.map((run, index) =>
        run.hit ? (
          <mark key={index} className="match-hit">
            {withSlashBreaks(run.value, breakAfterSlash)}
          </mark>
        ) : (
          <span key={index}>{withSlashBreaks(run.value, breakAfterSlash)}</span>
        ),
      )}
    </>
  );
}
