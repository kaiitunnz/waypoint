import type { NavKey } from "@waypoint/web-core/shellNav";

import { Icon } from "@/components/ui/Icon";

import styles from "./NavIcon.module.css";

export function NavIcon({ navKey }: { navKey: NavKey }) {
  if (navKey === "assistant") {
    return (
      <span className={styles.glyph} aria-hidden="true">
        ⁂
      </span>
    );
  }
  return <Icon name={navKey} size={22} strokeWidth={1.7} />;
}
