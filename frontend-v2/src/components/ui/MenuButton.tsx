"use client";

import { useRef, useState, type ReactNode } from "react";

import { Flyout } from "./Flyout";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { Menu, MenuItem } from "./Popover";

export interface MenuButtonItem {
  id: string;
  label: string;
  tone?: "default" | "danger";
  disabled?: boolean;
}

interface MenuButtonProps {
  // The spoken name of the button.
  label: string;
  // The heading of the touch sheet.
  title: string;
  subtitle?: ReactNode;
  items: MenuButtonItem[];
  onSelect: (id: string) => void;
  size?: "md" | "sm";
  header?: ReactNode;
  // Held by the caller, to open the menu from elsewhere too; left out, the button holds it.
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

// Destructive actions are menu items, never inline buttons.
export function MenuButton({
  label,
  title,
  subtitle,
  items,
  onSelect,
  size = "md",
  header,
  open: held,
  onOpenChange,
}: MenuButtonProps) {
  const [own, setOwn] = useState(false);
  const open = held ?? own;
  const setOpen = (next: boolean) => {
    setOwn(next);
    onOpenChange?.(next);
  };
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    <>
      <IconButton
        ref={trigger}
        label={label}
        size={size}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="more" size={20} />
      </IconButton>
      <Flyout
        open={open}
        onClose={() => setOpen(false)}
        triggerRef={trigger}
        title={title}
        label={label}
        subtitle={subtitle}
        width={220}
        align="right"
        flush
      >
        {header}
        <Menu label={label}>
          {items.map((item) => (
            <MenuItem
              key={item.id}
              disabled={item.disabled}
              tone={item.tone}
              onSelect={() => {
                setOpen(false);
                onSelect(item.id);
              }}
            >
              {item.label}
            </MenuItem>
          ))}
        </Menu>
      </Flyout>
    </>
  );
}
