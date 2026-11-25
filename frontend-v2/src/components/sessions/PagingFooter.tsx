"use client";

import { ActionChip } from "@/components/ui/ActionChip";

import styles from "./PagingFooter.module.css";
import type { ExitedPaging } from "./useExitedSessions";

interface PagingFooterProps {
  paging: ExitedPaging;
  // Shown while the next page loads; a list that reports loading elsewhere leaves it out.
  loadingText?: string;
}

export function PagingFooter({ paging, loadingText }: PagingFooterProps) {
  const { loading, failed, onRetry, hasMore, onMore } = paging;
  return (
    <>
      {failed ? (
        <p className={styles.status} role="status">
          Older sessions failed to load.{" "}
          <ActionChip icon="refresh" onClick={onRetry}>
            Retry
          </ActionChip>
        </p>
      ) : loading && loadingText ? (
        <p className={styles.status} role="status">
          {loadingText}
        </p>
      ) : null}
      {hasMore ? (
        <div className={styles.more}>
          <ActionChip icon="chevron-down" onClick={onMore}>
            Show more
          </ActionChip>
        </div>
      ) : null}
    </>
  );
}
