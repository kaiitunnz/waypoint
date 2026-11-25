"use client";

import { useRef, useState, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";

import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { useDialog } from "./useDialog";
import styles from "./Dialog.module.css";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  // No padding around the body, for content that brings its own (lists, pickers).
  flush?: boolean;
  // Modal only.
  size?: "md" | "lg";
}

// Opaque with a scrim: dialogs carry text, so they are never glass.
function Surface({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  flush,
  size = "md",
  variant,
}: DialogProps & { variant: "sheet" | "modal" }) {
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
    setDrag(0);
    if (drag > 90) onClose();
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
        className={isSheet ? styles.sheet : cx(styles.modal, size === "lg" && styles.modalLg)}
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
          <div className={styles.heading}>
            <h2 className={styles.title}>{title}</h2>
            {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
          </div>
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

export function Sheet(props: DialogProps) {
  return <Surface {...props} variant="sheet" />;
}

export function Modal(props: DialogProps) {
  return <Surface {...props} variant="modal" />;
}

// A modal on desktop, a sheet on mobile.
export function Dialog(props: DialogProps) {
  const desktop = useDesktop();
  return desktop ? <Modal {...props} /> : <Sheet {...props} />;
}
