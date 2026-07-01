"use client";

interface PagerProps {
  page: number;
  totalPages: number;
  total: number;
  pageStart: number;
  pageEnd: number;
  onPage: (page: number) => void;
  label?: string;
}

// Shared list paginator: a range readout plus Prev / page-indicator / Next.
// Renders nothing for a single page. Used by the schedule panel and session
// list so paginated panels read consistently.
export function Pager({
  page,
  totalPages,
  total,
  pageStart,
  pageEnd,
  onPage,
  label = "items",
}: PagerProps) {
  if (totalPages <= 1) {
    return null;
  }
  return (
    <div className="pager">
      <span className="pager-range">
        {pageStart}–{pageEnd} of {total} {label}
      </span>
      <div className="pager-controls">
        <button
          type="button"
          className="pager-btn"
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page === 1}
        >
          ← Prev
        </button>
        <span className="pager-indicator">
          {page} / {totalPages}
        </span>
        <button
          type="button"
          className="pager-btn"
          onClick={() => onPage(Math.min(totalPages, page + 1))}
          disabled={page === totalPages}
        >
          Next →
        </button>
      </div>
    </div>
  );
}
