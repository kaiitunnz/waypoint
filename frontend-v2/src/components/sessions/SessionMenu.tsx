"use client";

import type { ReactNode } from "react";

import type { SessionRecord } from "@waypoint/web-core/types";

import { MenuButton, type MenuButtonItem } from "@/components/ui/MenuButton";

export type SessionAction = "pin" | "unpin" | "rename" | "terminate" | "delete";

export interface ScreenAction extends MenuButtonItem {
  run: () => void;
}

interface ActionMenuProps {
  label: string;
  title: string;
  subtitle: string;
  items: ScreenAction[];
  header?: ReactNode;
}

export function ActionMenu({ label, title, subtitle, items, header }: ActionMenuProps) {
  return (
    <MenuButton
      label={label}
      title={title}
      subtitle={subtitle}
      items={items}
      size="sm"
      header={header}
      onSelect={(id) => items.find((item) => item.id === id)?.run()}
    />
  );
}

interface SessionMenuProps {
  session: SessionRecord;
  // The agent and model, under the title in the touch sheet.
  subtitle: string;
  onAction: (action: SessionAction, session: SessionRecord) => void;
  header?: ReactNode;
  // The screen's own actions, listed before the destructive one.
  extra?: ScreenAction[];
}

export function SessionMenu({ session, subtitle, onAction, header, extra = [] }: SessionMenuProps) {
  const act = (action: SessionAction): ScreenAction["run"] => () => onAction(action, session);
  const pin: SessionAction = session.pinned_at ? "unpin" : "pin";
  const end: ScreenAction =
    session.status === "exited"
      ? { id: "delete", label: "Delete", tone: "danger", run: act("delete") }
      : { id: "terminate", label: "Terminate", tone: "danger", run: act("terminate") };
  return (
    <ActionMenu
      label={`Actions for ${session.title}`}
      title={session.title}
      subtitle={subtitle}
      header={header}
      items={[
        { id: pin, label: session.pinned_at ? "Unpin" : "Pin", run: act(pin) },
        { id: "rename", label: "Rename", run: act("rename") },
        ...extra,
        end,
      ]}
    />
  );
}
