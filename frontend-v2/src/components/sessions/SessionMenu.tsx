"use client";

import type { ReactNode } from "react";

import type { SessionRecord } from "@waypoint/web-core/types";

import { MenuButton, type MenuButtonItem } from "@/components/ui/MenuButton";

export type SessionAction = "pin" | "unpin" | "rename" | "terminate" | "delete";

export interface ScreenAction extends MenuButtonItem {
  run: () => void;
}

interface SessionMenuProps {
  session: SessionRecord;
  // The agent and model, under the title in the touch sheet.
  subtitle: string;
  onAction: (action: SessionAction, session: SessionRecord) => void;
  header?: ReactNode;
  // The screen's own actions, listed before the destructive one.
  extra?: ScreenAction[];
  // A menu for one part of the screen lists only its own actions, under its name.
  scope?: { label: string; title: string };
}

export function SessionMenu({ session, subtitle, onAction, header, extra = [], scope }: SessionMenuProps) {
  const items: MenuButtonItem[] = scope
    ? extra
    : [
        { id: session.pinned_at ? "unpin" : "pin", label: session.pinned_at ? "Unpin" : "Pin" },
        { id: "rename", label: "Rename" },
        ...extra,
        session.status === "exited"
          ? { id: "delete", label: "Delete", tone: "danger" }
          : { id: "terminate", label: "Terminate", tone: "danger" },
      ];
  return (
    <MenuButton
      label={scope?.label ?? `Actions for ${session.title}`}
      title={scope?.title ?? session.title}
      subtitle={subtitle}
      items={items}
      size="sm"
      header={header}
      onSelect={(id) => {
        const own = extra.find((item) => item.id === id);
        if (own) own.run();
        else onAction(id as SessionAction, session);
      }}
    />
  );
}
