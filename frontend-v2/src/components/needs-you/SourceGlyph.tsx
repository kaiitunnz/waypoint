import { Icon, type IconName } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./SourceGlyph.module.css";

interface SourceGlyphProps {
  icon: IconName;
  // Nothing left for the human to do here.
  done?: boolean;
  label?: string;
}

// A source's glyph in place of a lamp: brass while it waits on the human.
export function SourceGlyph({ icon, done, label }: SourceGlyphProps) {
  return (
    <span
      className={cx(styles.glyph, done && styles.done)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <Icon name={icon} size={13} strokeWidth={2} />
    </span>
  );
}
