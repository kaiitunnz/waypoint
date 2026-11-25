"use client";

import type { ReactNode } from "react";

import { List } from "@/components/ui/List";
import { SectionHeading } from "@/components/ui/SectionHeading";

import { PagingFooter } from "./PagingFooter";
import styles from "./SessionsHome.module.css";
import type { ExitedPaging } from "./useExitedSessions";

interface SearchResultsProps {
  total: number;
  rows: ReactNode[];
  paging: ExitedPaging;
  phone: boolean;
}

export function SearchResults({ total, rows, paging, phone }: SearchResultsProps) {
  const { loading, failed } = paging;
  return (
    <section>
      <SectionHeading title="Results" count={total} dense={phone} />
      {rows.length > 0 ? (
        <div className={styles.inset}>
          <List>{rows}</List>
        </div>
      ) : loading ? (
        <p className={styles.status}>Searching older sessions…</p>
      ) : failed ? null : (
        <p className={styles.status}>No sessions match.</p>
      )}
      <PagingFooter paging={paging} />
    </section>
  );
}
