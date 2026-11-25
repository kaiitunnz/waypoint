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
  size?: "md" | "sm";
  header?: ReactNode;
}

export function SessionMenu({ session, subtitle, onAction, size, header }: SessionMenuProps) {
  const items: MenuButtonItem[] = [
    { id: session.pinned_at ? "unpin" : "pin", label: session.pinned_at ? "Unpin" : "Pin" },
    { id: "rename", label: "Rename" },
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
      size={size}
      header={header}
      onSelect={(id) => onAction(id as SessionAction, session)}
    />
  );
}
