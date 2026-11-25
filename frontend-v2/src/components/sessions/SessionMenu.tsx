"use client";

import type { ReactNode } from "react";

import type { SessionRecord } from "@waypoint/web-core/types";

import { MenuButton, type MenuButtonItem } from "@/components/ui/MenuButton";

export type SessionAction = "pin" | "unpin" | "rename" | "terminate" | "delete";

interface SessionMenuProps {
  session: SessionRecord;
  // The agent and model, under the title in the touch sheet.
  subtitle: string;
  onAction: (action: SessionAction, session: SessionRecord) => void;
  header?: ReactNode;
  // Actions of the screen the menu is on, listed before the destructive one.
  extra?: MenuButtonItem[];
  onExtra?: (id: string) => void;
}

export function SessionMenu({ session, subtitle, onAction, header, extra = [], onExtra }: SessionMenuProps) {
  const items: MenuButtonItem[] = [
    { id: session.pinned_at ? "unpin" : "pin", label: session.pinned_at ? "Unpin" : "Pin" },
    { id: "rename", label: "Rename" },
    ...extra,
    session.status === "exited"
      ? { id: "delete", label: "Delete", tone: "danger" }
      : { id: "terminate", label: "Terminate", tone: "danger" },
  ];
  return (
    <MenuButton
      label={`Actions for ${session.title}`}
      title={session.title}
      subtitle={subtitle}
      items={items}
      size="sm"
      header={header}
      onSelect={(id) => {
        if (extra.some((item) => item.id === id)) onExtra?.(id);
        else onAction(id as SessionAction, session);
      }}
    />
  );
}
