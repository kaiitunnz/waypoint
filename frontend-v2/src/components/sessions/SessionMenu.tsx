"use client";

import type { ReactNode } from "react";

import { sessionEndAction } from "@waypoint/web-core/session/inspectorActions";
import type { SessionRecord } from "@waypoint/web-core/types";

import { MenuButton, type MenuButtonItem, type MenuButtonProps } from "@/components/ui/MenuButton";

export type SessionAction = "pin" | "unpin" | "rename" | "terminate" | "delete";

export interface ScreenAction extends MenuButtonItem {
  run: () => void;
}

type HeldOpen = Pick<MenuButtonProps, "open" | "onOpenChange">;

interface ActionMenuProps extends HeldOpen {
  label: string;
  title: string;
  subtitle: string;
  items: ScreenAction[];
  header?: ReactNode;
}

export function ActionMenu({ label, title, subtitle, items, header, open, onOpenChange }: ActionMenuProps) {
  return (
    <MenuButton
      label={label}
      title={title}
      subtitle={subtitle}
      items={items}
      size="sm"
      header={header}
      open={open}
      onOpenChange={onOpenChange}
      onSelect={(id) => items.find((item) => item.id === id)?.run()}
    />
  );
}

interface SessionMenuProps extends HeldOpen {
  session: SessionRecord;
  // The agent and model, under the title in the touch sheet.
  subtitle: string;
  onAction: (action: SessionAction, session: SessionRecord) => void;
  header?: ReactNode;
  // The screen's own actions, listed before the destructive one.
  extra?: ScreenAction[];
}

export function SessionMenu({ session, subtitle, onAction, header, extra = [], open, onOpenChange }: SessionMenuProps) {
  const act = (action: SessionAction): ScreenAction["run"] => () => onAction(action, session);
  const pin: SessionAction = session.pinned_at ? "unpin" : "pin";
  const end: ScreenAction =
    sessionEndAction(session) === "delete"
      ? { id: "delete", label: "Delete", tone: "danger", run: act("delete") }
      : { id: "terminate", label: "Terminate", tone: "danger", run: act("terminate") };
  return (
    <ActionMenu
      label={`Actions for ${session.title}`}
      title={session.title}
      subtitle={subtitle}
      header={header}
      open={open}
      onOpenChange={onOpenChange}
      items={[
        { id: pin, label: session.pinned_at ? "Unpin" : "Pin", run: act(pin) },
        { id: "rename", label: "Rename", run: act("rename") },
        ...extra,
        end,
      ]}
    />
  );
}
