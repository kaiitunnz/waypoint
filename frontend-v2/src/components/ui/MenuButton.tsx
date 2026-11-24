"use client";

import { useRef, useState, type ReactNode } from "react";

import { useDesktop } from "@/lib/layout";

import { Sheet } from "./Dialog";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { Menu, MenuItem, Popover } from "./Popover";

export interface MenuButtonItem {
  id: string;
  label: string;
  tone?: "default" | "danger";
}

interface MenuButtonProps {
  // The spoken name of the button.
  label: string;
  // The heading of the mobile sheet.
  title: string;
  subtitle?: ReactNode;
  items: MenuButtonItem[];
  onSelect: (id: string) => void;
  size?: "md" | "sm";
}

// A "more" button whose actions live in a menu: a popover on desktop, a sheet on
// mobile. Destructive actions are items here, never inline buttons.
export function MenuButton({ label, title, subtitle, items, onSelect, size = "md" }: MenuButtonProps) {
  const desktop = useDesktop();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  const menu = (
    <Menu label={label}>
      {items.map((item) => (
        <MenuItem
          key={item.id}
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
  );

  return (
    <>
      <IconButton
        ref={trigger}
        label={label}
        size={size}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="more" size={20} />
      </IconButton>
      {desktop ? (
        <Popover open={open} onOpenChange={setOpen} triggerRef={trigger} label={label} width={220} align="right">
          {menu}
        </Popover>
      ) : (
        <Sheet open={open} onClose={() => setOpen(false)} title={title} subtitle={subtitle} flush>
          {menu}
        </Sheet>
      )}
    </>
  );
}
