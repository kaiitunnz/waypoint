import type { ToolFamily } from "@waypoint/web-core/session/toolFamily";
import type { HTMLAttributes } from "react";

import { cx } from "@/lib/cx";

import styles from "./ToolGlyph.module.css";

interface ToolGlyphProps extends HTMLAttributes<HTMLSpanElement> {
  glyph: string;
  family: ToolFamily | null;
}

export function ToolGlyph({ glyph, family, className, ...props }: ToolGlyphProps) {
  return (
    <span {...props} className={cx(styles.glyph, className)} data-family={family ?? undefined}>
      {glyph}
    </span>
  );
}
