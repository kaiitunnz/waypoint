import { useMemo, useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";

// Text that shows `lines` of itself until it is opened, from its start or its end.
export function useFold(text: string, lines: number, keep: "start" | "end") {
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldLines(text, lines, keep), [text, lines, keep]);
  const whole = useMemo(() => foldLines(text, Infinity, keep).text, [text, keep]);
  return {
    open,
    toggle: () => setOpen(!open),
    shown: open ? whole : folded.text,
    total: folded.total,
    foldable: folded.hidden > 0,
  };
}
