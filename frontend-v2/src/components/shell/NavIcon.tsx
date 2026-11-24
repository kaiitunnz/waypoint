import { Icon } from "@/components/ui/Icon";
import type { NavKey } from "@waypoint/web-core/shellNav";

import styles from "./NavIcon.module.css";

// The assistant is the asterism glyph; the rest are drawn icons.
export function NavIcon({ navKey, size = 22 }: { navKey: NavKey; size?: number }) {
  switch (navKey) {
    case "sessions":
      return <Icon name="sessions" size={size} strokeWidth={1.7} />;
    case "needs-you":
      return <Icon name="needs-you" size={size} strokeWidth={1.7} />;
    case "board":
      return <Icon name="board" size={size} strokeWidth={1.7} />;
    case "usage":
      return <Icon name="usage" size={size} strokeWidth={1.7} />;
    case "assistant":
      return (
        <span className={styles.glyph} style={{ fontSize: size - 2 }} aria-hidden="true">
          ⁂
        </span>
      );
  }
}
