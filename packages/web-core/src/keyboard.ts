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

const CONTROL_SELECTORS = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
];

const OUT_OF_TAB_ORDER = ':not([tabindex="-1"])';

const FOCUSABLE_SELECTOR = [...CONTROL_SELECTORS, `[tabindex]${OUT_OF_TAB_ORDER}`].join(", ");

// Only what Tab stops on: a roving tab list or a listbox keeps its other items,
// controls included, out of the order with tabindex="-1".
const TAB_STOP_SELECTOR = [...CONTROL_SELECTORS, "[tabindex]"]
  .map((selector) => `${selector}${OUT_OF_TAB_ORDER}`)
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
  // `tabStopsOnly` cycles over what Tab stops on, and also wraps from focus on
  // anything else in the root (the root itself, a roving item) with no stop
  // beyond it.
  options: { preventWhenEmpty?: boolean; tabStopsOnly?: boolean } = {},
): boolean {
  if (event.key !== "Tab" || !root) {
    return false;
  }
  const focusable = root.querySelectorAll<HTMLElement>(
    options.tabStopsOnly ? TAB_STOP_SELECTOR : FOCUSABLE_SELECTOR,
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
  const ahead = event.shiftKey ? Node.DOCUMENT_POSITION_PRECEDING : Node.DOCUMENT_POSITION_FOLLOWING;
  // Tab from here would leave the root.
  const escaping =
    !root.contains(active) ||
    (options.tabStopsOnly === true &&
      active !== null &&
      !Array.from(focusable).some((node) => node === active || active.compareDocumentPosition(node) & ahead));
  if (event.shiftKey && (active === first || escaping)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || escaping)) {
    event.preventDefault();
    first.focus();
  }
  return true;
}
