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

export function isComposingKeyEvent(event: KeyboardEventLike): boolean {
  return event.isComposing === true || event.nativeEvent?.isComposing === true;
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
  options: { preventWhenEmpty?: boolean; selector?: string } = {},
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
  if (event.shiftKey && (active === first || !root.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !root.contains(active))) {
    event.preventDefault();
    first.focus();
  }
  return true;
}
