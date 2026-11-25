import type { KeyboardEvent as ReactKeyboardEvent } from "react";

type KeyboardEventLike = Pick<
  globalThis.KeyboardEvent,
  "ctrlKey" | "key" | "metaKey" | "shiftKey"
> & {
  isComposing?: boolean;
  nativeEvent?: {
    isComposing?: boolean;
  };
};

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// The focusable elements Tab actually stops on: a roving tab list or a listbox
// keeps its other items out of the order with tabindex="-1".
export const TAB_STOP_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]",
]
  .map((selector) => `${selector}:not([tabindex="-1"])`)
  .join(", ");

// A terminal reads Ctrl+K as readline's kill-line and a contenteditable region
// keeps its own editing keys; a page-wide shortcut leaves keys from either alone.
const KEY_OWNER_SELECTOR = '.xterm, [contenteditable]:not([contenteditable="false"])';

export function ownsShortcutKeys(target: EventTarget | null): boolean {
  const element = target as { closest?: (selector: string) => unknown } | null;
  return typeof element?.closest === "function" && element.closest(KEY_OWNER_SELECTOR) !== null;
}

export function isComposingKeyEvent(event: KeyboardEventLike): boolean {
  return event.isComposing === true || event.nativeEvent?.isComposing === true;
}

export function isSwitcherShortcut(
  event: KeyboardEventLike & Pick<globalThis.KeyboardEvent, "altKey">,
): boolean {
  return (
    !isComposingKeyEvent(event) &&
    (event.metaKey || event.ctrlKey) &&
    !event.shiftKey &&
    !event.altKey &&
    event.key.toLowerCase() === "k"
  );
}

export function isModifiedEnterShortcut(
  event: ReactKeyboardEvent<HTMLTextAreaElement>,
): boolean {
  if (isComposingKeyEvent(event)) {
    return false;
  }
  return event.key === "Enter" && !event.shiftKey && (event.metaKey || event.ctrlKey);
}

export function isUnmodifiedEnterKey(event: KeyboardEventLike): boolean {
  return (
    !isComposingKeyEvent(event) &&
    event.key === "Enter" &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey
  );
}

export function isInlineMenuAcceptKey(event: KeyboardEventLike): boolean {
  return event.key === "Tab" || isUnmodifiedEnterKey(event);
}

export function trapTabFocus(
  event: globalThis.KeyboardEvent,
  root: HTMLElement | null,
  // `fromNonStops` also wraps from a focused element of the root that is not in
  // the list (the root itself, a roving item) when no element of the list lies
  // beyond it, instead of letting Tab leave the root.
  options: { fromNonStops?: boolean; preventWhenEmpty?: boolean; selector?: string } = {},
): boolean {
  if (event.key !== "Tab" || !root) {
    return false;
  }
  const focusable = root.querySelectorAll<HTMLElement>(
    options.selector ?? FOCUSABLE_SELECTOR,
  );
  if (focusable.length === 0) {
    if (options.preventWhenEmpty) {
      event.preventDefault();
      return true;
    }
    return false;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  const outside = !root.contains(active);
  const ahead = event.shiftKey ? Node.DOCUMENT_POSITION_PRECEDING : Node.DOCUMENT_POSITION_FOLLOWING;
  const stranded =
    Boolean(options.fromNonStops) &&
    active !== null &&
    !outside &&
    !Array.from(focusable).some((node) => node === active || active.compareDocumentPosition(node) & ahead);
  if (event.shiftKey && (active === first || stranded || outside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || stranded || outside)) {
    event.preventDefault();
    first.focus();
  }
  return true;
}
