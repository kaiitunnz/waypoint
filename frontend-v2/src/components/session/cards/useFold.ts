import { useMemo, useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";

// How much of a folded block shows. A line takes at least one visual line, so cutting
// to this many first keeps long text out of the page until it is opened.
const FOLD_LINES = 8;

// Text that shows only its start or its end until it is opened.
export function useFold(text: string, keep: "start" | "end") {
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldLines(text, FOLD_LINES, keep), [text, keep]);
  const whole = useMemo(() => foldLines(text, Infinity, keep).text, [text, keep]);
  return {
    open,
    toggle: () => setOpen(!open),
    shown: open ? whole : folded.text,
    total: folded.total,
    // Lines the fold leaves out, apart from what the box clips.
    hidden: folded.hidden,
  };
}
