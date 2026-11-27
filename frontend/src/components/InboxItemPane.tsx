"use client";

import { useCallback } from "react";

import { InboxItemView } from "@/components/InboxItemView";
import { useInboxItem } from "@waypoint/web-core/hooks/useInboxItem";

// Smart wrapper for a single inbox item: hydrates it, marks it read, keeps it
// live over /ws/inbox/{id}, and owns delete. Shared by the desktop split-view
// right pane and the mobile full-page route.
export function InboxItemPane({
  host,
  token,
  itemId,
  onAuthFailure,
  onDeleted,
}: {
  host: string;
  token: string;
  itemId: string;
  onAuthFailure: () => void;
  onDeleted: () => void;
}) {
  const { item, gone, error, remove } = useInboxItem({ host, token, itemId, onAuthFailure });

  const handleDelete = useCallback(() => {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this inbox item? This cannot be undone.")
    ) {
      return;
    }
    void remove().then((deleted) => {
      if (deleted) onDeleted();
    });
  }, [remove, onDeleted]);

  if (gone) {
    return <div className="inbox-empty">This item was deleted.</div>;
  }
  if (error) {
    return <div className="inbox-empty">{error}</div>;
  }
  if (!item) {
    return <div className="inbox-empty">Loading…</div>;
  }
  return (
    <InboxItemView
      host={host}
      token={token}
      item={item}
      onDelete={handleDelete}
    />
  );
}
