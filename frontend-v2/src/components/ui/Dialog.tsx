"use client";

import { useRef, useState, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { useDialog } from "./useDialog";
import styles from "./Dialog.module.css";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  // A row of actions pinned under the scrolling body.
  footer?: ReactNode;
  // No padding around the body, for content that brings its own (lists, pickers).
  flush?: boolean;
  className?: string;
}

// The only place a sheet's text sits: opaque, with a scrim, because dialogs carry text.
function Surface({
  open,
  onClose,
  title,
  children,
  footer,
  flush,
  className,
  variant,
  size,
}: DialogProps & { variant: "sheet" | "modal"; size?: "sm" | "md" | "lg" }) {
  const panel = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState(0);
  const dragStart = useRef<number | null>(null);
  useDialog(open, onClose, panel);

  if (!open || typeof document === "undefined") {
    return null;
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    dragStart.current = event.clientY;
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (dragStart.current === null) return;
    setDrag(Math.max(0, event.clientY - dragStart.current));
  }
  function onPointerUp() {
    if (dragStart.current === null) return;
    dragStart.current = null;
    if (drag > 90) {
      setDrag(0);
      onClose();
      return;
    }
    setDrag(0);
  }

  const isSheet = variant === "sheet";
  return createPortal(
    <div className={cx(styles.root, isSheet ? styles.rootSheet : styles.rootModal)}>
      <div className={styles.scrim} onMouseDown={onClose} aria-hidden="true" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cx(isSheet ? styles.sheet : cx(styles.modal, styles[size ?? "md"]), className)}
        style={drag ? { transform: `translateY(${drag}px)`, transition: "none" } : undefined}
      >
        {isSheet ? (
          <div
            className={styles.handle}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            <span className={styles.grabber} />
          </div>
        ) : null}
        <div className={styles.head}>
          <h2 className={styles.title}>{title}</h2>
          <IconButton label="Close" size="sm" onClick={onClose}>
            <Icon name="close" size={18} />
          </IconButton>
        </div>
        <div className={cx(styles.body, flush && styles.flush)}>{children}</div>
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

// Mobile bottom sheet with a grabber (drag it down to dismiss).
export function Sheet(props: DialogProps) {
  return <Surface {...props} variant="sheet" />;
}

// Desktop centred dialog.
export function Modal(props: DialogProps & { size?: "sm" | "md" | "lg" }) {
  return <Surface {...props} variant="modal" />;
}
