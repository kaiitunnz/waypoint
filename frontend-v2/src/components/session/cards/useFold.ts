import { useMemo, useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";

import { useClamp } from "./useClamp";

// How much of a folded block shows. A line takes at least one visual line, so cutting
// to this many first keeps long text out of the page until it is opened.
const FOLD_LINES = 8;

// Text that shows only its start or its end until it is opened. `content` goes on the
// clamped text and `chip` on the wrapper around its disclosure.
export function useFold<T extends HTMLElement>(text: string, keep: "start" | "end") {
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldLines(text, FOLD_LINES, keep), [text, keep]);
  const whole = useMemo(() => foldLines(text, Infinity, keep).text, [text, keep]);
  const cut = folded.hidden > 0;
  const { content, chip } = useClamp<T>(open || cut);
  return {
    open,
    toggle: () => setOpen(!open),
    shown: open ? whole : folded.text,
    // Known only when lines are cut; otherwise the box clips by height alone.
    total: cut ? folded.total : undefined,
    content,
    chip,
  };
}
