"use client";

import { Icon } from "@/components/ui/Icon";
import { SegmentedControl } from "@/components/ui/SegmentedControl";

export type SessionView = "chat" | "terminal";

const LABELS: Record<SessionView, string> = { chat: "Chat", terminal: "Terminal" };

interface ViewSwitchProps {
  value: SessionView;
  onChange: (view: SessionView) => void;
  variant: "icon" | "text";
  // A transport that is only a terminal has no chat to switch to.
  terminalOnly?: boolean;
}

export function ViewSwitch({ value, onChange, variant, terminalOnly }: ViewSwitchProps) {
  const views: SessionView[] = terminalOnly ? ["terminal"] : ["chat", "terminal"];
  const text = variant === "text";
  const segments = views.map((view) => ({
    value: view,
    label: text ? LABELS[view] : <Icon name={view} size={18} />,
    ariaLabel: text ? undefined : LABELS[view],
  }));
  return <SegmentedControl label="View" segments={segments} value={value} onChange={onChange} size={text ? "sm" : "icon"} />;
}
