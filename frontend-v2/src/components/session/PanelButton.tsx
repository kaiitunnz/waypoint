import { Icon, type IconName } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

export type SidePanel = "rail" | "pane";

export interface PanelControl {
  open: boolean;
  toggle: () => void;
}

const PANELS: Record<SidePanel, { label: string; icon: IconName }> = {
  rail: { label: "Sessions", icon: "panel-left" },
  pane: { label: "Side pane", icon: "panel-right" },
};

// The header's button for a panel, where focus can be sent back to it.
export const panelToggle = (panel: SidePanel) => `[data-panel-toggle="${panel}"]`;

interface PanelButtonProps {
  panel: SidePanel;
  control: PanelControl;
  size?: "md" | "sm";
}

// Shows or hides a side panel, from the session header and from the drawer itself.
export function PanelButton({ panel, control, size }: PanelButtonProps) {
  const { label, icon } = PANELS[panel];
  return (
    <IconButton
      label={label}
      size={size}
      aria-pressed={control.open}
      data-panel-toggle={panel}
      onClick={(event) => {
        // Safari does not focus a clicked button, and a drawer returns focus to what held it.
        event.currentTarget.focus();
        control.toggle();
      }}
    >
      <Icon name={icon} size={size === "sm" ? 18 : 20} />
    </IconButton>
  );
}
