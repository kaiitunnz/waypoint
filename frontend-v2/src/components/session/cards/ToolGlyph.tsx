import type { ToolFamily } from "@waypoint/web-core/session/toolRun";
import type { HTMLAttributes } from "react";

import { cx } from "@/lib/cx";

import styles from "./ToolGlyph.module.css";

interface ToolGlyphProps extends HTMLAttributes<HTMLSpanElement> {
  glyph: string;
  family: ToolFamily | null;
}

// A tool's glyph, in its family's colour while the row or card holding it is lit.
export function ToolGlyph({ glyph, family, className, ...props }: ToolGlyphProps) {
  return (
    <span {...props} className={cx(styles.glyph, className)} data-family={family ?? undefined}>
      {glyph}
    </span>
  );
}
