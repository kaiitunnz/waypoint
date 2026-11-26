"use client";

import { useRef, useState } from "react";
import { flushSync } from "react-dom";

import { Flyout } from "@/components/ui/Flyout";
import { Icon, type IconName } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { Menu, MenuItem } from "@/components/ui/Popover";
import { usePointerUI } from "@/lib/layout";

import styles from "./Composer.module.css";

export interface PlusAction {
  id: string;
  label: string;
  icon: IconName;
  disabled?: boolean;
  // Runs inside the tap, once the menu has closed, so it can open the file
  // picker or the keyboard.
  run: () => void;
}

export function PlusMenu({ actions }: { actions: PlusAction[] }) {
  const pointer = usePointerUI();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    <>
      <IconButton
        ref={trigger}
        label="Add to message"
        size={pointer ? "sm" : "md"}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="plus" size={pointer ? 18 : 22} />
      </IconButton>
      <Flyout
        open={open}
        onClose={() => setOpen(false)}
        triggerRef={trigger}
        title="Add to message"
        width={230}
        side="top"
        flush
      >
        <Menu label="Add to message">
          {actions.map((action) => (
            <MenuItem
              key={action.id}
              disabled={action.disabled}
              onSelect={() => {
                // Closed first, so the closing sheet cannot take the focus back.
                flushSync(() => setOpen(false));
                // The popover took the focus with it.
                if (pointer) trigger.current?.focus();
                action.run();
              }}
            >
              <span className={styles.menuIcon}>
                <Icon name={action.icon} size={16} />
              </span>
              {action.label}
            </MenuItem>
          ))}
        </Menu>
      </Flyout>
    </>
  );
}
