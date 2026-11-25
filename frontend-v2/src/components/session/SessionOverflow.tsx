"use client";

import type { SessionRecord } from "@waypoint/web-core/types";

import { MenuButton, type MenuButtonItem } from "@/components/ui/MenuButton";
import { useDesktop } from "@/lib/layout";

import { DensityToggle, type Density } from "./DensityToggle";
import type { SessionAction } from "@/components/sessions/SessionMenu";

interface SessionOverflowProps {
  session: SessionRecord;
  subtitle: string;
  density: Density;
  onDensity: (density: Density) => void;
  onAction: (action: SessionAction, session: SessionRecord) => void;
}

// The session's actions. On mobile it also carries the transcript density, which
// the desktop header shows as its own toggle.
export function SessionOverflow({ session, subtitle, density, onDensity, onAction }: SessionOverflowProps) {
  const desktop = useDesktop();
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
      size="sm"
      header={desktop ? null : <DensityToggle variant="segmented" value={density} onChange={onDensity} />}
      onSelect={(id) => onAction(id as SessionAction, session)}
    />
  );
}
