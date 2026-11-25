"use client";

import type { ReactNode } from "react";

import { List } from "@/components/ui/List";
import { SectionHeading } from "@/components/ui/SectionHeading";

import styles from "./SessionsHome.module.css";
import type { ExitedPaging } from "./useExitedSessions";

interface SearchResultsProps {
  total: number;
  rows: ReactNode[];
  paging: ExitedPaging;
  desktop: boolean;
}

export function SearchResults({ total, rows, paging, desktop }: SearchResultsProps) {
  const { loading, failed, onRetry, hasMore, onMore } = paging;
  return (
    <section>
      <SectionHeading title="Results" count={total} dense={!desktop} />
      {rows.length > 0 ? (
        <div className={styles.inset}>
          <List>{rows}</List>
        </div>
      ) : loading ? (
        <p className={styles.status}>Searching older sessions…</p>
      ) : failed ? null : (
        <p className={styles.status}>No sessions match.</p>
      )}
      {failed ? (
        <p className={styles.status}>
          Older sessions failed to load.{" "}
          <button type="button" className={styles.retry} onClick={onRetry}>
            Retry
          </button>
        </p>
      ) : null}
      {hasMore ? (
        <div className={styles.more}>
          <button type="button" className={styles.retry} onClick={onMore}>
            Show more
          </button>
        </div>
      ) : null}
    </section>
  );
}
