import Link from "next/link";

import { Icon } from "@/components/ui/Icon";

import styles from "./Ask.module.css";

// A card headline that opens the ask's detail.
export function OpenHeadline({ href, children }: { href: string; children: string }) {
  return (
    <h3 className={styles.headline}>
      <Link href={href} className={styles.open}>
        <span>{children}</span>
        <Icon name="chevron-right" size={16} strokeWidth={2} />
      </Link>
    </h3>
  );
}

// `replace` for a link beside the open item, which keeps the history and scroll.
export function OpenLink({ href, label, replace }: { href: string; label: string; replace?: boolean }) {
  return (
    <Link href={href} replace={replace} scroll={!replace} className={styles.go}>
      {label}
      <Icon name="chevron-right" size={15} strokeWidth={2} />
    </Link>
  );
}
