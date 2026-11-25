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

// Selects a panel's toggle, to send focus back to it.
export const panelToggle = (panel: SidePanel) => `[data-panel-toggle="${panel}"]`;

interface PanelButtonProps {
  panel: SidePanel;
  control: PanelControl;
  size?: "md" | "sm";
}

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
