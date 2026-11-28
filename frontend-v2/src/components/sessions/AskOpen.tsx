import Link from "next/link";
import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";

import styles from "./Ask.module.css";

export function OpenHeadline({ href, children }: { href: string; children: ReactNode }) {
  return (
    <h3 className={styles.headline}>
      <Link href={href} className={styles.open}>
        <span>{children}</span>
        <Icon name="chevron-right" size={16} strokeWidth={2} />
      </Link>
    </h3>
  );
}

// `replace` adds no history entry and keeps the scroll position.
export function OpenLink({ href, label, replace }: { href: string; label: string; replace?: boolean }) {
  return (
    <Link href={href} replace={replace} scroll={!replace} className={styles.go}>
      {label}
      <Icon name="chevron-right" size={15} strokeWidth={2} />
    </Link>
  );
}
