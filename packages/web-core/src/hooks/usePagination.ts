"use client";

import { useEffect, useState } from "react";

// Client-side pagination over an in-memory list. Clamps the current page when
// the list shrinks (e.g. a scheduled message fires and leaves the list) so the
// view never strands on an empty page.
export function usePagination<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(1);
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages]);

  const clampedPage = Math.min(page, totalPages);
  const start = (clampedPage - 1) * pageSize;
  const pageItems = items.slice(start, start + pageSize);

  return {
    page: clampedPage,
    setPage,
    totalPages,
    total,
    pageItems,
    pageStart: total ? start + 1 : 0,
    pageEnd: Math.min(total, start + pageSize),
  };
}
