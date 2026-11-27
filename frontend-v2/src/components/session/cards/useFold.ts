import { useMemo, useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";

import { useClamp } from "./useClamp";

// How much of a folded block shows. A line takes at least one visual line, so cutting
// to this many first keeps long text out of the page until it is opened.
export const FOLD_LINES = 8;

// Text that shows only its start until it is opened. `content` goes on the
// element just inside the clamped box, and `chip` on the wrapper around its disclosure.
export function useFold<T extends HTMLElement>(text: string) {
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldLines(text, FOLD_LINES), [text]);
  const whole = useMemo(() => foldLines(text, Infinity).text, [text]);
  const cut = folded.hidden > 0;
  const { content, chip } = useClamp<T>(open || cut);
  return {
    open,
    toggle: () => setOpen(!open),
    shown: open ? whole : folded.text,
    total: cut ? folded.total : undefined,
    content,
    chip,
  };
}
