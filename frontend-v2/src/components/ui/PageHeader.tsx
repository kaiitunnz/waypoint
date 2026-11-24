import Link from "next/link";
import type { ReactNode } from "react";

import { Icon } from "./Icon";

import styles from "./PageHeader.module.css";

interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  // Sits right after the title (the host chip).
  titleExtra?: ReactNode;
  actions?: ReactNode;
  // A way out on mobile screens that have no tab bar; desktop has the rail.
  back?: { href: string; label: string };
}

// The in-flow header of a top-level screen: the page title in serif, padded
// below the status bar and Dynamic Island.
export function PageHeader({ title, subtitle, titleExtra, actions, back }: PageHeaderProps) {
  return (
    <header className={styles.header}>
      {back ? (
        <Link href={back.href} className={styles.back} aria-label={`Back to ${back.label}`}>
          <Icon name="chevron-left" size={22} />
        </Link>
      ) : null}
      <div className={styles.titles}>
        <h1 className={styles.title}>{title}</h1>
        {subtitle ? <div className={styles.subtitle}>{subtitle}</div> : null}
      </div>
      {titleExtra}
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  );
}
